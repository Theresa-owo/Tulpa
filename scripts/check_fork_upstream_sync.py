"""Exercise fork synchronization against isolated local Git remotes, offline."""

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "sync-upstream.ps1"
PWSH = shutil.which("pwsh.exe") or shutil.which("pwsh")


@unittest.skipUnless(PWSH, "PowerShell 7 is required for the sync script fixtures")
class ForkSyncChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="tulpa-fork-sync-")
        self.addCleanup(self.temp.cleanup)
        self.base = pathlib.Path(self.temp.name)
        self.repo = self.base / "checkout"
        self.upstream = self.base / "upstream.git"
        self.origin = self.base / "fork.git"
        self.seed = self.base / "seed"
        self.env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1",
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_MERGE_AUTOEDIT="no")
        # Do not inherit a caller's Git repository or command-level configuration.
        for key in list(self.env):
            if key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")) or key in {
                "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_CONFIG_COUNT",
                "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            }:
                self.env.pop(key)
        self.git(self.base, "init", "--bare", str(self.upstream))
        self.git(self.base, "init", "--bare", str(self.origin))
        self.git(self.base, "init", "-b", "main", str(self.seed))
        self.identity(self.seed)
        (self.seed / "shared.txt").write_text("base\n", encoding="utf-8")
        (self.seed / "scripts").mkdir()
        (self.seed / "scripts" / "check_fixture.py").write_text("print('fixture check passed')\n", encoding="utf-8")
        self.manifest = {
            "schema_version": 1, "branch": "main",
            "fork_repository": "github.com/fixture/fork",
            "upstream_repository": "github.com/fixture/upstream",
            "required_checks": ["scripts/check_fixture.py"],
        }
        self.write_manifest(self.seed)
        self.commit(self.seed, "base")
        self.git(self.seed, "push", str(self.upstream), "main")
        self.git(self.seed, "push", str(self.origin), "main")
        self.git(self.base, "clone", "--branch", "main", str(self.origin), str(self.repo))
        self.identity(self.repo)
        self.git(self.repo, "remote", "set-url", "origin", "https://github.com/fixture/fork.git")
        self.git(self.repo, "remote", "add", "upstream", "git@github.com:fixture/upstream.git")
        # Real Git transport goes to temporary bare repos; no network access.
        self.git(self.repo, "config", f"url.{self.origin.as_uri()}.insteadOf", "https://github.com/fixture/fork.git")
        self.git(self.repo, "config", f"url.{self.upstream.as_uri()}.insteadOf", "git@github.com:fixture/upstream.git")

    def git(self, where, *args, ok=True):
        result = subprocess.run(["git", "-C", str(where), *args], env=self.env,
                                capture_output=True, text=True, encoding="utf-8", timeout=30)
        if ok and result.returncode:
            self.fail(f"fixture git {args[0]} failed: {result.stderr}")
        return result.stdout.strip() if ok else result

    def identity(self, where):
        self.git(where, "config", "user.name", "Fork Fixture")
        self.git(where, "config", "user.email", "fixture@example.invalid")
        self.git(where, "config", "commit.gpgsign", "false")
        self.git(where, "config", "core.autocrlf", "false")

    def write_manifest(self, where):
        (where / "fork-manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def commit(self, where, message):
        self.git(where, "add", "--all")
        self.git(where, "commit", "-m", message)
        return self.git(where, "rev-parse", "HEAD")

    def upstream_change(self, filename="upstream.txt", content="upstream\n"):
        (self.seed / filename).write_text(content, encoding="utf-8")
        head = self.commit(self.seed, "upstream change")
        self.git(self.seed, "push", str(self.upstream), "main")
        return head

    def sync(self, *args, succeeds=True):
        result = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-File", str(SCRIPT),
                                 "-RepoPath", str(self.repo), "-Python", os.sys.executable, *args],
                                env=self.env, capture_output=True, text=True, encoding="utf-8", timeout=45)
        self.assertEqual(result.returncode, 0 if succeeds else 1, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def test_preview_fetches_without_merging(self):
        head = self.git(self.repo, "rev-parse", "HEAD")
        self.upstream_change()
        output = self.sync()
        self.assertIn("ahead 0, behind 1", output)
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), head)
        self.assertFalse((self.repo / "upstream.txt").exists())

    def test_merge_retains_both_histories_and_pushes_only_when_requested(self):
        (self.repo / "fork.txt").write_text("fork patch\n", encoding="utf-8")
        fork_head = self.commit(self.repo, "fork patch")
        remote_head = self.git(self.origin, "rev-parse", "main")
        upstream_head = self.upstream_change()
        self.sync("-Apply")
        head = self.git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD^1"), fork_head)
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD^2"), upstream_head)
        self.assertEqual((self.repo / "fork.txt").read_text(), "fork patch\n")
        self.assertEqual((self.repo / "upstream.txt").read_text(), "upstream\n")
        self.assertEqual(self.git(self.origin, "rev-parse", "main"), remote_head)
        self.sync("-Push")
        self.assertEqual(self.git(self.origin, "rev-parse", "main"), head)

    def test_conflict_stops_and_preserves_resolution_state(self):
        (self.repo / "shared.txt").write_text("fork\n", encoding="utf-8")
        head = self.commit(self.repo, "conflicting fork patch")
        upstream_head = self.upstream_change("shared.txt", "upstream\n")
        origin_head = self.git(self.origin, "rev-parse", "main")
        self.sync("-Apply", "-Push", succeeds=False)
        self.assertEqual(self.git(self.repo, "rev-parse", "HEAD"), head)
        self.assertEqual(self.git(self.repo, "rev-parse", "MERGE_HEAD"), upstream_head)
        self.assertIn("<<<<<<<", (self.repo / "shared.txt").read_text())
        self.assertEqual(self.git(self.origin, "rev-parse", "main"), origin_head)
        self.sync("-Apply", succeeds=False)

    def test_dirty_checkout_is_rejected_before_fetch(self):
        self.upstream_change()
        (self.repo / "untracked.txt").write_text("keep me\n", encoding="utf-8")
        self.assertIn("dirty", self.sync("-Apply", succeeds=False))
        self.assertEqual((self.repo / "untracked.txt").read_text(), "keep me\n")
        self.assertNotEqual(self.git(self.repo, "rev-parse", "--verify", "refs/remotes/upstream/main", ok=False).returncode, 0)

    def test_wrong_remote_and_embedded_credentials_are_not_printed(self):
        secret = "fixture-secret-never-display"
        self.git(self.repo, "config", "remote.origin.url", f"https://user:{secret}@github.com/fixture/fork.git")
        output = self.sync("-Apply", succeeds=False)
        self.assertNotIn(secret, output)
        self.assertIn("without embedded credentials", output)
        self.git(self.repo, "config", "remote.origin.url", "https://github.com/other/fork.git")
        self.assertIn("does not match", self.sync(succeeds=False))

    def test_pushurl_override_is_checked(self):
        self.git(self.repo, "config", "remote.origin.pushurl", "git@github.com:other/fork.git")
        self.assertIn("does not match", self.sync("-Push", succeeds=False))

    def test_ssh_uri_and_https_normalize_to_same_identity(self):
        uri = "ssh://git@github.com/fixture/upstream.git"
        self.git(self.repo, "config", "--add", f"url.{self.upstream.as_uri()}.insteadOf", uri)
        self.git(self.repo, "config", "remote.upstream.url", uri)
        self.sync()

    def test_other_branch_and_unborn_checkout_are_rejected(self):
        self.git(self.repo, "checkout", "-b", "feature")
        self.assertIn("branch first", self.sync(succeeds=False))
        self.git(self.repo, "checkout", "--orphan", "unborn")
        self.sync(succeeds=False)
        self.git(self.repo, "branch", "-D", "main")
        self.git(self.repo, "symbolic-ref", "HEAD", "refs/heads/main")
        self.sync(succeeds=False)

    def test_failed_check_does_not_push(self):
        (self.repo / "scripts" / "check_fixture.py").write_text("raise SystemExit(9)\n", encoding="utf-8")
        self.commit(self.repo, "failing fork check")
        origin_head = self.git(self.origin, "rev-parse", "main")
        self.assertIn("Required fork check failed", self.sync("-Apply", "-Push", succeeds=False))
        self.assertEqual(self.git(self.origin, "rev-parse", "main"), origin_head)

    def test_push_requires_integrated_upstream_and_current_origin(self):
        self.upstream_change()
        self.assertIn("not integrated", self.sync("-Push", succeeds=False))
        self.git(self.seed, "push", str(self.origin), "main")
        self.assertIn("commits absent", self.sync("-Apply", succeeds=False))


if __name__ == "__main__":
    if not PWSH:
        raise SystemExit("PowerShell 7 is required; upstream sync checks were not run.")
    unittest.main(verbosity=2)
