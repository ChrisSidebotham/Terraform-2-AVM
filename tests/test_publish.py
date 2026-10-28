import base64
import copy
import hashlib
import json
from pathlib import Path
import shutil
import unittest
from unittest import mock
import urllib.parse
import uuid

from terraform_to_avm.publish import (
    APIError, GitHubAPI, MAX_FILE_BYTES, PublishError, _NoRedirect, publish,
)


def git_sha(kind, content):
    return hashlib.sha1(kind.encode() + b" " + str(len(content)).encode() + b"\0" + content).hexdigest()


class MemoryGitHub:
    """Content-addressed Git model exercising the actual publisher REST calls."""

    def __init__(self, source_files=None):
        self.calls = []
        self.blobs = {}
        self.trees = {}
        self.commits = {}
        self.refs = {}
        self.pulls = []
        self.fail_ref_update = False
        files = source_files if source_files is not None else {"infra/main.tf": b"old\n"}
        self.source = self.add_commit(self.make_tree({
            path: ("100644", self.add_blob(content)) for path, content in files.items()
        }), [], "Source")
        self.refs["main"] = self.source

    def add_blob(self, content):
        sha = git_sha("blob", content)
        self.blobs[sha] = content
        return sha

    def make_tree(self, flat):
        children = {}
        for path, entry in flat.items():
            first, separator, rest = path.partition("/")
            if separator:
                children.setdefault(first, {})[rest] = entry
            else:
                children[first] = entry
        entries = []
        for name, value in sorted(children.items()):
            if isinstance(value, dict):
                mode, sha, kind = "040000", self.make_tree(value), "tree"
            else:
                mode, sha = value
                kind = "commit" if mode == "160000" else "blob"
            entries.append({"path": name, "mode": mode, "sha": sha, "type": kind})
        sha = git_sha("tree", json.dumps(entries, sort_keys=True).encode())
        self.trees[sha] = {"sha": sha, "tree": entries, "truncated": False}
        return sha

    def flatten(self, sha, prefix=""):
        flat = {}
        for entry in self.trees[sha]["tree"]:
            path = prefix + entry["path"]
            if entry["type"] == "tree":
                flat.update(self.flatten(entry["sha"], path + "/"))
            else:
                flat[path] = (entry["mode"], entry["sha"])
        return flat

    def add_commit(self, tree, parents, message):
        sha = git_sha("commit", json.dumps([tree, parents, message]).encode())
        self.commits[sha] = {
            "sha": sha, "tree": {"sha": tree},
            "parents": [{"sha": parent} for parent in parents], "message": message,
        }
        return sha

    def request(self, method, path, data=None):
        self.calls.append((method, path, copy.deepcopy(data)))
        assert path.startswith("/repos/owner/repo/")
        path = path[len("/repos/owner/repo"):]
        if method == "GET" and path.startswith("/git/ref/heads/"):
            branch = urllib.parse.unquote(path[len("/git/ref/heads/"):])
            if branch not in self.refs:
                raise APIError(404)
            return {"object": {"sha": self.refs[branch]}}
        if method == "GET" and path.startswith("/git/commits/"):
            return copy.deepcopy(self.commits[path.rsplit("/", 1)[-1]])
        if method == "GET" and path.startswith("/git/trees/"):
            return copy.deepcopy(self.trees[path.rsplit("/", 1)[-1]])
        if method == "GET" and path.startswith("/git/blobs/"):
            content = self.blobs[path.rsplit("/", 1)[-1]]
            return {"size": len(content), "encoding": "base64",
                    "content": base64.b64encode(content).decode()}
        if method == "GET" and path.startswith("/pulls?"):
            return copy.deepcopy(self.pulls)
        if method == "POST" and path == "/git/blobs":
            return {"sha": self.add_blob(base64.b64decode(data["content"]))}
        if method == "POST" and path == "/git/trees":
            flat = self.flatten(data["base_tree"])
            for entry in data["tree"]:
                if entry["sha"] is None:
                    del flat[entry["path"]]
                else:
                    flat[entry["path"]] = (entry["mode"], entry["sha"])
            return {"sha": self.make_tree(flat)}
        if method == "POST" and path == "/git/commits":
            return {"sha": self.add_commit(data["tree"], data["parents"], data["message"])}
        if method == "POST" and path == "/git/refs":
            branch = data["ref"][len("refs/heads/"):]
            if branch in self.refs:
                raise APIError(422)
            self.refs[branch] = data["sha"]
            return {}
        if method == "PATCH" and path.startswith("/git/refs/heads/"):
            self.assert_nonforce(data)
            branch = urllib.parse.unquote(path[len("/git/refs/heads/"):])
            if self.fail_ref_update or self.commits[data["sha"]]["parents"][0]["sha"] != self.refs[branch]:
                raise APIError(422)
            self.refs[branch] = data["sha"]
            return {}
        if method == "POST" and path == "/pulls":
            pr = {
                "number": 7, "state": "open", "draft": data["draft"], "merged_at": None,
                "head": {"ref": data["head"], "repo": {"full_name": "owner/repo"}},
                "base": {"ref": data["base"]}, "title": data["title"], "body": data["body"],
            }
            self.pulls.append(pr)
            return copy.deepcopy(pr)
        if method == "PATCH" and path == "/pulls/7":
            self.pulls[0].update(data)
            return copy.deepcopy(self.pulls[0])
        raise AssertionError((method, path, data))

    @staticmethod
    def assert_nonforce(data):
        assert data["force"] is False


