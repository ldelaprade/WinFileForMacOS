from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree


class SvnOperationError(Exception):
    def __init__(self, message: str, authentication_required: bool = False) -> None:
        super().__init__(message)
        self.authentication_required = authentication_required


@dataclass(frozen=True)
class SvnChange:
    path: str
    status: str


@dataclass(frozen=True)
class SvnChanges:
    directory: str
    items: list[SvnChange]

    def for_action(self, action: str) -> list[SvnChange]:
        if action == "add":
            return [item for item in self.items if item.status == "unversioned"]
        if action == "delete":
            return [
                item for item in self.items
                if item.status not in {"unversioned", "deleted", "external", "ignored"}
            ]
        if action == "commit":
            return [
                item for item in self.items
                if item.status not in {"unversioned", "deleted", "missing", "obstructed", "incomplete", "external", "ignored"}
            ]
        raise SvnOperationError(f"Unsupported SVN selection action: {action}")


class SvnOperations:
    def __init__(self, directory: str, credentials: tuple[str, str] | None = None) -> None:
        self.directory = Path(os.path.abspath(directory))
        self.credentials = credentials

    def checkout(self, url: str, destination: str) -> str:
        try:
            parsed_url = urlsplit(url.strip())
        except ValueError as error:
            raise SvnOperationError("Enter a valid SVN repository URL.") from error
        if parsed_url.scheme not in {"file", "http", "https", "svn", "svn+ssh"}:
            raise SvnOperationError("Enter a valid SVN repository URL.")
        if not parsed_url.path.strip("/"):
            raise SvnOperationError("The SVN repository URL must include a repository path.")
        if parsed_url.username is not None or parsed_url.password is not None:
            raise SvnOperationError("Do not include credentials in the repository URL.")
        target = Path(os.path.abspath(os.path.expanduser(destination)))
        if not target.parent.is_dir():
            raise SvnOperationError("The checkout parent folder does not exist.")
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            raise SvnOperationError("The checkout destination must be a new or empty folder.")
        output = self._execute("checkout", "--", url.strip(), str(target))
        return output or f"Checked out {url.strip()} to {target}."

    def changes(self) -> SvnChanges:
        info = self._execute("info", "--xml", "--", str(self.directory) + "@")
        try:
            root_text = ElementTree.fromstring(info).findtext("entry/wc-info/wcroot-abspath")
        except ElementTree.ParseError as error:
            raise SvnOperationError("Could not read SVN working-copy information.") from error
        if not root_text:
            raise SvnOperationError("This folder is not inside an SVN working copy.")
        status_output = self._execute(
            "status", "--xml", "--depth", "infinity", "--", str(self.directory) + "@"
        )
        try:
            xml_root = ElementTree.fromstring(status_output)
        except ElementTree.ParseError as error:
            raise SvnOperationError("Could not read SVN status information.") from error
        items: list[SvnChange] = []
        for entry in xml_root.findall(".//entry"):
            working_status = entry.find("wc-status")
            if working_status is None:
                continue
            status = working_status.get("item", "normal")
            if status == "normal" and working_status.get("props", "none") != "none":
                status = "property modified"
            if status in {"normal", "ignored", "external"} and status != "normal":
                continue
            target = Path(entry.get("path", ""))
            if not target.is_absolute():
                target = self.directory / target
            target = Path(os.path.abspath(target))
            try:
                relative = target.relative_to(self.directory)
            except ValueError:
                continue
            if any(part.lower() == ".svn" for part in relative.parts):
                continue
            items.append(SvnChange(str(target), status))
        items.sort(key=lambda item: item.path.casefold())
        return SvnChanges(str(self.directory), items)

    def run(self, action: str, paths: list[str] | None = None, message: str = "") -> str:
        if action not in {"status", "diff", "add", "delete", "commit", "update", "checkout"}:
            raise SvnOperationError(f"Unsupported SVN action: {action}")
        if action == "checkout":
            if not paths or len(paths) != 2:
                raise SvnOperationError("Provide a repository URL and checkout destination.")
            return self.checkout(paths[0], paths[1])
        if action in {"add", "delete", "commit"} and not paths:
            raise SvnOperationError("Select files or folders first.")
        if action == "commit" and not message.strip():
            raise SvnOperationError("A commit message is required.")
        info = self._execute("info", "--xml", "--", str(self.directory) + "@")
        try:
            root_text = ElementTree.fromstring(info).findtext("entry/wc-info/wcroot-abspath")
        except ElementTree.ParseError as error:
            raise SvnOperationError("Could not read SVN working-copy information.") from error
        if not root_text:
            raise SvnOperationError("This folder is not inside an SVN working copy.")
        root = Path(root_text)
        targets = []
        for path in paths or []:
            target = Path(os.path.abspath(path))
            try:
                relative = target.relative_to(root)
                target.relative_to(self.directory)
            except ValueError as error:
                raise SvnOperationError("Selected items must belong to this working-copy folder.") from error
            if any(part.lower() == ".svn" for part in relative.parts):
                raise SvnOperationError("SVN metadata cannot be selected.")
            targets.append(str(target) + "@")
        options = {"add": ["--parents"], "delete": ["--force"]}.get(action, [])
        if action == "diff":
            options = ["--revision", "BASE", "--internal-diff"]
        if action == "commit":
            options = ["--message", message.strip()]
        if action in {"status", "update"}:
            targets = []
        targets = targets or [str(self.directory) + "@"]
        if action == "diff":
            targets = [target[:-1] for target in targets]
        output = self._execute(action, *options, "--", *targets)
        return output or {
            "status": "No local changes.",
            "diff": "No local differences.",
        }.get(action, f"SVN {action} completed.")

    def _execute(self, *arguments: str) -> str:
        command = ["svn", arguments[0], "--non-interactive"]
        if self.credentials is not None:
            username, password = self.credentials
            command.extend(["--no-auth-cache", "--username", username, "--password", password])
        command.extend(arguments[1:])
        try:
            result = subprocess.run(
                command,
                cwd=self.directory, stdin=subprocess.DEVNULL, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=120,
            )
        except FileNotFoundError as error:
            raise SvnOperationError("SVN or the working folder was not found. Install svn and check the folder.") from error
        except subprocess.TimeoutExpired as error:
            raise SvnOperationError("The SVN operation timed out after 120 seconds.") from error
        except OSError as error:
            raise SvnOperationError(str(error)) from error
        if result.returncode:
            error_message = result.stderr.strip() or result.stdout.strip() or "SVN operation failed."
            authentication_required = "E215004" in error_message or "authentication failed" in error_message.lower()
            raise SvnOperationError(error_message, authentication_required)
        return "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())