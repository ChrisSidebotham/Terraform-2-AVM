"""Publish an untrusted migration bundle as a draft, using only GitHub REST.

Repair branches are deliberately conservative: their base must still be the
bundle source, and every intervening commit must have a verified publisher
marker. If the base advances, regenerate the bundle and remove the old repair
branch (after reviewing it), rather than force-pushing over anybody's work.
"""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid


MAX_FILES = 100
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 4 * MAX_FILE_BYTES
MAX_JSON_BYTES = 8 * MAX_FILE_BYTES
SPECIAL_FILES = {
    ".terraform.lock.hcl",
    "AVM-MIGRATION-CHANGELOG.md",
    "avm-state-migration.sh",
    "avm-state-import.tf.disabled",
    "terraform-state-importer.config.yaml",
}
RESERVED_COMPONENTS = {".git", ".github", ".terraform", ".terragrunt-cache", ".hg", ".svn"}
SHA = re.compile(r"[0-9a-f]{40}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
SEGMENT = re.compile(r"[A-Za-z0-9_.-]+\Z")
MARKER = re.compile(
    r"Terraform to AVM automated draft\n\n"
    r"terraform-to-avm:v1 source=([0-9a-f]{40}) root=([0-9a-f]{64}) "
    r"tree=([0-9a-f]{40}) diff=([0-9a-f]{64})\Z"
)


class PublishError(Exception):
    """A safe, actionable error that contains no bundle or server values."""


class APIError(PublishError):
    def __init__(self, status):
        self.status = status
        super().__init__("GitHub REST request failed; check token permissions and repository access.")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward a bearer credential to a redirect target.
        return None


class GitHubAPI:
    """Small mockable JSON REST transport; no shell, SDK, or consumer code."""

    def __init__(self, token):
        if not token or "\r" in token or "\n" in token:
            raise PublishError("Set GITHUB_TOKEN to a token with contents and pull-request write access.")
        self.token = token
        self.opener = urllib.request.build_opener(_NoRedirect())

    def request(self, method, path, data=None):
        if not path.startswith("/repos/") or "\r" in path or "\n" in path:
            raise PublishError("Invalid GitHub REST resource.")
        encoded = None if data is None else json.dumps(data).encode("utf-8")
        request = urllib.request.Request(
            "https://api.github.com" + path,
            data=encoded,
            method=method,
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
                "User-Agent": "terraform-to-avm-draft-publisher",
            },
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                body = response.read(16 * MAX_FILE_BYTES + 1)
            if len(body) > 16 * MAX_FILE_BYTES:
                raise PublishError("GitHub REST response exceeded the safety limit.")
            return json.loads(body)
        except urllib.error.HTTPError as error:
            raise APIError(error.code) from None
        except (urllib.error.URLError, OSError, ValueError):
            raise PublishError("GitHub REST request failed; retry after checking connectivity.") from None


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()


def _bytes_hash(value):
    return hashlib.sha256(value).hexdigest()


def _valid_path(path):
    if not isinstance(path, str) or not path or len(path) > 1024:
        return False
    parts = path.split("/")
    return len(parts) <= 32 and all(
        SEGMENT.fullmatch(part)
        and part not in {".", ".."}
        and part.lower() not in RESERVED_COMPONENTS
        for part in parts
    )


def _allowed(path, root):
    if not _valid_path(path):
        return False
    parent, _, name = path.rpartition("/")
    if parent != ("" if root == "." else root):
        return False
    return name in SPECIAL_FILES or (name.endswith(".tf") and not name.startswith("."))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PublishError("Bundle JSON contains duplicate keys.")
        result[key] = value
    return result


def _read_json(path, limit):
    try:
        # O_NOFOLLOW also protects the final file from a symlink substitution.
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise PublishError("Bundle files must be regular files within the size limits.")
            content = stream.read(limit + 1)
        if len(content) > limit:
            raise PublishError("Bundle file exceeded the size limit.")
        return json.loads(content, object_pairs_hook=_unique_object)
    except PublishError:
        raise
    except (OSError, ValueError, UnicodeError, RecursionError):
        raise PublishError("Cannot read a valid, regular bundle JSON file.") from None


def _load_bundle(bundle_path):
    try:
        directory = Path(bundle_path)
        if not directory.is_absolute() or any(
            item.is_symlink() for item in (directory, *directory.parents)
        ) or not directory.is_dir():
            raise PublishError("Bundle must be an absolute directory without symlinks.")
        result = _read_json(directory / "result.json", 256 * 1024)
        changes = _read_json(directory / "changes.json", MAX_JSON_BYTES)
        if not isinstance(result, dict) or not isinstance(changes, dict):
            raise PublishError("Bundle documents must be JSON objects.")
        if type(result.get("schema_version")) is not int or result["schema_version"] != 1:
            raise PublishError("Unsupported result schema version.")
        if not isinstance(result.get("stages"), dict):
            raise PublishError("Result stages must be a JSON object.")
        if set(changes) != {"schema_version", "source_commit", "selected_root", "files"}:
            raise PublishError("Invalid changes schema.")
        if type(changes["schema_version"]) is not int or changes["schema_version"] != 1:
            raise PublishError("Unsupported changes schema version.")
        source, root, files = (changes[key] for key in ("source_commit", "selected_root", "files"))
        if not isinstance(source, str) or not SHA.fullmatch(source):
            raise PublishError("Bundle source must be a full lowercase Git commit SHA.")
        if root != "." and not _valid_path(root):
            raise PublishError("Bundle selected root is not a safe repository-relative path.")
        if result.get("source_commit") != source or result.get("selected_root") != root:
            raise PublishError("Bundle documents disagree about the source or selected root.")
        if not isinstance(files, list) or len(files) > MAX_FILES:
            raise PublishError("Bundle must contain no more than 100 changed files.")
        seen, total = set(), 0
        for item in files:
            if not isinstance(item, dict) or set(item) != {"path", "before_sha256", "content"}:
                raise PublishError("Invalid bundle file record.")
            path, before, content = (item[key] for key in ("path", "before_sha256", "content"))
            if not _allowed(path, root) or path in seen:
                raise PublishError("Bundle paths must be unique allowed immediate files of the selected root.")
            seen.add(path)
            if before is not None and (not isinstance(before, str) or not HASH.fullmatch(before)):
                raise PublishError("Invalid before-content hash.")
            if content is None:
                if before is None:
                    raise PublishError("A deletion must identify an existing source file.")
            elif isinstance(content, str):
                size = len(content.encode("utf-8"))
                if size > MAX_FILE_BYTES or "\x00" in content:
                    raise PublishError("Bundle file content exceeds the UTF-8 text safety limits.")
                total += size
            else:
                raise PublishError("Bundle content must be UTF-8 text or a deletion.")
        changed_files = result.get("changed_files")
        if not isinstance(changed_files, list) or any(
            not isinstance(path, str) for path in changed_files
        ) or len(changed_files) != len(seen) or set(changed_files) != seen:
            raise PublishError("Bundle documents disagree about changed files.")
        if total > MAX_TOTAL_BYTES:
            raise PublishError("Bundle content exceeds the aggregate safety limit.")
        return source, root, files, result
    except (TypeError, UnicodeError, RecursionError):
        raise PublishError("Bundle contains invalid values.") from None


def _record(path, before_hash, after_hash, before_mode, after_mode):
    return {
        "path": path, "before_sha256": before_hash, "after_sha256": after_hash,
        "before_mode": before_mode, "after_mode": after_mode,
    }


def _finish(bundle_path, result, outcome):
    """Persist only generic promotion metadata, without following output links."""
    directory = Path(bundle_path)
    target = directory / "result.json"
    if _read_json(target, 256 * 1024) != result:
        raise PublishError("Bundle result changed during publication; inspect the draft and rerun.")
    updated = dict(result)
    updated["stages"] = dict(result["stages"])
    if outcome["status"] == "no_changes":
        promotion = {
            "status": "not_assessed",
            "reason": "No meaningful Terraform repair diff; no draft pull request published.",
        }
    else:
        promotion = {
            "status": "passed",
            "outcome": outcome["status"],
            "pull_request": outcome["pull_request"],
            "reason": "Draft pull request published; manual configuration and state review required.",
        }
    updated["stages"]["promote"] = promotion
    content = json.dumps(updated, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    if len(content.encode("utf-8")) > 256 * 1024:
        raise PublishError("Publication outcome exceeds result safety limits; inspect the draft manually.")
    scratch = directory / (".result-publish-" + uuid.uuid4().hex + ".json")
    try:
        fd = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        if target.is_symlink():
            raise PublishError("Bundle result became a symlink; inspect the draft and rerun.")
        os.replace(scratch, target)
    except OSError:
        raise PublishError("Cannot persist publication outcome; inspect the draft and rerun.") from None
    finally:
        try:
            scratch.unlink(missing_ok=True)
        except OSError:
            pass
    return outcome


def _safe_status(value):
    status = value.get("status") if isinstance(value, dict) else None
    return status if status in ("passed", "failed", "blocked", "not_assessed") else "not_assessed"


def _pr_body(result, effective, source):
    # Only fixed vocabulary, counts, and the validated SHA cross this boundary.
    severities = {"error": 0, "warning": 0, "info": 0, "unknown": 0}
    findings = result.get("findings", [])
    for item in findings if isinstance(findings, list) else []:
        severity = item.get("severity") if isinstance(item, dict) else None
        key = severity if isinstance(severity, str) and severity in severities else "unknown"
        severities[key] += 1
    counts = {"compatible_address_move": 0, "cross_type_adoption": 0, "unknown_or_ambiguous": 0}
    actions = result.get("state_migration_actions", [])
    missing_ids = 0
    for item in actions if isinstance(actions, list) else []:
        kind = item.get("kind") if isinstance(item, dict) else None
        key = kind if isinstance(kind, str) and kind in counts else "unknown_or_ambiguous"
        counts[key] += 1
        if kind == "cross_type_adoption" and item.get("resource_id_status") != "present":
            missing_ids += 1
    lines = [
        "Automated Terraform-to-AVM migration candidate.",
        "",
        f"Source commit: `{source}`.",
        f"Changed files: {len(effective)}. Terraform files: "
        f"{sum(item['path'].endswith('.tf') for item, _ in effective)}.",
        "",
        "### Reported conversion stages",
    ]
    for stage in ("intake", "plan_repair", "apply_fixes", "validate"):
        lines.append(f"- {stage}: **{_safe_status(result['stages'].get(stage))}**")
    lines.extend([
        "- promote: **passed** (draft publication only; not approval)",
        "",
        f"Static validation: **{_safe_status(result.get('static_validation'))}**.",
        "Live plan: **not_assessed**. Deployment readiness: **blocked**.",
        "Reported static validation is not deployment proof or an infrastructure safety guarantee.",
        "",
        "### Reported findings",
        f"Errors: {severities['error']}. Warnings: {severities['warning']}. "
        f"Information: {severities['info']}. Unclassified: {severities['unknown']}.",
        "",
        "### State migration review",
        f"Compatible address moves: {counts['compatible_address_move']}. "
        f"Cross-type adoption: {counts['cross_type_adoption']}. "
        f"Unknown/ambiguous destinations: {counts['unknown_or_ambiguous']}.",
        f"Missing/unchecked resource ID blockers: {missing_ids}.",
        "",
        "DRAFT — manual configuration and Terraform state review is required.",
        "- [ ] Review all advisory findings, lifecycle changes, and unmapped arguments.",
        "- [ ] Verify compatible move destinations against real Terraform state.",
        "- [ ] Review cross-type adoption separately; missing resource IDs block adoption.",
        "- [ ] Resolve unknown or ambiguous destinations and verify every required resource ID.",
        "- [ ] Obtain an authenticated, state-aware plan before any apply or merge.",
        "",
        "No Terraform apply, import, state modification, or generated state commands were executed "
        "by this publisher. Generated migration instructions are review-only.",
    ])
    return "\n".join(lines)


class _Repository:
    def __init__(self, api, repository, root):
        self.api = api
        self.prefix = "/repos/" + repository
        self.root = root
        self.trees = {}
        self.blobs = {}
        self.blob_bytes = 0
        self.commits = {}

    def call(self, method, path, data=None):
        return self.api.request(method, self.prefix + path, data)

    def ref(self, branch, optional=False):
        try:
            value = self.call("GET", "/git/ref/heads/" + urllib.parse.quote(branch, safe=""))
        except APIError as error:
            if optional and error.status == 404:
                return None
            raise
        if not isinstance(value, dict) or not SHA.fullmatch(value.get("object", {}).get("sha", "")):
            raise PublishError("GitHub returned an invalid Git reference.")
        return value["object"]["sha"]

    def commit(self, sha):
        if sha not in self.commits:
            value = self.call("GET", "/git/commits/" + sha)
            if not isinstance(value, dict) or value.get("sha") != sha:
                raise PublishError("GitHub returned an invalid Git commit.")
            tree = value.get("tree", {}).get("sha", "")
            parents = value.get("parents")
            if not SHA.fullmatch(tree) or not isinstance(parents, list) or any(
                not isinstance(parent, dict) or not SHA.fullmatch(parent.get("sha", ""))
                for parent in parents
            ):
                raise PublishError("GitHub returned invalid commit ancestry.")
            self.commits[sha] = value
        return self.commits[sha]

    def tree(self, sha):
        if sha is None:
            return {}
        if sha not in self.trees:
            value = self.call("GET", "/git/trees/" + sha)
            entries = value.get("tree") if isinstance(value, dict) else None
            if not isinstance(entries, list) or len(entries) > 100000 or value.get("truncated"):
                raise PublishError("Cannot safely inspect the repository tree.")
            parsed = {}
            for entry in entries:
                name = entry.get("path", "")
                if not isinstance(name, str) or "/" in name or name in parsed:
                    raise PublishError("GitHub returned an invalid Git tree.")
                if not SHA.fullmatch(entry.get("sha", "")):
                    raise PublishError("GitHub returned an invalid tree object.")
                parsed[name] = entry
            self.trees[sha] = parsed
        return self.trees[sha]

    def blob(self, entry):
        if entry is None:
            return None
        if entry.get("mode") not in {"100644", "100755"} or entry.get("type") != "blob":
            raise PublishError("Refusing symlinks, submodules, or non-regular changed files.")
        sha = entry["sha"]
        if sha not in self.blobs:
            value = self.call("GET", "/git/blobs/" + sha)
            try:
                if value.get("encoding") != "base64" or type(value.get("size")) is not int:
                    raise ValueError
                if not 0 <= value["size"] <= MAX_FILE_BYTES:
                    raise ValueError
                encoded = value["content"]
                if not isinstance(encoded, str) or len(encoded) > 2 * MAX_FILE_BYTES:
                    raise ValueError
                content = base64.b64decode("".join(encoded.split()), validate=True)
                if len(content) != value["size"]:
                    raise ValueError
                if hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest() != sha:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                raise PublishError("Cannot verify a source Git blob within safety limits.") from None
            self.blob_bytes += len(content)
            if self.blob_bytes > 4 * MAX_TOTAL_BYTES:
                raise PublishError("Repair history exceeds content safety limits; review and remove the branch manually.")
            self.blobs[sha] = content
        return self.blobs[sha]

    def source_file(self, tree_sha, path):
        parts = path.split("/")
        for part in parts[:-1]:
            entry = self.tree(tree_sha).get(part)
            if entry is None:
                return None
            if entry.get("mode") != "040000" or entry.get("type") != "tree":
                raise PublishError("Selected root traverses a symlink or non-directory Git entry.")
            tree_sha = entry["sha"]
        entry = self.tree(tree_sha).get(parts[-1])
        self.blob(entry)
        return entry

    def diff(self, before_tree, after_tree, prefix=""):
        if before_tree == after_tree:
            return []
        before, after = self.tree(before_tree), self.tree(after_tree)
        records = []
        for name in sorted(set(before) | set(after)):
            old, new = before.get(name), after.get(name)
            if old == new:
                continue
            if old and new and old.get("sha") == new.get("sha") and old.get("mode") == new.get("mode"):
                continue
            path = prefix + name
            directories = [entry for entry in (old, new) if entry and entry.get("type") == "tree"]
            if directories:
                if any(entry and (entry.get("type") != "tree" or entry.get("mode") != "040000")
                       for entry in (old, new)):
                    raise PublishError("Repair branch contains an unsafe file-type change.")
                if self.root == "." or not (path == self.root or self.root.startswith(path + "/")):
                    raise PublishError("Repair branch changes files outside the selected root.")
                records.extend(self.diff(old["sha"] if old else None, new["sha"] if new else None, path + "/"))
            else:
                if not _allowed(path, self.root):
                    raise PublishError("Repair branch changes files outside the allowed file list.")
                old_bytes, new_bytes = self.blob(old), self.blob(new)
                records.append(_record(
                    path, _bytes_hash(old_bytes) if old else None,
                    _bytes_hash(new_bytes) if new else None,
                    old["mode"] if old else None, new["mode"] if new else None,
                ))
            if len(records) > MAX_FILES:
                raise PublishError("Repair branch exceeds the changed-file safety limit.")
        return sorted(records, key=lambda record: record["path"])

    def verify_branch(self, head, source, root_hash):
        current, visited = head, set()
        while current != source:
            if current in visited or len(visited) >= 100:
                raise PublishError("Repair branch ancestry exceeds safety limits; review and remove it manually.")
            visited.add(current)
            commit = self.commit(current)
            marker = MARKER.fullmatch(commit.get("message", ""))
            if not marker or marker.group(1) != source or marker.group(2) != root_hash:
                raise PublishError(
                    "Repair branch is unrelated, human-edited, or stale. Review it, then remove the "
                    "repair branch and regenerate the bundle; no branch was overwritten."
                )
            if len(commit["parents"]) != 1 or marker.group(3) != commit["tree"]["sha"]:
                raise PublishError("Repair branch ownership cannot be verified; review and remove it manually.")
            parent = commit["parents"][0]["sha"]
            actual = self.diff(self.commit(parent)["tree"]["sha"], commit["tree"]["sha"])
            if not actual or marker.group(4) != _digest(actual):
                raise PublishError("Repair branch diff does not match its publisher marker; review it manually.")
            current = parent
        if not visited:
            raise PublishError("Existing repair branch has no publisher ownership; review and remove it manually.")


def publish(bundle_path, repository, base_branch, api=None):
    """Publish a validated bundle; injected ``api.request(method, path, data)`` is mockable."""
    source, root, files, result = _load_bundle(bundle_path)
    if not isinstance(repository, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", repository
    ) or repository.split("/")[-1] in {".", ".."}:
        raise PublishError("Repository must have the form owner/repo.")
    if not isinstance(base_branch, str) or not base_branch or len(base_branch) > 255 or any(
        token in base_branch for token in ("..", "@{", "\\", "~", "^", ":", "?", "*", "[")
    ) or any(ord(char) <= 32 or ord(char) == 127 for char in base_branch) or (
        base_branch.startswith(("/", ".")) or base_branch.endswith(("/", ".", ".lock"))
        or "//" in base_branch
    ):
        raise PublishError("Base branch is not a safe Git branch name.")
    if not any(item["path"].endswith(".tf") for item in files):
        return _finish(bundle_path, result, {"status": "no_changes"})
    repo = _Repository(api if api is not None else GitHubAPI(os.environ.get("GITHUB_TOKEN")), repository, root)
    if repo.ref(base_branch) != source:
        raise PublishError("Base branch advanced; regenerate the bundle from the current base commit.")
    source_tree = repo.commit(source)["tree"]["sha"]
    desired, effective, total = [], [], 0
    for item in files:
        old = repo.source_file(source_tree, item["path"])
        old_bytes = repo.blob(old)
        old_hash = _bytes_hash(old_bytes) if old else None
        if item["before_sha256"] != old_hash:
            raise PublishError("Bundle before-content hash does not match the source; regenerate the bundle.")
        new_bytes = item["content"].encode("utf-8") if item["content"] is not None else None
        total += len(old_bytes or b"")
        if total > MAX_TOTAL_BYTES:
            raise PublishError("Source contents exceed the aggregate safety limit.")
        if old_bytes == new_bytes:
            continue
        mode = old["mode"] if old else "100644"
        desired.append(_record(item["path"], old_hash, _bytes_hash(new_bytes) if new_bytes is not None else None,
                               old["mode"] if old else None, mode if new_bytes is not None else None))
        effective.append((item, mode))
    if not any(item["path"].endswith(".tf") for item, _ in effective):
        return _finish(bundle_path, result, {"status": "no_changes"})
    desired.sort(key=lambda record: record["path"])
    root_hash = hashlib.sha256(root.encode("utf-8")).hexdigest()
    branch = "terraform-to-avm/" + root_hash
    head = repo.ref(branch, optional=True)
    if head is not None:
        repo.verify_branch(head, source, root_hash)
    query = urllib.parse.urlencode({"state": "all", "head": repository.split("/")[0] + ":" + branch, "per_page": 100})
    pulls = repo.call("GET", "/pulls?" + query)
    if not isinstance(pulls, list) or len(pulls) > 1:
        raise PublishError("Repair branch has ambiguous pull requests; review them manually.")
    pr = pulls[0] if pulls else None
    if pr is not None and (
        head is None or pr.get("state") != "open" or pr.get("draft") is not True
        or pr.get("merged_at") is not None or pr.get("base", {}).get("ref") != base_branch
        or pr.get("head", {}).get("ref") != branch
        or pr.get("head", {}).get("repo", {}).get("full_name", "").lower() != repository.lower()
        or type(pr.get("number")) is not int or pr["number"] <= 0
    ):
        raise PublishError("Existing pull request is not the expected open draft; review it manually.")
    parent = head or source
    parent_tree = repo.commit(parent)["tree"]["sha"]
    identical = head is not None and repo.diff(source_tree, parent_tree) == desired
    if repo.ref(base_branch) != source:
        raise PublishError("Base branch advanced; regenerate the bundle before publishing.")
    if not identical:
        entries = []
        for item, mode in effective:
            blob_sha = None
            if item["content"] is not None:
                blob = repo.call("POST", "/git/blobs", {
                    "content": base64.b64encode(item["content"].encode("utf-8")).decode("ascii"),
                    "encoding": "base64",
                })
                blob_sha = blob.get("sha", "")
                if not SHA.fullmatch(blob_sha):
                    raise PublishError("GitHub returned an invalid new blob.")
            entries.append({"path": item["path"], "mode": mode, "type": "blob", "sha": blob_sha})
        tree = repo.call("POST", "/git/trees", {"base_tree": source_tree, "tree": entries})
        tree_sha = tree.get("sha", "")
        if not SHA.fullmatch(tree_sha) or repo.diff(source_tree, tree_sha) != desired:
            raise PublishError("GitHub created an unexpected content tree; publication stopped.")
        diff_digest = _digest(repo.diff(parent_tree, tree_sha))
        message = (
            "Terraform to AVM automated draft\n\n"
            f"terraform-to-avm:v1 source={source} root={root_hash} tree={tree_sha} diff={diff_digest}"
        )
        commit = repo.call("POST", "/git/commits", {"message": message, "tree": tree_sha, "parents": [parent]})
        new_head = commit.get("sha", "")
        if not SHA.fullmatch(new_head):
            raise PublishError("GitHub returned an invalid new commit.")
        try:
            if head is None:
                repo.call("POST", "/git/refs", {"ref": "refs/heads/" + branch, "sha": new_head})
            else:
                repo.call("PATCH", "/git/refs/heads/" + urllib.parse.quote(branch, safe=""),
                          {"sha": new_head, "force": False})
        except APIError:
            raise PublishError("Repair branch changed concurrently; review it and rerun. No force update was attempted.") from None
    payload = {"title": "Draft: Terraform to AVM migration", "body": _pr_body(result, effective, source)}
    if pr is None:
        payload.update({"head": branch, "base": base_branch, "draft": True})
        pr = repo.call("POST", "/pulls", payload)
    else:
        repo.call("PATCH", "/pulls/" + str(pr["number"]), payload)
    if not isinstance(pr, dict) or type(pr.get("number")) is not int:
        raise PublishError("GitHub did not return a valid draft pull request.")
    return _finish(bundle_path, result, {
        "status": "unchanged" if identical else ("updated" if head else "published"),
        "branch": branch, "pull_request": pr["number"],
    })


def main(argv=None):
    parser = argparse.ArgumentParser(description="Publish a validated migration bundle as a GitHub draft PR.")
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--base-branch", required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(publish(args.bundle, args.repository, args.base_branch), sort_keys=True))
    except PublishError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
