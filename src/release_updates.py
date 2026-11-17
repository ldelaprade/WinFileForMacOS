import json
import platform
from urllib.parse import quote

from packaging.version import InvalidVersion, Version
from PySide6.QtCore import QObject, QSettings, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QCheckBox, QMessageBox

from .version import APP_VERSION


REPOSITORY = "ldelaprade/WinFileForMacOS"
POLICY_URL = f"https://raw.githubusercontent.com/{REPOSITORY}/HEAD/updates.json"


def release_tag(policy):
    if not isinstance(policy, dict) or policy.get("enabled") is not True:
        return None
    tag = policy.get("tag")
    if not isinstance(tag, str) or not tag or len(tag) > 128:
        return None
    return tag


def available_release(release, tag, current_version, system, machine):
    if not isinstance(release, dict) or release.get("tag_name") != tag:
        return None
    if release.get("draft") is not False or release.get("prerelease") is not False:
        return None
    try:
        version = Version(tag.removeprefix("v"))
        if version.is_prerelease or version.is_devrelease or version <= Version(current_version):
            return None
    except InvalidVersion:
        return None
    target = {"Windows": "win", "Darwin": "osx", "Linux": "linux"}.get(system)
    architecture = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine.lower())
    if not target or not architecture:
        return None
    suffix = {"Windows": "Setup.exe", "Darwin": ".pkg", "Linux": ".AppImage"}[system]
    assets = release.get("assets")
    if not isinstance(assets, list):
        return None
    runtime = f"-{target}-{architecture}"
    if not any(
        isinstance(asset, dict) and isinstance(asset.get("name"), str)
        and any(marker in asset["name"] for marker in (runtime + "-", runtime + "."))
        and asset["name"].endswith(suffix)
        for asset in assets
    ):
        return None
    return str(version), f"https://github.com/{REPOSITORY}/releases/tag/{quote(tag, safe='')}"


class ReleaseNotifier(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.settings = QSettings("WinFileXP", "WinFileXP")
        self.network = QNetworkAccessManager(self)
        self.tag = None
        self.dialog = None

    def start(self):
        if self.settings.value("updates/check_on_startup", True, type=bool):
            self._get(POLICY_URL, self._policy_received)

    def _get(self, url, callback):
        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"User-Agent", b"RetroFile-Release-Notifier")
        request.setTransferTimeout(8000)
        reply = self.network.get(request)
        reply.finished.connect(lambda: self._finished(reply, callback))

    def _finished(self, reply, callback):
        try:
            if reply.error() != QNetworkReply.NetworkError.NoError:
                return
            data = bytes(reply.read(1024 * 1024 + 1))
            if len(data) > 1024 * 1024:
                return
            callback(json.loads(data))
        except (ValueError, TypeError):
            pass
        finally:
            reply.deleteLater()

    def _policy_received(self, policy):
        self.tag = release_tag(policy)
        if self.tag:
            self._get(
                f"https://api.github.com/repos/{REPOSITORY}/releases/tags/{quote(self.tag, safe='')}",
                self._release_received,
            )

    def _release_received(self, release):
        available = available_release(release, self.tag, APP_VERSION, platform.system(), platform.machine())
        if not available:
            return
        version, url = available
        self.dialog = QMessageBox(self.window)
        self.dialog.setWindowTitle("RetroFile Update")
        self.dialog.setIcon(QMessageBox.Icon.Information)
        self.dialog.setText(f"RetroFile {version} is available.")
        self.dialog.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        self.dialog.button(QMessageBox.StandardButton.Yes).setText("View Release")
        self.dialog.button(QMessageBox.StandardButton.No).setText("Later")
        self.dialog.setDefaultButton(QMessageBox.StandardButton.No)
        checkbox = QCheckBox("Check for updates at startup", self.dialog)
        checkbox.setChecked(True)
        self.dialog.setCheckBox(checkbox)
        self.dialog.finished.connect(lambda result: self._dismissed(result, url))
        self.dialog.open()

    def _dismissed(self, result, url):
        self.settings.setValue("updates/check_on_startup", self.dialog.checkBox().isChecked())
        if result == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl(url))