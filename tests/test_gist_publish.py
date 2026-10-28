import copy
import hashlib
import json
import unittest
from unittest import mock
import urllib.parse

from test_publish import MemoryGitHub
from terraform_to_avm.gist_publish import (
    BRANCH, DIRECTORY, MAX_FILE_BYTES, MAX_TOTAL_BYTES, MARKER, PublishError, _digest, publish,
)


class HistoricalPullGitHub(MemoryGitHub):
    """Model GitHub's open-PR filtering and unique pull request numbers."""

    def request(self, method, path, data=None):
        if method == "PATCH" and "/pulls/" in path:
            self.calls.append((method, path, copy.deepcopy(data)))
            number = int(path.rsplit("/", 1)[-1])
            pr = next(pr for pr in self.pulls if pr["number"] == number)
            pr.update(data)
            return copy.deepcopy(pr)
        value = super().request(method, path, data)
        if method == "GET" and "/pulls?" in path:
            state = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)["state"][0]
            return [pr for pr in value if state == "all" or pr["state"] == state]
        if method == "POST" and path.endswith("/pulls"):
            self.pulls[-1]["number"] = len(self.pulls) + 6
            return copy.deepcopy(self.pulls[-1])
        return value


class GistPublishTests(unittest.TestCase):
    def setUp(self):
        self.api = MemoryGitHub({
            DIRECTORY + "avmigrate.py": b"old engine\n",
            DIRECTORY + "provenance.json": b'{"version":"1.0.0"}\n',
            DIRECTORY + "upstream-license.txt": b"old license\n",
            DIRECTORY + "README.md": b"Attribution retained\n",
            "README.md": b"repository\n",
        })
        self.base = self.api.source
        self.revision = "a" * 40
        self.engine = b"raise RuntimeError('must never execute')\n"
        self.set_candidate()
        self.fetcher = mock.Mock(side_effect=lambda revision: copy.deepcopy(self.files))

    def set_candidate(self, license_bytes=None, version="1.2.3"):
        self.hash = hashlib.sha256(self.engine).hexdigest()
        self.files = {
            "avmigrate.py": self.engine,
            "provenance.json": json.dumps({
                "version": version, "git_revision": self.revision, "sha256": self.hash,
                "author": "upstream-author", "license_status": "upstream original notice",
            }).encode(),
        }
        if license_bytes is not None:
            self.files["upstream-license.txt"] = license_bytes

    def run_publish(self, **kwargs):
        args = {
            "revision": self.revision, "sha256": self.hash, "repository": "owner/repo",
            "base_commit": self.base, "base_branch": "main", "api": self.api,
            "fetcher": self.fetcher,
        }
        args.update(kwargs)
        return publish(**args)

    def writes(self):
        return [call for call in self.api.calls if call[0] != "GET"]

    def tree(self):
        return self.api.flatten(self.api.commits[self.api.refs[BRANCH]]["tree"]["sha"])

    def test_creates_draft_only_with_exact_scope_and_preserves_base(self):
        result = self.run_publish()
        self.assertEqual(result["status"], "published")
        self.assertEqual(result["branch"], BRANCH)
        self.fetcher.assert_called_once_with(self.revision)
        self.assertEqual(self.api.refs["main"], self.base)
        self.assertEqual(self.api.blobs[self.tree()[DIRECTORY + "avmigrate.py"][1]], self.engine)
        self.assertNotIn(DIRECTORY + "upstream-license.txt", self.tree())
        original = self.api.flatten(self.api.commits[self.base]["tree"]["sha"])
        self.assertEqual(self.tree()[DIRECTORY + "README.md"], original[DIRECTORY + "README.md"])
        self.assertEqual(self.tree()["README.md"], original["README.md"])
        entries = [data["tree"] for method, path, data in self.writes()
                   if method == "POST" and path.endswith("/git/trees")][0]
        self.assertEqual({entry["path"] for entry in entries}, {
            DIRECTORY + name for name in ("avmigrate.py", "provenance.json", "upstream-license.txt")
        })
        self.assertTrue(self.api.pulls[0]["draft"])
        self.assertFalse(any("/merge" in path for _, path, _ in self.api.calls))
        body = self.api.pulls[0]["body"]
        for value in (self.revision, self.hash, "1.2.3", "No upstream license", "not the upstream",
                      "do not establish cloud", "reviewed merge"):
            self.assertIn(value, body)

    def test_identical_repeat_has_no_writes_or_duplicate_pr(self):
        first = self.run_publish()
        commits = len(self.api.commits)
        self.api.calls.clear()
        result = self.run_publish()
        self.assertEqual(result["status"], "unchanged")
        self.assertEqual(result["pull_request"], first["pull_request"])
        self.assertEqual(len(self.api.commits), commits)
        self.assertEqual(len(self.api.pulls), 1)
        self.assertEqual(self.writes(), [])

    def test_historical_closed_prs_do_not_block_later_updates(self):
        for merged in (False, True):
            with self.subTest(merged=merged):
                self.setUp()
                self.api = HistoricalPullGitHub({
                    DIRECTORY + "avmigrate.py": b"old engine\n",
                    DIRECTORY + "provenance.json": b'{"version":"1.0.0"}\n',
                })
                self.base = self.api.source
                self.run_publish()
                old_head = self.api.refs.pop(BRANCH)
                self.api.pulls[0].update({
                    "state": "closed", "merged_at": "2026-10-28T00:00:00Z" if merged else None,
                })
                historical = copy.deepcopy(self.api.pulls[0])
                self.base = self.api.add_commit(
                    self.api.commits[old_head if merged else self.base]["tree"]["sha"],
                    [self.base], "Reviewed base advance",
                )
                self.api.refs["main"] = self.base
                self.revision = "b" * 40
                self.engine = b"next tested revision\n"
                self.set_candidate(version="2.0.0")
                result = self.run_publish()
                self.assertEqual(result["status"], "published")
                self.assertEqual(result["pull_request"], 8)
                self.assertEqual(len(self.api.pulls), 2)
                self.assertTrue(self.api.pulls[1]["draft"])
                self.assertEqual(self.api.pulls[0], historical)
                self.assertEqual(self.run_publish()["status"], "unchanged")
                self.revision = "c" * 40
                self.engine = b"next tested revision after that\n"
                self.set_candidate(version="2.0.1")
                self.api.calls.clear()
                updated = self.run_publish()
                self.assertEqual(updated["status"], "updated")
                self.assertEqual(updated["pull_request"], 8)
                self.assertEqual(self.api.pulls[0], historical)
                pull_patches = [path for method, path, _ in self.writes()
                                if method == "PATCH" and "/pulls/" in path]
                self.assertEqual(pull_patches, ["/repos/owner/repo/pulls/8"])
                self.assertEqual(self.run_publish()["status"], "unchanged")
                self.assertEqual(len(self.api.pulls), 2)

    def test_new_revision_updates_existing_branch_with_fast_forward(self):
        self.run_publish()
        previous = self.api.refs[BRANCH]
        self.revision = "b" * 40
        self.engine = b"new engine revision\n"
        self.set_candidate(license_bytes=b"Copyright upstream. Original license.\n", version="2.0.0-rc.1")
        result = self.run_publish()
        self.assertEqual(result["status"], "updated")
        self.assertEqual(self.api.commits[self.api.refs[BRANCH]]["parents"], [{"sha": previous}])
        self.assertEqual(len(self.api.pulls), 1)
        self.assertIn("upstream license file is included", self.api.pulls[0]["body"])
        self.assertEqual(self.run_publish()["status"], "unchanged")
        updates = [data for method, path, data in self.api.calls
                   if method == "PATCH" and "/git/refs/" in path]
        self.assertTrue(updates)
        self.assertTrue(all(data["force"] is False for data in updates))

    def test_updated_tree_is_built_from_source_not_prior_candidate(self):
        self.set_candidate(license_bytes=b"first revision license")
        self.run_publish()
        self.revision = "b" * 40
        self.engine = b"second revision\n"
        self.set_candidate()
        self.run_publish()
        self.assertNotIn(DIRECTORY + "upstream-license.txt", self.tree())
        self.assertEqual(self.run_publish()["status"], "unchanged")

    def test_no_diff_is_no_publication(self):
        self.api = MemoryGitHub({DIRECTORY + name: content for name, content in self.files.items()})
        self.base = self.api.source
        self.assertEqual(self.run_publish(), {"status": "no_changes"})
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.api.pulls, [])

    def test_missing_license_deletes_previous_even_if_engine_unchanged(self):
        self.api = MemoryGitHub({
            **{DIRECTORY + name: content for name, content in self.files.items()},
            DIRECTORY + "upstream-license.txt": b"previous upstream license",
        })
        self.base = self.api.source
        self.run_publish()
        self.assertNotIn(DIRECTORY + "upstream-license.txt", self.tree())

    def test_base_advance_refuses_without_writes(self):
        self.api.refs["main"] = self.api.add_commit(
            self.api.commits[self.base]["tree"]["sha"], [self.base], "base advanced",
        )
        with self.assertRaisesRegex(PublishError, "Base branch advanced"):
            self.run_publish()
        self.assertEqual(self.writes(), [])

    def test_stale_owned_branch_requires_manual_review_on_new_base(self):
        self.run_publish()
        self.base = self.api.add_commit(
            self.api.commits[self.base]["tree"]["sha"], [self.base], "new base",
        )
        self.api.refs["main"] = self.base
        self.api.calls.clear()
        with self.assertRaisesRegex(PublishError, "stale"):
            self.run_publish()
        self.assertEqual(self.writes(), [])

    def test_human_noop_commit_is_refused(self):
        self.run_publish()
        previous = self.api.refs[BRANCH]
        self.api.refs[BRANCH] = self.api.add_commit(
            self.api.commits[previous]["tree"]["sha"], [previous], "Human edit",
        )
        self.api.calls.clear()
        with self.assertRaisesRegex(PublishError, "human-edited"):
            self.run_publish()
        self.assertEqual(self.writes(), [])

    def test_copied_marker_with_different_tree_is_refused(self):
        self.run_publish()
        previous = self.api.refs[BRANCH]
        flat = self.tree()
        flat[DIRECTORY + "avmigrate.py"] = ("100644", self.api.add_blob(b"human engine"))
        self.api.refs[BRANCH] = self.api.add_commit(
            self.api.make_tree(flat), [previous], self.api.commits[previous]["message"],
        )
        self.api.calls.clear()
        with self.assertRaisesRegex(PublishError, "human-edited"):
            self.run_publish()
        self.assertEqual(self.writes(), [])

    def test_spoofed_marker_cannot_hide_outside_scope_changes(self):
        self.run_publish()
        previous = self.api.refs[BRANCH]
        flat = self.tree()
        flat["README.md"] = ("100644", self.api.add_blob(b"human repository change"))
        tree = self.api.make_tree(flat)
        message = (
            "AVMigrate gist dependency update\n\n"
            f"avmigrate-gist:v1 source={self.base} tree={tree} diff={_digest([])}"
        )
        self.api.refs[BRANCH] = self.api.add_commit(tree, [previous], message)
        self.api.calls.clear()
        with self.assertRaisesRegex(PublishError, "human-edited"):
            self.run_publish()
        self.assertEqual(self.writes(), [])

    def test_marker_diff_binding_is_verified(self):
        self.run_publish()
        head = self.api.refs[BRANCH]
        commit = self.api.commits[head]
        marker = MARKER.fullmatch(commit["message"])
        commit["message"] = commit["message"][:-64] + ("0" * 64)
        self.assertNotEqual(marker.group(3), "0" * 64)
        self.api.calls.clear()
        with self.assertRaisesRegex(PublishError, "human-edited"):
            self.run_publish()
        self.assertEqual(self.writes(), [])

    def test_all_intervening_commits_are_verified(self):
        self.run_publish()
        first = self.api.refs[BRANCH]
        self.revision = "b" * 40
        self.engine = b"second\n"
        self.set_candidate()
        self.run_publish()
        self.api.commits[first]["message"] = "Human edit hidden behind later automated commit"
        self.api.calls.clear()
        with self.assertRaisesRegex(PublishError, "human-edited"):
            self.run_publish()
        self.assertEqual(self.writes(), [])

    def test_ref_update_race_does_not_force_or_create_pr(self):
        self.run_publish()
        self.api.fail_ref_update = True
        self.revision = "b" * 40
        self.engine = b"raced revision\n"
        self.set_candidate()
        previous = self.api.refs[BRANCH]
        with self.assertRaisesRegex(PublishError, "concurrently"):
            self.run_publish()
        self.assertEqual(self.api.refs[BRANCH], previous)
        self.assertEqual(len(self.api.pulls), 1)

    def test_base_race_after_commit_prevents_ref_or_pr_write(self):
        original = self.api.request

        def request(method, path, data=None):
            value = original(method, path, data)
            if method == "POST" and path.endswith("/git/commits"):
                self.api.refs["main"] = self.api.add_commit(
                    self.api.commits[self.base]["tree"]["sha"], [self.base], "concurrent base",
                )
            return value

        self.api.request = request
        with self.assertRaisesRegex(PublishError, "Base branch advanced"):
            self.run_publish()
        self.assertNotIn(BRANCH, self.api.refs)
        self.assertEqual(self.api.pulls, [])

    def test_ref_race_before_create_is_detected(self):
        original = self.api.request

        def request(method, path, data=None):
            value = original(method, path, data)
            if method == "POST" and path.endswith("/git/commits"):
                self.api.refs[BRANCH] = self.base
            return value

        self.api.request = request
        with self.assertRaisesRegex(PublishError, "concurrently"):
            self.run_publish()
        self.assertEqual(self.api.refs[BRANCH], self.base)
        self.assertEqual(self.api.pulls, [])

    def test_non_draft_or_closed_pr_not_overwritten(self):
        for change in ({"draft": False}, {"state": "closed"}, {"base": {"ref": "other"}}):
            with self.subTest(change=change):
                self.setUp()
                self.run_publish()
                self.api.pulls[0].update(change)
                self.api.calls.clear()
                with self.assertRaisesRegex(PublishError, "expected open draft"):
                    self.run_publish()
                self.assertEqual(self.writes(), [])

    def test_invalid_inputs_make_no_fetch_or_api_requests(self):
        cases = [
            {"revision": "main"}, {"revision": "../engine"}, {"revision": "A" * 40},
            {"sha256": "../hash"}, {"sha256": "f" * 63}, {"sha256": None},
            {"base_commit": "HEAD"}, {"repository": "owner/repo/../other"},
            {"repository": "owner/.."}, {"base_branch": "../main"},
            {"base_branch": "main.lock/sub"}, {"base_branch": "x\nmain"},
            {"base_branch": BRANCH},
        ]
        for args in cases:
            with self.subTest(args=args), self.assertRaises(PublishError):
                self.run_publish(**args)
        self.assertEqual(self.api.calls, [])
        self.fetcher.assert_not_called()

    def test_hash_and_provenance_mismatches_make_no_api_requests(self):
        for change in (
            {"avmigrate.py": b"not tested"},
            {"provenance.json": b"invalid json"},
            {"provenance.json": b'{"git_revision":"wrong","sha256":"wrong","version":"1.2.3"}'},
            {"provenance.json": b'{"version":"1.0.0","version":"2.0.0"}'},
        ):
            with self.subTest(change=change):
                self.set_candidate()
                self.files.update(change)
                with self.assertRaises(PublishError):
                    self.run_publish()
        self.assertEqual(self.api.calls, [])

    def test_invalid_paths_types_and_oversize_make_no_api_requests(self):
        cases = [
            {"../avmigrate.py": b"evil"}, {"vendor/avmigrate/avmigrate.py": b"evil"},
            {"extra.txt": b"extra"}, {"avmigrate.py": "not bytes"},
            {"avmigrate.py": b""}, {"upstream-license.txt": b"x" * (MAX_FILE_BYTES + 1)},
            {"upstream-license.txt": b"x" * MAX_FILE_BYTES,
             "avmigrate.py": b"x" * MAX_FILE_BYTES},
        ]
        for change in cases:
            with self.subTest(change=list(change)):
                self.set_candidate()
                self.files.update(change)
                with self.assertRaises(PublishError):
                    self.run_publish()
        self.assertEqual(MAX_TOTAL_BYTES, 2 * MAX_FILE_BYTES)
        self.assertEqual(self.api.calls, [])

    def test_untrusted_provenance_metadata_never_appears_in_pr_body(self):
        provenance = json.loads(self.files["provenance.json"])
        provenance["license_status"] = "@someone SECRET https://evil.invalid"
        provenance["author"] = "@someone SECRET"
        self.files["provenance.json"] = json.dumps(provenance).encode()
        self.run_publish()
        self.assertNotIn("SECRET", self.api.pulls[0]["body"])
        self.assertNotIn("evil.invalid", self.api.pulls[0]["body"])
        content = self.api.blobs[self.tree()[DIRECTORY + "provenance.json"][1]]
        self.assertEqual(content, self.files["provenance.json"])

    def test_version_is_safe_semver_before_any_api_requests(self):
        for version in ("1.2.3\n@mention", "1.2.3`", "1.2", "01.2.3", "1.2.3-01", {}, None):
            with self.subTest(version=version):
                self.set_candidate(version=version)
                with self.assertRaisesRegex(PublishError, "semantic version"):
                    self.run_publish()
        self.assertEqual(self.api.calls, [])

    def test_symlink_or_submodule_source_is_not_replaced(self):
        for mode in ("120000", "160000"):
            with self.subTest(mode=mode):
                flat = self.api.flatten(self.api.commits[self.base]["tree"]["sha"])
                flat[DIRECTORY + "avmigrate.py"] = (mode, self.api.add_blob(b"target"))
                self.base = self.api.add_commit(self.api.make_tree(flat), [], "unsafe source")
                self.api.refs["main"] = self.base
                self.api.calls.clear()
                with self.assertRaises(PublishError):
                    self.run_publish()
                self.assertEqual(self.writes(), [])


if __name__ == "__main__":
    unittest.main()
