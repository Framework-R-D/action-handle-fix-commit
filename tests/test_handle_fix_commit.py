"""Verify commit isolation, destination binding, and fail-closed retry behavior."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from handle_fix_commit import clean_environment, git, handle, push_snapshot


class HandleFixCommitTests(unittest.TestCase):
    """Use ephemeral repositories and mocked networking without live credentials."""

    def setUp(self) -> None:
        """Create a tracked file and one modified working-tree snapshot."""
        temp = tempfile.TemporaryDirectory(prefix="fix-commit-test-")
        self.addCleanup(temp.cleanup)
        self.source = Path(temp.name)
        self.env = clean_environment(self.source)
        git(self.source, ["init", "--quiet", "--template="], self.env)
        self.file = self.source / "example.txt"
        self.file.write_text("before\n", encoding="utf-8")
        git(self.source, ["add", "example.txt"], self.env)
        git(
            self.source,
            ["-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "base"],
            self.env,
        )
        self.sha = git(self.source, ["rev-parse", "HEAD"], self.env).decode().strip()
        self.file.write_text("after\n", encoding="utf-8")
        self.settings = {
            "PR_REPO": "Example/target",
            "PR_REF": "feature/test",
            "PR_SHA": self.sha,
            "TOOL": "formatter",
            "TOKEN": "TEST_SENTINEL",
            "RETRY_ATTEMPTS": "2",
        }

    def test_missing_approval_produces_patch_without_network(self) -> None:
        self.settings["PR_SHA"] = ""
        with patch("handle_fix_commit.push_snapshot") as network:
            outputs = handle(self.source, self.settings)
        network.assert_not_called()
        self.assertEqual(outputs, {"pushed": "false", "patch_name": "fix.patch"})
        self.assertIn(b"+after", (self.source / "fix.patch").read_bytes())

    def test_source_hooks_filters_config_index_and_head_are_not_used_for_push(self) -> None:
        sentinel = self.source / "executed"
        attack = f"touch '{sentinel}'"
        git(self.source, ["config", "filter.evil.clean", attack], self.env)
        git(self.source, ["config", "credential.helper", f"!{attack}"], self.env)
        (self.source / ".gitattributes").write_text("*.txt filter=evil\n", encoding="utf-8")
        config_before = (self.source / ".git/config").read_bytes()
        index_before = (self.source / ".git/index").read_bytes()
        captured = []

        def fake_network(cwd, env, *args):
            self.assertNotIn("TOKEN", env)
            self.assertNotIn(b"evil", (cwd / ".git/config").read_bytes())
            captured.append(cwd)
            return True

        with patch("handle_fix_commit.push_snapshot", side_effect=fake_network):
            outputs = handle(self.source, self.settings)
        self.assertEqual(outputs["pushed"], "true")
        self.assertFalse(sentinel.exists())
        self.assertFalse(captured[0].exists())
        self.assertEqual(config_before, (self.source / ".git/config").read_bytes())
        self.assertEqual(index_before, (self.source / ".git/index").read_bytes())
        self.assertEqual(
            git(self.source, ["rev-parse", "HEAD"], self.env).decode().strip(), self.sha
        )

    def test_wrong_checkout_sha_and_malformed_inputs_fail_closed(self) -> None:
        for key, value in [
            ("PR_SHA", "a" * 40),
            ("PR_REPO", "Example/target\nspoof=true"),
            ("PR_REF", "x:refs/heads/main"),
            ("RETRY_ATTEMPTS", "0"),
        ]:
            with self.subTest(key=key), patch("handle_fix_commit.push_snapshot") as network:
                with self.assertRaises(ValueError):
                    handle(self.source, self.settings | {key: value})
                network.assert_not_called()

    def test_existing_patch_symlink_cannot_clobber_another_file(self) -> None:
        victim = self.source / "victim"
        victim.write_text("unchanged\n", encoding="utf-8")
        (self.source / "fix.patch").symlink_to(victim)
        with self.assertRaises(FileExistsError):
            handle(self.source, self.settings, patch_only=True)
        self.assertEqual(victim.read_text(), "unchanged\n")

    def test_nested_working_directory_preserves_files_outside_staging_scope(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nested-fix-test-") as temporary:
            root = Path(temporary)
            env = clean_environment(root)
            git(root, ["init", "--quiet", "--template="], env)
            outside = root / "README.md"
            outside.write_text("outside\n", encoding="utf-8")
            nested = root / "pkg"
            nested.mkdir()
            target = nested / "code.py"
            target.write_text("before\n", encoding="utf-8")
            git(root, ["add", "README.md", "pkg/code.py"], env)
            git(
                root,
                ["-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "base"],
                env,
            )
            sha = git(root, ["rev-parse", "HEAD"], env).decode().strip()
            target.write_text("after\n", encoding="utf-8")

            def verify_commit(
                cwd, push_env, repository, branch, approved, commit, token, attempts
            ):
                self.assertEqual(git(cwd, ["show", f"{commit}:README.md"], push_env), b"outside\n")
                self.assertEqual(git(cwd, ["show", f"{commit}:pkg/code.py"], push_env), b"after\n")
                return True

            with patch("handle_fix_commit.push_snapshot", side_effect=verify_commit):
                outputs = handle(
                    nested,
                    self.settings | {"PR_SHA": sha},
                )
            self.assertEqual(outputs["pushed"], "true")

    def test_sparse_checkout_fails_before_staging(self) -> None:
        git(self.source, ["config", "core.sparseCheckout", "true"], self.env)
        with patch("handle_fix_commit.push_snapshot") as network:
            with self.assertRaisesRegex(ValueError, "Sparse checkouts are not supported"):
                handle(self.source, self.settings)
        network.assert_not_called()

    def test_tracked_deletions_remain_supported_in_complete_checkout(self) -> None:
        self.file.unlink()
        outputs = handle(self.source, self.settings, patch_only=True)
        self.assertEqual(outputs["patch_name"], "fix.patch")
        patch_text = (self.source / "fix.patch").read_text(encoding="utf-8")
        self.assertIn("--- a/example.txt", patch_text)
        self.assertIn("+++ /dev/null", patch_text)

    def test_moved_head_including_another_trusted_formatter_never_rebases(self) -> None:
        for moved in ["a" * 40, "b" * 40]:
            with (
                self.subTest(moved=moved),
                patch(
                    "handle_fix_commit.git",
                    return_value=f"{moved}\trefs/heads/feature/test\n".encode(),
                ) as mocked,
            ):
                result = push_snapshot(
                    self.source,
                    self.env,
                    "Example/target",
                    "feature/test",
                    self.sha,
                    "c" * 40,
                    "TEST_SENTINEL",
                    2,
                )
                self.assertFalse(result)
                self.assertEqual(mocked.call_count, 1)

    def test_retry_keeps_explicit_destination_and_immutable_lease(self) -> None:
        commit = "c" * 40
        ref = "refs/heads/feature/test"
        expected = f"{self.sha}\t{ref}\n".encode()
        with (
            patch("handle_fix_commit.git", side_effect=[expected, expected, expected]) as mocked,
            patch(
                "handle_fix_commit.git_result",
                side_effect=[
                    subprocess.CompletedProcess([], 1, b"", b"failed"),
                    subprocess.CompletedProcess([], 0, b"pushed", b""),
                ],
            ) as pushes,
        ):
            self.assertTrue(
                push_snapshot(
                    self.source,
                    self.env,
                    "Example/target",
                    "feature/test",
                    self.sha,
                    commit,
                    "TEST_SENTINEL",
                    2,
                )
            )
        self.assertEqual(pushes.call_count, 2)
        for call in pushes.call_args_list:
            self.assertIn(f"--force-with-lease={ref}:{self.sha}", call.args[1])
            self.assertIn("https://github.com/Example/target.git", call.args[1])
            self.assertNotIn("TEST_SENTINEL", " ".join(call.args[1]))
        self.assertEqual(mocked.call_count, 3)

    def test_acknowledged_push_succeeds_without_a_followup_lookup(self) -> None:
        ref = "refs/heads/feature/test"
        expected = f"{self.sha}\t{ref}\n".encode()
        with (
            patch("handle_fix_commit.git", return_value=expected) as lookup,
            patch(
                "handle_fix_commit.git_result",
                return_value=subprocess.CompletedProcess([], 0, b"pushed", b""),
            ),
        ):
            self.assertTrue(
                push_snapshot(
                    self.source,
                    self.env,
                    "Example/target",
                    "feature/test",
                    self.sha,
                    "c" * 40,
                    "TEST_SENTINEL",
                    2,
                )
            )
        self.assertEqual(lookup.call_count, 1)

    def test_ambiguous_push_is_recovered_by_remote_ref(self) -> None:
        """Accept a nonzero push only when the destination now names the commit."""
        ref = "refs/heads/feature/test"
        expected = f"{self.sha}\t{ref}\n".encode()
        pushed = f"{'c' * 40}\t{ref}\n".encode()
        with (
            patch("handle_fix_commit.git", side_effect=[expected, pushed]) as lookup,
            patch(
                "handle_fix_commit.git_result",
                return_value=subprocess.CompletedProcess([], 1, b"", b"connection lost"),
            ),
        ):
            self.assertTrue(
                push_snapshot(
                    self.source,
                    self.env,
                    "Example/target",
                    "feature/test",
                    self.sha,
                    "c" * 40,
                    "TEST_SENTINEL",
                    2,
                )
            )
        self.assertEqual(lookup.call_count, 2)

    def test_inherited_secret_and_git_environment_is_not_forwarded(self) -> None:
        with patch.dict(os.environ, {"GH_TOKEN": "sentinel", "GIT_CONFIG_COUNT": "99"}):
            env = clean_environment(self.source)
        self.assertNotIn("GH_TOKEN", env)
        self.assertNotIn("GIT_CONFIG_COUNT", env)


if __name__ == "__main__":
    unittest.main()
