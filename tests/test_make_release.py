import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


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

    def run_target(self, target="release", *options):
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
