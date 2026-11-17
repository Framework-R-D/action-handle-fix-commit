"""Prepare tracked-file fixes in an isolated Git repository and push one snapshot."""

from __future__ import annotations

import base64
import os
import re
import subprocess
import tempfile
from pathlib import Path


def clean_environment(home: Path) -> dict[str, str]:
    """Exclude inherited credentials, Git overrides, and executable configuration."""
    return {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "github-actions[bot]",
        "GIT_COMMITTER_NAME": "github-actions[bot]",
        "GIT_AUTHOR_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
        "GIT_COMMITTER_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
    }


def git(cwd: Path, args: list[str], env: dict[str, str], *, check: bool = True) -> bytes:
    """Run Git without printing subprocess output or secret-bearing arguments."""
    result = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={os.devnull}", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        check=False,
    )
    if check and result.returncode:
        raise ValueError("Git operation failed; no subprocess output was disclosed")
    return result.stdout if result.returncode == 0 else b""


def push_snapshot(
    cwd: Path,
    env: dict[str, str],
    repository: str,
    branch: str,
    sha: str,
    commit: str,
    token: str,
    attempts: int,
) -> bool:
    """Retry only against the approved SHA, never fetch or rebase onto a new head."""
    url = f"https://github.com/{repository}.git"
    ref = f"refs/heads/{branch}"
    auth = base64.b64encode(f"x-access-token:{token}".encode()).decode("ascii")
    network_env = env | {
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_0": f"http.{url}.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {auth}",
        "GIT_CONFIG_KEY_1": "http.followRedirects",
        "GIT_CONFIG_VALUE_1": "false",
        "GIT_CONFIG_KEY_2": "credential.helper",
        "GIT_CONFIG_VALUE_2": "",
    }
    for _ in range(attempts):
        result = git(cwd, ["ls-remote", "--refs", url, ref], network_env, check=False)
        if result == f"{commit}\t{ref}\n".encode():
            return True  # An interrupted push may already have succeeded.
        if result != f"{sha}\t{ref}\n".encode():
            return False
        git(
            cwd,
            [
                "push",
                "--porcelain",
                "--recurse-submodules=no",
                f"--force-with-lease={ref}:{sha}",
                url,
                f"{commit}:{ref}",
            ],
            network_env,
            check=False,
        )
        if git(cwd, ["ls-remote", "--refs", url, ref], network_env, check=False) == (
            f"{commit}\t{ref}\n".encode()
        ):
            return True
    return False


def handle(source: Path, settings: dict[str, str], *, patch_only: bool = False) -> dict[str, str]:
    """Commit only tracked changes, leaving source Git config, refs, and index intact."""
    repository = settings["PR_REPO"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid target repository")
    if repository.split("/")[1] in {".", ".."}:
        raise ValueError("Invalid target repository")
    branch = settings["PR_REF"]
    if not branch or branch.startswith(("-", "refs/")) or any(ord(c) < 32 for c in branch):
        raise ValueError("Invalid target branch")
    approved = settings.get("PR_SHA", "")
    if approved and not re.fullmatch(r"[0-9a-f]{40}", approved):
        raise ValueError("Expected a full immutable source SHA")
    attempts_text = settings.get("RETRY_ATTEMPTS", "6")
    if not re.fullmatch(r"[1-9][0-9]?", attempts_text) or int(attempts_text) > 20:
        raise ValueError("retry-attempts must be between 1 and 20")
    tool = settings["TOOL"]
    if not tool or len(tool) > 100 or any(ord(c) < 32 for c in tool):
        raise ValueError("Invalid tool label")

    with tempfile.TemporaryDirectory(prefix="handle-fix-commit-") as temp:
        isolated = Path(temp)
        env = clean_environment(isolated)
        git(source, ["check-ref-format", f"refs/heads/{branch}"], env)
        head = git(source, ["rev-parse", "--verify", "HEAD^{commit}"], env).decode().strip()
        if approved and head != approved:
            raise ValueError("Checkout HEAD does not match the approved source SHA")
        common = git(source, ["rev-parse", "--path-format=absolute", "--git-common-dir"], env)
        objects = Path(os.fsdecode(common).strip()) / "objects"
        if any(c in str(objects) for c in "\r\n"):
            raise ValueError("Invalid Git object directory")
        git(isolated, ["init", "--quiet", "--template="], env)
        # Only objects are shared; source hooks, remotes, credential helpers and filters are not.
        (isolated / ".git/objects/info/alternates").write_text(f"{objects}\n", encoding="utf-8")
        env = env | {"GIT_WORK_TREE": str(source), "GIT_DIR": str(isolated / ".git")}
        git(isolated, ["read-tree", head], env)
        git(source, ["add", "-u", "--", "."], env)
        patch = git(
            isolated, ["diff", "--cached", "--binary", "--no-ext-diff", "--no-textconv", head], env
        )
        if not patch:
            return {"pushed": "false"}
        tree = git(isolated, ["write-tree"], env).decode().strip()
        commit = git(isolated, ["commit-tree", tree, "-p", head, "-m", f"Apply {tool} fixes"], env)
        commit_sha = commit.decode().strip()
        token = settings.get("TOKEN", "")
        if (
            not patch_only
            and approved
            and token
            and push_snapshot(
                isolated, env, repository, branch, approved, commit_sha, token, int(attempts_text)
            )
        ):
            return {"pushed": "true", "commit_sha": commit_sha, "commit_sha_short": commit_sha[:7]}
        # Exclusive creation prevents overwriting target files or following a pre-existing symlink.
        descriptor = os.open(source / "fix.patch", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as patch_file:
            patch_file.write(patch)
        return {"pushed": "false", "patch_name": "fix.patch"}


def main() -> None:
    """Publish only validated fixed-name outputs; never expose underlying Git diagnostics."""
    outputs = handle(
        Path.cwd(), dict(os.environ), patch_only=os.environ.get("PATCH_ONLY") == "true"
    )
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output_file:
        for name, value in outputs.items():
            output_file.write(f"{name}={value}\n")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError):
        raise SystemExit(
            "Fix handling failed closed; check approved snapshot and patch destination"
        ) from None
