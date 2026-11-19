import argparse
import json
import os
import runpy
import subprocess
import sys
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "ldelaprade/WinFileForMacOS"
UPDATES_FILE = ROOT / "updates.json"
TOKEN_FILE = ROOT / "GITHUB_TOKEN.txt"


def version_tag():
    version = runpy.run_path(str(ROOT / "src" / "version.py"))["APP_VERSION"]
    if not isinstance(version, str) or not version:
        raise RuntimeError("APP_VERSION is missing or invalid.")
    return f"v{version}"


def resolve_token():
    token = os.environ.get("VPK_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token and TOKEN_FILE.is_file():
        token = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if not token:
        raise RuntimeError("Set VPK_TOKEN or GITHUB_TOKEN, or create ignored GITHUB_TOKEN.txt.")
    return token


def git(*args, check=True):
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or "git command failed.")
    return result


def verify_publish_prerequisites(tag):
    branch = git("branch", "--show-current").stdout.strip()
    if branch != "main":
        raise RuntimeError(f"velo-publish must run on main, not {branch or 'a detached HEAD'}.")

    remote = git("remote", "get-url", "origin").stdout.strip()
    if not remote.startswith("git@github.com:"):
        raise RuntimeError("origin must use the passwordless SSH GitHub remote.")

    local_tag = git("rev-parse", "--verify", f"refs/tags/{tag}", check=False)
    if local_tag.returncode:
        raise RuntimeError(f"Local Git tag {tag} does not exist.")

    remote_tag = git("ls-remote", "--exit-code", "--tags", "origin", f"refs/tags/{tag}", check=False)
    if remote_tag.returncode:
        raise RuntimeError(f"Git tag {tag} has not been pushed to origin.")


def write_update_policy(tag):
    policy = {"enabled": True, "tag": tag}
    UPDATES_FILE.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
    git("add", "--", "updates.json")
    staged = git("diff", "--cached", "--quiet", "--", "updates.json", check=False)
    if staged.returncode == 0:
        git("push", "origin", "main")
        return False
    git("commit", "-m", f"Enable update alerts for {tag}")
    git("push", "origin", "main")
    return True


def api_request(method, path, token, body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{REPOSITORY}{path}",
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "RetroFile-Release-Publisher",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API HTTP {error.code}: {detail}")
    except URLError as error:
        raise RuntimeError(f"GitHub API request failed: {error.reason}")


def publish_github_release(tag, token):
    releases = api_request("GET", "/releases?per_page=100", token)
    for release in releases:
        if release.get("draft") and (
            release.get("tag_name") == tag or str(release.get("tag_name", "")).startswith("untagged-")
        ):
            return api_request(
                "PATCH",
                f"/releases/{release['id']}",
                token,
                {"tag_name": tag, "draft": False, "prerelease": False},
            )

    for release in releases:
        if release.get("tag_name") == tag:
            if release.get("draft"):
                raise RuntimeError(f"Draft release {tag} could not be resolved.")
            return release

    raise RuntimeError(f"No draft or published GitHub release was found for {tag}.")


def main():
    parser = argparse.ArgumentParser(description="Publish the RetroFile Velopack release.")
    parser.add_argument("--confirm", action="store_true", help="Confirm publication.")
    args = parser.parse_args()
    if not args.confirm:
        parser.error("Pass --confirm to publish the release.")

    try:
        tag = version_tag()
        verify_publish_prerequisites(tag)
        token = resolve_token()
        write_update_policy(tag)
        release = publish_github_release(tag, token)
        print(f"Published {tag}: {release['html_url']}")
    except (RuntimeError, OSError, ValueError, KeyError) as error:
        print(f"velo-publish failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
