from __future__ import annotations

import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree


class SvnOperationError(Exception):
    def __init__(self, message: str, authentication_required: bool = False) -> None:
        super().__init__(message)
        self.authentication_required = authentication_required


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

    def run(self, action: str, paths: list[str] | None = None, message: str = "") -> str:
        if action not in {"status", "diff", "add", "commit", "update", "checkout"}:
            raise SvnOperationError(f"Unsupported SVN action: {action}")
        if action == "checkout":
            if not paths or len(paths) != 2:
                raise SvnOperationError("Provide a repository URL and checkout destination.")
            return self.checkout(paths[0], paths[1])
        if action in {"add", "commit"} and not paths:
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
        options = ["--parents"] if action == "add" else []
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