class PublishTests(unittest.TestCase):
    def setUp(self):
        # All test artifacts stay under the project; never use system temp paths.
        self.directory = Path(__file__).resolve().parent / (".publish-fixture-" + uuid.uuid4().hex)
        self.directory.mkdir()
        self.addCleanup(shutil.rmtree, self.directory)
        self.api = MemoryGitHub()
        self.write_bundle()

    def write_bundle(self, root="infra", content="new\n", files=None, source=None):
        source = source or self.api.source
        if files is None:
            path = (root + "/" if root != "." else "") + "main.tf"
            files = [{"path": path, "before_sha256": hashlib.sha256(b"old\n").hexdigest(),
                      "content": content}]
        self.changes = {"schema_version": 1, "source_commit": source, "selected_root": root, "files": files}
        self.result = {
            "schema_version": 1, "source_commit": source, "selected_root": root,
            "engine": {"version": "untrusted-version"}, "stages": {},
            "findings": [{"message": "SECRET_UNTRUSTED_FINDING\n@someone"}],
            "changed_files": [item["path"] for item in files],
        }
        self.save()

    def save(self):
        (self.directory / "changes.json").write_text(json.dumps(self.changes))
        (self.directory / "result.json").write_text(json.dumps(self.result))

    def run_publish(self):
        return publish(str(self.directory), "owner/repo", "main", self.api)

    def writes(self):
        return [call for call in self.api.calls if call[0] != "GET"]

    def test_creates_only_draft_and_never_renders_untrusted_metadata(self):
        result = self.run_publish()
        self.assertEqual(result["status"], "published")
        self.assertTrue(self.api.pulls[0]["draft"])
        self.assertNotIn("SECRET", self.api.pulls[0]["body"])
        self.assertNotIn("untrusted-version", self.api.pulls[0]["body"])
        self.assertIn("state review", self.api.pulls[0]["body"])
        self.assertEqual(len(self.api.pulls), 1)
        self.assertFalse(any("/merge" in path for _, path, _ in self.api.calls))

    def test_pr_summary_uses_only_status_enums_counts_and_safe_source(self):
        private = "PRIVATE_VALUE\n@someone\n[bad](https://evil.invalid)"
        self.result["stages"] = {
            "intake": {"status": "passed", "output": private},
            "plan_repair": {"status": "blocked"},
            "apply_fixes": {"status": "failed"},
            "validate": {"status": private},
            "promote": {"status": private},
        }
        self.result["static_validation"] = {"status": "failed", "reason": private}
        self.result["live_plan"] = {"status": "passed", "reason": private}
        self.result["deployment_readiness"] = "passed"
        self.result["findings"] = [
            {"severity": "error", "message": private, "file": private},
            {"severity": "warning", "resource": private},
            {"severity": "info", "message": private},
            {"severity": private},
            {"severity": {"unsafe": private}},
        ]
        self.result["state_migration_actions"] = [
            {"kind": "compatible_address_move", "source": private, "destination": private},
            {"kind": "cross_type_adoption", "resource_id_status": "missing", "id": private},
            {"kind": "cross_type_adoption", "resource_id_status": "present", "id": private},
            {"kind": "unknown_destination", "source": private},
            {"kind": "ambiguous_absorbed_destination", "destination": private},
            {"kind": {"unsafe": private}},
        ]
        self.save()
        self.run_publish()
        body = self.api.pulls[0]["body"]
        for expected in (
            "intake: **passed**", "plan_repair: **blocked**", "apply_fixes: **failed**",
            "validate: **not_assessed**", "promote: **passed**",
            "Static validation: **failed**", "Live plan: **not_assessed**",
            "Deployment readiness: **blocked**", "not deployment proof",
            "Errors: 1. Warnings: 1. Information: 1. Unclassified: 2.",
            "Compatible address moves: 1. Cross-type adoption: 2. Unknown/ambiguous destinations: 3.",
            "Missing/unchecked resource ID blockers: 1.", "lifecycle changes", "unmapped arguments",
            "missing resource IDs block adoption", "generated state commands were executed",
            self.api.source,
        ):
            self.assertIn(expected, body)
        for forbidden in ("PRIVATE_VALUE", "@someone", "evil.invalid", "untrusted-version",
                          "infra/main.tf", "Live plan: **passed**", "Deployment readiness: **passed**"):
            self.assertNotIn(forbidden, body)

    def test_malformed_optional_metadata_is_not_rendered_or_executed(self):
        self.result["findings"] = {"run": "do-not-run"}
        self.result["state_migration_actions"] = "do-not-run"
        self.result["static_validation"] = ["passed", "do-not-run"]
        self.result["stages"] = {name: ["passed", "do-not-run"]
                                 for name in ("intake", "plan_repair", "apply_fixes", "validate")}
        self.save()
        self.run_publish()
        body = self.api.pulls[0]["body"]
        self.assertNotIn("do-not-run", body)
        self.assertIn("Static validation: **not_assessed**", body)

    def test_identical_repeat_updates_same_pr_without_git_writes(self):
        first = self.run_publish()
        count = len(self.api.commits)
        self.api.calls.clear()
        second = self.run_publish()
        self.assertEqual(second["status"], "unchanged")
        self.assertEqual(second["pull_request"], first["pull_request"])
        self.assertEqual(len(self.api.commits), count)
        self.assertEqual([(method, path) for method, path, _ in self.writes()],
                         [("PATCH", "/repos/owner/repo/pulls/7")])

    def test_promotion_outcome_is_persisted_without_changing_other_fields(self):
        self.result["deployment_readiness"] = "blocked"
        self.result["live_plan"] = {"status": "not_assessed"}
        self.result["stages"]["validate"] = {"status": "failed"}
        self.save()
        self.run_publish()
        result = json.loads((self.directory / "result.json").read_text())
        self.assertEqual(result["stages"]["promote"]["status"], "passed")
        self.assertEqual(result["stages"]["promote"]["outcome"], "published")
        self.assertEqual(result["stages"]["promote"]["pull_request"], 7)
        self.assertEqual(result["stages"]["validate"], {"status": "failed"})
        self.assertEqual(result["deployment_readiness"], "blocked")
        self.assertEqual(result["live_plan"], {"status": "not_assessed"})
        self.assertNotIn("SECRET", json.dumps(result["stages"]["promote"]))
        self.run_publish()
        result = json.loads((self.directory / "result.json").read_text())
        self.assertEqual(result["stages"]["promote"]["outcome"], "unchanged")
        self.assertEqual(list(self.directory.glob(".result-publish-*")), [])

    def test_verified_automated_updates_parent_existing_head(self):
        first = self.run_publish()
        old_head = self.api.refs[first["branch"]]
        self.write_bundle(content="second\n")
        second = self.run_publish()
        new_head = self.api.refs[first["branch"]]
        self.assertEqual(second["status"], "updated")
        self.assertEqual(self.api.commits[new_head]["parents"], [{"sha": old_head}])
        self.assertEqual(len(self.api.pulls), 1)
        self.assertEqual(self.run_publish()["status"], "unchanged")

    def test_human_commit_refused_even_when_resulting_tree_identical(self):
        first = self.run_publish()
        head = self.api.refs[first["branch"]]
        self.api.refs[first["branch"]] = self.api.add_commit(
            self.api.commits[head]["tree"]["sha"], [head], "Human edit")
        self.api.calls.clear()
        with self.assertRaisesRegex(PublishError, "human-edited"):
            self.run_publish()
        self.assertEqual(self.writes(), [])

    def test_human_commit_with_copied_marker_but_changed_tree_refused(self):
        first = self.run_publish()
        head = self.api.refs[first["branch"]]
        flat = self.api.flatten(self.api.commits[head]["tree"]["sha"])
        flat["infra/main.tf"] = ("100644", self.api.add_blob(b"human content"))
        self.api.refs[first["branch"]] = self.api.add_commit(
            self.api.make_tree(flat), [head], self.api.commits[head]["message"])
        with self.assertRaisesRegex(PublishError, "ownership"):
            self.run_publish()

    def test_human_commit_between_automated_commits_is_not_skipped(self):
        first = self.run_publish()
        head = self.api.refs[first["branch"]]
        original = self.api.commits[head]
        human = self.api.add_commit(original["tree"]["sha"], [head], "Human edit")
        forged = self.api.add_commit(original["tree"]["sha"], [human], original["message"])
        self.api.refs[first["branch"]] = forged
        with self.assertRaises(PublishError):
            self.run_publish()

    def test_tampered_diff_marker_refused(self):
        first = self.run_publish()
        head = self.api.refs[first["branch"]]
        self.api.commits[head]["message"] = self.api.commits[head]["message"][:-64] + "0" * 64
        with self.assertRaisesRegex(PublishError, "diff"):
            self.run_publish()

    def test_existing_unowned_branch_at_source_refused(self):
        branch = "terraform-to-avm/" + hashlib.sha256(b"infra").hexdigest()
        self.api.refs[branch] = self.api.source
        with self.assertRaisesRegex(PublishError, "ownership"):
            self.run_publish()
        self.assertEqual(self.writes(), [])

    def test_base_advance_refused_without_writes(self):
        self.api.refs["main"] = self.api.add_commit(
            self.api.commits[self.api.source]["tree"]["sha"], [self.api.source], "Base advanced")
        with self.assertRaisesRegex(PublishError, "regenerate"):
            self.run_publish()
        self.assertEqual(self.writes(), [])

    def test_base_advance_during_validation_refused_without_writes(self):
        original = self.api.request
        reads = 0

        def request(method, path, data=None):
            nonlocal reads
            if method == "GET" and path.endswith("/git/ref/heads/main"):
                reads += 1
                if reads == 2:
                    return {"object": {"sha": "f" * 40}}
            return original(method, path, data)

        self.api.request = request
        with self.assertRaisesRegex(PublishError, "advanced"):
            self.run_publish()
        self.assertEqual(self.writes(), [])

    def test_stale_owned_branch_refused_after_regeneration(self):
        self.run_publish()
        self.api.source = self.api.add_commit(
            self.api.commits[self.api.source]["tree"]["sha"], [self.api.source], "Base advanced")
        self.api.refs["main"] = self.api.source
        self.write_bundle()
        self.api.calls.clear()
        with self.assertRaisesRegex(PublishError, "stale"):
            self.run_publish()
        self.assertEqual(self.writes(), [])

    def test_before_hash_mismatch_refused_without_writes(self):
        self.changes["files"][0]["before_sha256"] = "0" * 64
        self.save()
        with self.assertRaisesRegex(PublishError, "before-content"):
            self.run_publish()
        self.assertEqual(self.writes(), [])

    def test_no_meaningful_tf_diff_creates_nothing(self):
        for files in (
            [],
            [{"path": "infra/AVM-MIGRATION-CHANGELOG.md", "before_sha256": None, "content": "notes"}],
            [{"path": "infra/main.tf", "before_sha256": hashlib.sha256(b"old\n").hexdigest(),
              "content": "old\n"},
             {"path": "infra/AVM-MIGRATION-CHANGELOG.md", "before_sha256": None, "content": "notes"}],
        ):
            with self.subTest(files=files):
                self.write_bundle(files=files)
                self.assertEqual(self.run_publish(), {"status": "no_changes"})
                self.assertEqual(self.writes(), [])
                result = json.loads((self.directory / "result.json").read_text())
                self.assertEqual(result["stages"]["promote"]["status"], "not_assessed")

    def test_root_dot_is_supported(self):
        self.api = MemoryGitHub({"main.tf": b"old\n"})
        self.write_bundle(root=".")
        self.assertEqual(self.run_publish()["status"], "published")

    def test_all_auxiliary_files_and_deletion_supported(self):
        self.api = MemoryGitHub({"infra/main.tf": b"old\n", "infra/delete.tf": b"remove\n"})
        files = [{"path": "infra/main.tf", "before_sha256": hashlib.sha256(b"old\n").hexdigest(),
                  "content": "new\n"},
                 {"path": "infra/delete.tf", "before_sha256": hashlib.sha256(b"remove\n").hexdigest(),
                  "content": None}]
        for name in (".terraform.lock.hcl", "AVM-MIGRATION-CHANGELOG.md", "avm-state-migration.sh",
                     "avm-state-import.tf.disabled", "terraform-state-importer.config.yaml"):
            files.append({"path": "infra/" + name, "before_sha256": None, "content": "text\n"})
        self.write_bundle(files=files)
        result = self.run_publish()
        flat = self.api.flatten(self.api.commits[self.api.refs[result["branch"]]]["tree"]["sha"])
        self.assertNotIn("infra/delete.tf", flat)
        self.assertEqual(len(flat), 6)

    def test_unsafe_paths_refused_before_network(self):
        for path in (
            "../main.tf", "/infra/main.tf", "infra/../main.tf", "infra/nested/main.tf",
            "infra/.git/main.tf", ".git/config.tf", "infra/terraform.tfvars",
            "infra/main.tfvars.json", "infra/main.tf\n@someone", "infra\\main.tf",
            "infra//main.tf", "infra/.hidden.tf", "infra/main.tf/child.tf",
            ".github/main.tf", ".terraform/main.tf", ".GIT/main.tf",
        ):
            with self.subTest(path=path):
                self.write_bundle(files=[{"path": path, "before_sha256": None, "content": "new"}])
                with self.assertRaises(PublishError):
                    self.run_publish()
                self.assertEqual(self.api.calls, [])

    def test_unsafe_roots_duplicates_and_schema_refused(self):
        mutations = [
            lambda: self.changes.update(selected_root="../infra"),
            lambda: self.changes.update(selected_root=".git"),
            lambda: self.changes.update(selected_root=".github"),
            lambda: self.changes.update(selected_root=".terraform"),
            lambda: self.changes.update(schema_version=True),
            lambda: self.changes.update(source_commit="main"),
            lambda: self.changes.update(extra="untrusted"),
            lambda: self.result.update(changed_files=[]),
            lambda: self.result.update(stages=[]),
            lambda: self.changes["files"].append(copy.deepcopy(self.changes["files"][0])),
            lambda: self.changes["files"][0].update(content={"execute": "unsafe"}),
            lambda: self.changes["files"][0].update(content="\x00"),
            lambda: self.changes["files"][0].update(content=None, before_sha256=None),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.write_bundle()
                mutate()
                self.save()
                with self.assertRaises(PublishError):
                    self.run_publish()
                self.assertEqual(self.api.calls, [])

    def test_file_count_and_content_limits(self):
        self.write_bundle(files=[{"path": f"infra/file{i}.tf", "before_sha256": None, "content": "x"}
                                 for i in range(101)])
        with self.assertRaisesRegex(PublishError, "100"):
            self.run_publish()
        self.write_bundle(content="x" * (MAX_FILE_BYTES + 1))
        with self.assertRaisesRegex(PublishError, "limits"):
            self.run_publish()
        self.write_bundle(files=[
            {"path": f"infra/file{i}.tf", "before_sha256": None, "content": "x" * MAX_FILE_BYTES}
            for i in range(5)
        ])
        with self.assertRaisesRegex(PublishError, "aggregate"):
            self.run_publish()
        self.assertEqual(self.api.calls, [])

    def test_source_unrelated_files_preserved_and_update_removes_old_automation(self):
        self.api = MemoryGitHub({"infra/main.tf": b"old\n", "other/untouched.txt": b"untouched"})
        first_files = [
            {"path": "infra/main.tf", "before_sha256": hashlib.sha256(b"old\n").hexdigest(), "content": "new\n"},
            {"path": "infra/extra.tf", "before_sha256": None, "content": "extra\n"},
        ]
        self.write_bundle(files=first_files)
        first = self.run_publish()
        self.write_bundle(content="second\n")
        self.run_publish()
        flat = self.api.flatten(self.api.commits[self.api.refs[first["branch"]]]["tree"]["sha"])
        self.assertNotIn("infra/extra.tf", flat)
        self.assertEqual(self.api.blobs[flat["other/untouched.txt"][1]], b"untouched")
        self.assertEqual(self.run_publish()["status"], "unchanged")

    def test_symlink_bundle_json_refused(self):
        target = self.directory / "real.json"
        (self.directory / "changes.json").rename(target)
        (self.directory / "changes.json").symlink_to(target)
        with self.assertRaises(PublishError):
            self.run_publish()
        self.assertEqual(self.api.calls, [])

    def test_duplicate_json_keys_refused(self):
        (self.directory / "changes.json").write_text('{"schema_version":1,"schema_version":1}')
        with self.assertRaisesRegex(PublishError, "duplicate"):
            self.run_publish()

    def test_source_file_symlink_refused(self):
        flat = self.api.flatten(self.api.commits[self.api.source]["tree"]["sha"])
        flat["infra/main.tf"] = ("120000", self.api.add_blob(b"old\n"))
        self.api.source = self.api.add_commit(self.api.make_tree(flat), [], "Symlink source")
        self.api.refs["main"] = self.api.source
        self.write_bundle()
        with self.assertRaisesRegex(PublishError, "symlink"):
            self.run_publish()
        self.assertEqual(self.writes(), [])

    def test_selected_root_symlink_refused(self):
        flat = {"infra": ("120000", self.api.add_blob(b"elsewhere"))}
        self.api.source = self.api.add_commit(self.api.make_tree(flat), [], "Symlink root")
        self.api.refs["main"] = self.api.source
        self.write_bundle()
        with self.assertRaisesRegex(PublishError, "symlink"):
            self.run_publish()

    def test_non_draft_or_closed_existing_pr_refused_before_writes(self):
        self.run_publish()
        for change in ({"draft": False}, {"state": "closed"}, {"base": {"ref": "other"}}):
            with self.subTest(change=change):
                saved = copy.deepcopy(self.api.pulls[0])
                self.api.pulls[0].update(change)
                self.api.calls.clear()
                with self.assertRaisesRegex(PublishError, "open draft"):
                    self.run_publish()
                self.assertEqual(self.writes(), [])
                self.api.pulls[0] = saved

    def test_concurrent_ref_change_never_force_updates(self):
        self.run_publish()
        self.write_bundle(content="second\n")
        self.api.fail_ref_update = True
        with self.assertRaisesRegex(PublishError, "concurrently"):
            self.run_publish()
        updates = [data for method, path, data in self.api.calls
                   if method == "PATCH" and "/git/refs/" in path]
        self.assertEqual(len(updates), 1)
        self.assertIs(updates[0]["force"], False)

    def test_token_not_sent_to_redirects(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.invalid"))
        with mock.patch("urllib.request.build_opener") as build:
            api = GitHubAPI("secret-test-token")
            response = build.return_value.open.return_value.__enter__.return_value
            response.read.return_value = b'{"ok":true}'
            self.assertEqual(api.request("GET", "/repos/owner/repo/git/ref/heads/main"), {"ok": True})
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, "https://api.github.com/repos/owner/repo/git/ref/heads/main")
            self.assertEqual(request.get_header("Authorization"), "Bearer " + api.token)


if __name__ == "__main__":
    unittest.main()
