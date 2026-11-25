import os
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import venv
from pathlib import Path
from unittest import mock

from tools import build_release
from tools import publish_release


@unittest.skipIf(shutil.which("make") is None, "Requires make")
class WindowsReleaseBuildTests(unittest.TestCase):
    def test_help_lists_described_targets_once_from_all_makefiles(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            (Path(temporary) / "extra.mk").write_text(
                "build: ## Duplicate build description\n"
                "extra-target: ## Extra target description\n"
                "undocumented:\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["make", "--no-print-directory", "-f", str(root / "Makefile"),
                 "-f", "extra.mk", "HOST_OS=Darwin", f"PYTHON={sys.executable}", "help"],
                cwd=temporary, capture_output=True, text=True, timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "Available targets:")
        self.assertIn("  help                     List available targets and their descriptions", lines)
        self.assertIn("  extra-target             Extra target description", lines)
        self.assertEqual(sum(line.startswith("  build ") for line in lines), 1)
        self.assertNotIn("Duplicate build description", result.stdout)
        self.assertNotIn("undocumented", result.stdout)

    @unittest.skipUnless(os.name == "nt", "Requires Windows")
    def test_windows_prefers_virtual_environment_python(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            venv.EnvBuilder(with_pip=True).create(Path(temporary) / ".venv")
            environment = os.environ.copy()
            environment.pop("PYTHON", None)
            result = subprocess.run(
                ["make", "--dry-run", "--no-print-directory", "-f", str(root / "Makefile"),
                 "HOST_OS=Windows", "OS=Windows_NT", "build-windows"],
                cwd=temporary, env=environment, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(".venv/Scripts/python.exe -m pip", result.stdout)

    def test_windows_build_cleans_pyinstaller_cache(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ["make", "--dry-run", "--no-print-directory", "HOST_OS=Windows",
             "OS=Windows_NT", "PYTHON=python", "build-windows"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--clean", result.stdout)

    def test_velopack_build_uses_directory_output(self):
        root = Path(__file__).resolve().parents[1]
        for host in ("Windows", "Linux"):
            with self.subTest(host=host):
                result = subprocess.run(
                    ["make", "--dry-run", "--no-print-directory", f"HOST_OS={host}",
                     "OS=Windows_NT", "PYTHON=python", f"build-{host.lower()}"],
                    cwd=root, capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--onedir", result.stdout)
                self.assertNotIn("--onefile", result.stdout)

    def test_release_routes_every_host_to_velopack(self):
        root = Path(__file__).resolve().parents[1]
        for host in ("Windows", "Linux", "Darwin"):
            with self.subTest(host=host):
                result = subprocess.run(
                    ["make", "--dry-run", "--no-print-directory", f"HOST_OS={host}",
                     "OS=Windows_NT", "PYTHON=python", "release"],
                    cwd=root, capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("tools/build_release.py", result.stdout)
                self.assertNotIn("ISCC", result.stdout)

    def test_velo_upload_runs_build_then_upload(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            ["make", "--dry-run", "--no-print-directory", "HOST_OS=Darwin",
             "OS=Darwin", "PYTHON=python", "velo-upload"],
            cwd=root, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        build_position = result.stdout.find("tools/build_release.py")
        upload_position = result.stdout.find("tools/build_release.py", build_position + 1)
        self.assertGreater(upload_position, build_position)
        self.assertIn("--upload", result.stdout)

    def test_velo_publish_requires_confirmation(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
        ["make", "--dry-run", "--no-print-directory", "velo-publish"],
        cwd=root, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("tools/publish_release.py --confirm", result.stdout)


class VelopackResolutionTests(unittest.TestCase):
    def test_upload_runtime_matches_packaging_channels(self):
        self.assertEqual(build_release.release_runtime("Windows", "AMD64"), "win-x64")
        self.assertEqual(build_release.release_runtime("Darwin", "arm64"), "osx-arm64")
        self.assertEqual(build_release.release_runtime("Linux", "x86_64"), "linux-x64")

    def test_upload_command_uses_release_tag_and_runtime_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output_dir = root / "dist" / "releases" / "osx-arm64"
            output_dir.mkdir(parents=True)
            with mock.patch.object(build_release, "ROOT", root), mock.patch.object(
                build_release, "release_runtime", return_value="osx-arm64"
            ):
                command, resolved_output = build_release.upload_command(
                    "RetroFile", "3.0.2", "Darwin", "arm64", "/fake/vpk"
                )

        self.assertEqual(resolved_output, output_dir)
        self.assertIn("--tag", command)
        self.assertEqual(command[command.index("--tag") + 1], "v3.0.2")
        self.assertEqual(command[command.index("--channel") + 1], "osx-arm64")

    def test_upload_token_uses_environment_before_ignored_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "GITHUB_TOKEN.txt").write_text("file-token\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"VPK_TOKEN": "env-token"}, clear=True):
                self.assertEqual(build_release.resolve_upload_token(root), "env-token")
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(build_release.resolve_upload_token(root), "file-token")

    def test_missing_cli_installs_pinned_tool_locally(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dotnet = "/fake/dotnet"

            def install_tool(command, cwd, check):
                self.assertEqual(
                    command,
                    [dotnet, "tool", "install", "--tool-path", str(root / ".velopack"),
                     "vpk", "--version", "1.2.161"],
                )
                (root / ".velopack").mkdir()
                (root / ".velopack" / "vpk").touch()

            with mock.patch.object(
                build_release.shutil, "which", side_effect=lambda name: dotnet if name == "dotnet" else None
            ), mock.patch.object(build_release.subprocess, "run", side_effect=install_tool):
                resolved = build_release.resolve_vpk("vpk", root)

            self.assertEqual(resolved, str(root / ".velopack" / "vpk"))


class PublishReleaseTests(unittest.TestCase):
    def test_version_tag_matches_application_version(self):
        self.assertEqual(publish_release.version_tag(), "v3.1.0")

    def test_publish_draft_patches_tag_and_publication(self):
        releases = [
            {
                "id": 123,
                "tag_name": "untagged-6af3dd6c91d1d31d61e4",
                "draft": True,
            }
        ]
        calls = []

        def request(method, path, token, body=None):
            calls.append((method, path, token, body))
            if method == "GET":
                return releases
            return {"id": 123, "tag_name": "v3.1.0", "draft": False,
                    "html_url": "https://example.test/release"}

        with mock.patch.object(publish_release, "api_request", side_effect=request):
            release = publish_release.publish_github_release("v3.1.0", "token")

        self.assertFalse(release["draft"])
        self.assertEqual(calls[-1], (
            "PATCH",
            "/releases/123",
            "token",
            {"tag_name": "v3.1.0", "draft": False, "prerelease": False},
        ))

    def test_policy_is_enabled_with_exact_version_tag(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy_file = root / "updates.json"
            policy_file.write_text('{"enabled":false,"tag":"v3.0.0"}\n', encoding="utf-8")

            def git_result(*args, **kwargs):
                if args[:3] == ("diff", "--cached", "--quiet"):
                    return subprocess.CompletedProcess(args, 1, stdout="", stderr="")
                return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

            with mock.patch.object(publish_release, "UPDATES_FILE", policy_file), mock.patch.object(
                publish_release, "git",
                side_effect=git_result,
            ):
                changed = publish_release.write_update_policy("v3.1.0")

            self.assertTrue(changed)
            self.assertEqual(json.loads(policy_file.read_text(encoding="utf-8")), {
                "enabled": True,
                "tag": "v3.1.0",
            })


@unittest.skipIf(os.name == "nt" or shutil.which("make") is None, "Requires POSIX make")
class MakeReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.log = self.directory / "steps"
        self.identity = "Developer ID Application: Test Developer (TESTTEAM)"
        self.environment = {
            **os.environ,
            "PATH": f"{self.directory}{os.pathsep}{os.environ.get('PATH', '')}",
            "STEP_LOG": str(self.log),
            "IDENTITIES": "",
            "SECURITY_STATUS": "0",
            "FAIL_STEP": "",
        }
        self.write_executable(
            "security",
            '#!/bin/sh\n'
            'printf "%s\\n" "$IDENTITIES"\n'
            'exit "$SECURITY_STATUS"\n',
        )
        self.make_stub = self.write_executable(
            "recursive-make",
            '#!/bin/sh\n'
            'printf "%s\\n" "$1" >> "$STEP_LOG"\n'
            'if [ "$1" = "$FAIL_STEP" ]; then exit 1; fi\n',
        )

    def write_executable(self, name, content):
        path = self.directory / name
        path.write_text(content)
        path.chmod(0o755)
        return path

    def run_target(self, target="release-macos-dmg", *options):
        return subprocess.run(
            [
                "make", "--no-print-directory", "-j4",
                "-f", str(Path(__file__).resolve().parents[1] / "Makefile"),
                "HOST_OS=Darwin", "PYTHON=python3",
                f"DEV_ID={self.identity}", f"MAKE={self.make_stub}",
                *options, target,
            ],
            cwd=self.directory,
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def steps(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_missing_identity_uses_unsigned_dmg(self):
        result = self.run_target()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.steps(), ["unsigned-dmg"])
        self.assertIn("Building an unsigned DMG", result.stdout)
        self.assertNotIn("Signed, notarized and stapled", result.stdout)

    def test_different_identity_does_not_enable_signing(self):
        self.environment["IDENTITIES"] = (
            '  1) ABCDEF "Developer ID Application: Other Developer (OTHERTEAM)"\n'
            '     1 valid identities found'
        )
        result = self.run_target()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.steps(), ["unsigned-dmg"])

    def test_valid_identity_runs_signed_steps_in_order(self):
        self.environment["IDENTITIES"] = f'  1) ABCDEF "{self.identity}"'
        result = self.run_target()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.steps(), ["build", "sign", "dmg", "notarize", "staple"])
        self.assertIn("Signed, notarized and stapled", result.stdout)

    def test_signing_pipeline_failures_do_not_fall_back(self):
        self.environment["IDENTITIES"] = f'  1) ABCDEF "{self.identity}"'
        stages = ["build", "sign", "dmg", "notarize", "staple"]
        for index, stage in enumerate(stages):
            with self.subTest(stage=stage):
                if self.log.exists():
                    self.log.unlink()
                self.environment["FAIL_STEP"] = stage
                result = self.run_target()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.steps(), stages[:index + 1])
                self.assertNotIn("Signed, notarized and stapled", result.stdout)

    def test_keychain_error_is_not_treated_as_missing_identity(self):
        self.environment["SECURITY_STATUS"] = "1"
        result = self.run_target()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.steps(), [])

    def test_unsigned_build_failure_is_propagated(self):
        self.environment["FAIL_STEP"] = "unsigned-dmg"
        result = self.run_target()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.steps(), ["unsigned-dmg"])

    def test_unsigned_dmg_packages_after_build(self):
        result = self.run_target("unsigned-dmg", "-o", "build")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.steps(), ["dmg"])


if __name__ == "__main__":
    unittest.main()
