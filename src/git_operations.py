from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from git import GitCommandError, InvalidGitRepositoryError, NoSuchPathError, Repo


class GitOperationError(Exception):
    pass


@dataclass(frozen=True)
class GitChange:
    path: str
    original_path: str | None = None


@dataclass(frozen=True)
class GitChanges:
    root: str
    modified: list[GitChange]
    untracked: list[GitChange]
    staged: list[GitChange]
    branch: str | None = None


@dataclass(frozen=True)
class GitBranchInfo:
    current: str | None
    local: list[str]
    remote: list[tuple[str, str]]
    upstream: str | None = None
    remotes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GitRepositoryStatus:
    text: str
    branches: GitBranchInfo


@dataclass(frozen=True)
class GitIconStatus:
    root: str
    paths: dict[str, str]
    branch: str | None = None

    def status_for(self, path: str) -> str | None:
        try:
            relative = Path(os.path.abspath(path)).relative_to(self.root)
        except ValueError:
            return None
        if ".git" in relative.parts:
            return None
        name = relative.as_posix()
        if name in self.paths:
            return self.paths[name]
        for parent in relative.parents:
            inherited = self.paths.get(parent.as_posix())
            if inherited in {"ignored", "untracked"}:
                return inherited
        return None


class GitOperations:
    def __init__(self, directory: str) -> None:
        self.directory = Path(directory).absolute()

    def run(
        self, action: str, paths: list[str] | None = None, message: str = ""
    ) -> str | GitChanges:
        try:
            if action == "clone":
                if not paths or len(paths) != 2:
                    raise GitOperationError("Provide a repository URL and clone destination.")
                return self.clone(paths[0], paths[1])
            if action == "init":
                try:
                    with Repo(self.directory, search_parent_directories=True):
                        raise GitOperationError("This folder already belongs to a Git repository.")
                except InvalidGitRepositoryError:
                    pass
                with Repo.init(self.directory) as repository:
                    return f"Initialized repository in {repository.working_tree_dir}"

            with Repo(self.directory, search_parent_directories=True) as repository:
                if repository.bare or repository.working_tree_dir is None:
                    raise GitOperationError("A working-tree repository is required.")
                selected = self._relative_paths(repository.working_tree_dir, paths or []) if action in {
                    "stage", "unstage", "diff"
                } else []
                with repository.git.custom_environment(
                    GIT_LITERAL_PATHSPECS="1", GIT_TERMINAL_PROMPT="0"
                ):
                    if action in {"staging", "unstaging"}:
                        return self._changes(repository)
                    if action == "status":
                        return repository.git.status(untracked_files="all", **self._timeout_options(30))
                    if action in {"push", "pull", "fetch"}:
                        command = {
                            "push": repository.git.push,
                            "pull": repository.git.pull,
                            "fetch": repository.git.fetch,
                        }[action]
                        options = {
                            "push": ["--verbose"],
                            "pull": ["--ff-only"],
                            "fetch": ["--all", "--prune"],
                        }[action]
                        _, stdout, stderr = command(
                            *options, with_extended_output=True, **self._timeout_options(120)
                        )
                        return "\n".join(part for part in (stdout, stderr) if part) or f"Git {action} completed."
                    if action == "publish":
                        if not paths or len(paths) != 2:
                            raise GitOperationError("Select a remote to publish the current branch to.")
                        remote, branch = paths
                        if remote not in {item.name for item in repository.remotes}:
                            raise GitOperationError(f"Git remote '{remote}' does not exist.")
                        if branch not in {head.name for head in repository.heads}:
                            raise GitOperationError(f"Local branch '{branch}' does not exist.")
                        repository.git.push(
                            "--set-upstream", remote, branch, **self._timeout_options(120)
                        )
                        return f"Published branch '{branch}' to '{remote}' and set its upstream."
                    if action == "switch":
                        branch = self._required_argument(paths, "Select a local branch first.")
                        if branch not in {head.name for head in repository.heads}:
                            raise GitOperationError(f"Local branch '{branch}' does not exist.")
                        repository.git.switch(branch, **self._timeout_options(30))
                        return f"Switched to branch '{branch}'."
                    if action == "track":
                        if not paths or len(paths) != 2:
                            raise GitOperationError("Select a remote branch first.")
                        remote_ref, local_branch = paths
                        if remote_ref not in {ref.name for remote in repository.remotes for ref in remote.refs}:
                            raise GitOperationError(f"Remote branch '{remote_ref}' does not exist.")
                        if not local_branch.strip():
                            raise GitOperationError("A local branch name is required.")
                        repository.git.check_ref_format("--branch", local_branch)
                        repository.git.switch(
                            "--track", "-c", local_branch, remote_ref,
                            **self._timeout_options(30),
                        )
                        return f"Created tracking branch '{local_branch}' from '{remote_ref}'."
                    if action == "create_branch":
                        branch = self._required_argument(paths, "Enter a local branch name.")
                        repository.git.check_ref_format("--branch", branch)
                        repository.git.switch(
                            "--no-track", "-c", branch, **self._timeout_options(30)
                        )
                        return f"Created local branch '{branch}' without an upstream."
                    if action == "diff":
                        return repository.git.diff(
                            "--cached", "--no-ext-diff", "--no-textconv", **self._timeout_options(30)
                        ) or "No staged changes."
                    if action in {"stage", "unstage"}:
                        if not selected:
                            raise GitOperationError("Select files or folders first.")
                        if action == "stage":
                            repository.git.add("--all", "--", *selected, **self._timeout_options(30))
                        elif repository.head.is_valid():
                            repository.git.reset("HEAD", "--", *selected, **self._timeout_options(30))
                        else:
                            repository.git.rm(
                                "--cached", "-r", "--force", "--ignore-unmatch",
                                "--", *selected, **self._timeout_options(30),
                            )
                        return f"{'Staged' if action == 'stage' else 'Unstaged'} {len(selected)} selected item(s)."
                    if action == "commit":
                        if not message.strip():
                            raise GitOperationError("A commit message is required.")
                        return repository.git.commit("-m", message.strip(), **self._timeout_options(30))
                    raise GitOperationError(f"Unsupported Git action: {action}")
        except (InvalidGitRepositoryError, NoSuchPathError) as error:
            raise GitOperationError("This folder is not inside a Git repository.") from error
        except GitCommandError as error:
            raise GitOperationError(error.stderr.strip() or str(error)) from error
        except OSError as error:
            raise GitOperationError(str(error)) from error

    def icon_status(self) -> GitIconStatus:
        with Repo(self.directory, search_parent_directories=True) as repository:
            if repository.bare or repository.working_tree_dir is None:
                raise GitOperationError("A working-tree repository is required.")
            priorities = {
                "clean": 0, "ignored": 1, "untracked": 2, "added": 3,
                "modified": 4, "deleted": 5, "conflicted": 6,
            }
            statuses: dict[str, str] = {".": "clean"}

            def include(name: str, status: str) -> None:
                relative = Path(name.rstrip("/"))
                entries = (relative,) if status == "ignored" else (relative, *relative.parents)
                for entry in entries:
                    key = entry.as_posix()
                    previous = statuses.get(key)
                    if previous is None or priorities[status] > priorities[previous]:
                        statuses[key] = status

            with repository.git.custom_environment(GIT_OPTIONAL_LOCKS="0"):
                tracked = repository.git.ls_files(
                    "--cached", "-z", **self._timeout_options(30)
                )
                for name in tracked.split("\0"):
                    if name:
                        include(name, "clean")
                records = iter(repository.git.status(
                    "--porcelain=v1", "-z", "--ignored=matching",
                    untracked_files="all", **self._timeout_options(30),
                ).split("\0"))
                for record in records:
                    if not record:
                        continue
                    flags, name = record[:2], record[3:]
                    original = next(records) if "R" in flags or "C" in flags else None
                    if flags == "!!":
                        status = "ignored"
                    elif flags == "??":
                        status = "untracked"
                    elif "U" in flags or flags in {"AA", "DD"}:
                        status = "conflicted"
                    elif "D" in flags:
                        status = "deleted"
                    elif flags == "A ":
                        status = "added"
                    else:
                        status = "modified"
                    include(name, status)
                    if original:
                        include(original, "modified")
            branch = None if repository.head.is_detached else repository.active_branch.name
            return GitIconStatus(repository.working_tree_dir, statuses, branch)

    def clone(self, url: str, destination: str) -> str:
        url = url.strip()
        if not url:
            raise GitOperationError("Enter a Git repository URL or local path.")
        try:
            parsed_url = urlsplit(url)
        except ValueError as error:
            raise GitOperationError("Enter a valid Git repository URL or local path.") from error
        if parsed_url.username is not None or parsed_url.password is not None:
            raise GitOperationError("Do not include credentials in the repository URL.")
        if parsed_url.scheme and parsed_url.scheme not in {"file", "http", "https", "ssh", "git"}:
            raise GitOperationError("Enter a Git URL using HTTPS, SSH, git, or a local path.")
        target = Path(os.path.abspath(os.path.expanduser(destination)))
        if not target.parent.is_dir():
            raise GitOperationError("The clone parent folder does not exist.")
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            raise GitOperationError("The clone destination must be a new or empty folder.")
        with Repo.clone_from(
            url, str(target), env={"GIT_TERMINAL_PROMPT": "0"},
            **self._timeout_options(120),
        ) as repository:
            return f"Cloned {url} to {repository.working_tree_dir}."

    def branch_info(self) -> GitBranchInfo:
        try:
            with Repo(self.directory, search_parent_directories=True) as repository:
                if repository.bare or repository.working_tree_dir is None:
                    raise GitOperationError("A working-tree repository is required.")
                current = None if repository.head.is_detached else repository.active_branch.name
                local = sorted(head.name for head in repository.heads)
                active = None if current is None else repository.active_branch
                tracking = None if active is None else active.tracking_branch()
                remote = sorted(
                    (reference.name, reference.remote_head)
                    for remote_repository in repository.remotes
                    for reference in remote_repository.refs
                    if reference.remote_head != "HEAD"
                )
                return GitBranchInfo(
                    current,
                    local,
                    remote,
                    tracking.name if tracking is not None else None,
                    sorted(item.name for item in repository.remotes),
                )
        except (InvalidGitRepositoryError, NoSuchPathError) as error:
            raise GitOperationError("This folder is not inside a Git repository.") from error

    @staticmethod
    def _required_argument(paths: list[str] | None, error_message: str) -> str:
        if not paths or not paths[0].strip():
            raise GitOperationError(error_message)
        return paths[0]

    @staticmethod
    def _timeout_options(seconds: int) -> dict[str, int]:
        return {} if sys.platform == "win32" else {"kill_after_timeout": seconds}

    @staticmethod
    def _changes(repository: Repo) -> GitChanges:
        records = iter(repository.git.status(
            "--porcelain=v1", "-z", untracked_files="all", **GitOperations._timeout_options(30)
        ).split("\0"))
        modified: list[GitChange] = []
        untracked: list[GitChange] = []
        staged: list[GitChange] = []
        for record in records:
            if not record:
                continue
            index_status, worktree_status = record[:2]
            path = record[3:]
            original_path = next(records) if "R" in record[:2] or "C" in record[:2] else None
            if record[:2] == "??":
                untracked.append(GitChange(path))
                continue
            if index_status not in {" ", "?", "!", "U"} and worktree_status != "U":
                staged.append(GitChange(path, original_path if index_status in {"R", "C"} else None))
            if worktree_status not in {" ", "?", "!"} or index_status == "U":
                modified.append(GitChange(path, original_path if worktree_status in {"R", "C"} else None))
        branch = None if repository.head.is_detached else repository.active_branch.name
        return GitChanges(repository.working_tree_dir, modified, untracked, staged, branch)

    @staticmethod
    def _relative_paths(root: str, paths: list[str]) -> list[str]:
        root_path = Path(os.path.abspath(root))
        relative_paths = []
        for path in paths:
            try:
                relative = Path(os.path.abspath(path)).relative_to(root_path)
            except ValueError as error:
                raise GitOperationError("Selected items must belong to this repository.") from error
            if any(part.lower() == ".git" for part in relative.parts):
                raise GitOperationError("Git metadata cannot be staged or unstaged.")
            relative_paths.append(relative.as_posix())
        return relative_paths