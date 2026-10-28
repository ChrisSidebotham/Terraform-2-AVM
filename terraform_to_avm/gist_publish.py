"""Publish a re-fetched, tested gist revision without executing upstream code."""

import base64
import hashlib
import json
import os
import re
import urllib.parse

from .publish import APIError, GitHubAPI, PublishError, _Repository, _digest, _unique_object


BRANCH = "automation/avmigrate-gist"
DIRECTORY = "vendor/avmigrate/"
FILENAMES = {"avmigrate.py", "provenance.json", "upstream-license.txt"}
PATHS = {DIRECTORY + name for name in FILENAMES}
MAX_FILE_BYTES = 1024 * 1024
MAX_TOTAL_BYTES = 2 * MAX_FILE_BYTES
SHA = re.compile(r"[0-9a-f]{40}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
SEMVER = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)
MARKER = re.compile(
    r"AVMigrate gist dependency update\n\n"
    r"avmigrate-gist:v1 source=([0-9a-f]{40}) tree=([0-9a-f]{40}) "
    r"diff=([0-9a-f]{64})\Z"
)
OWNERSHIP_ERROR = (
    "Updater branch is human-edited, unrelated, or stale; review and remove "
    "automation/avmigrate-gist manually, then rerun the update. Nothing was overwritten."
)
BASE_ERROR = (
    "Base branch advanced; rerun compatibility tests from the current base commit. "
    "Review and remove any stale automation/avmigrate-gist branch manually before retrying."
)


def _validate_inputs(revision, sha256, repository, base_commit, base_branch):
    for value, pattern, message in (
        (revision, SHA, "Revision must be a full lowercase immutable Git SHA."),
        (sha256, HASH, "Tested SHA-256 must be a full lowercase content hash."),
        (base_commit, SHA, "Base commit must be a full lowercase Git SHA."),
    ):
        if not isinstance(value, str) or not pattern.fullmatch(value):
            raise PublishError(message)
    if not isinstance(repository, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}", repository
    ) or repository.split("/")[-1] in {".", ".."}:
        raise PublishError("Repository must have the form owner/repo.")
    if (
        not isinstance(base_branch, str) or not base_branch or len(base_branch) > 255
        or base_branch == BRANCH
        or any(token in base_branch for token in ("..", "@{", "\\", "~", "^", ":", "?", "*", "["))
        or any(ord(char) <= 32 or ord(char) >= 127 for char in base_branch)
        or any(not part or part.startswith(".") or part.endswith((".", ".lock"))
               for part in base_branch.split("/"))
    ):
        raise PublishError("Base branch is not a safe Git branch name.")


def _candidate(revision, tested_hash, fetcher):
    if fetcher is None:
        from .gist_update import download
        fetcher = download
    files = fetcher(revision)
    if (
        not isinstance(files, dict) or not {"avmigrate.py", "provenance.json"} <= files.keys()
        or not files.keys() <= FILENAMES
        or any(not isinstance(content, bytes) or len(content) > MAX_FILE_BYTES
               for content in files.values())
        or sum(len(content) for content in files.values()) > MAX_TOTAL_BYTES
        or not files["avmigrate.py"]
    ):
        raise PublishError("Fetched candidate must contain only bounded engine, provenance, and optional license bytes.")
    if hashlib.sha256(files["avmigrate.py"]).hexdigest() != tested_hash:
        raise PublishError("Re-fetched engine does not match the tested SHA-256; rerun compatibility tests.")
    try:
        provenance = json.loads(files["provenance.json"], object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError):
        raise PublishError("Fetched provenance is not valid JSON.") from None
    if (
        not isinstance(provenance, dict) or provenance.get("git_revision") != revision
        or provenance.get("sha256") != tested_hash
    ):
        raise PublishError("Fetched provenance does not match the tested revision and SHA-256.")
    version = provenance.get("version")
    if not isinstance(version, str) or len(version) > 128 or not SEMVER.fullmatch(version):
        raise PublishError("Fetched provenance must contain a safe semantic version.")
    return files, version


class _GistRepository(_Repository):
    def diff(self, before_tree, after_tree, prefix=""):
        """Inspect ordinary trees, including all changes outside the vendor directory."""
        if before_tree == after_tree:
            return []
        before, after = self.tree(before_tree), self.tree(after_tree)
        records = []
        for name in sorted(before.keys() | after.keys()):
            old, new = before.get(name), after.get(name)
            if old == new:
                continue
            if old and new and all(old.get(key) == new.get(key) for key in ("sha", "mode", "type")):
                continue
            if not name or name in {".", ".."} or "/" in name:
                raise PublishError(OWNERSHIP_ERROR)
            path = prefix + name
            if any(entry and entry.get("type") == "tree" for entry in (old, new)):
                if path not in {"vendor", "vendor/avmigrate"} or any(
                    entry and (entry.get("type") != "tree" or entry.get("mode") != "040000")
                    for entry in (old, new)
                ):
                    raise PublishError(OWNERSHIP_ERROR)
                records.extend(self.diff(
                    old["sha"] if old else None, new["sha"] if new else None, path + "/",
                ))
            else:
                if path not in PATHS:
                    raise PublishError(OWNERSHIP_ERROR)
                self.blob(old)
                self.blob(new)
                records.append({
                    "path": path,
                    "before": [old["mode"], old["sha"]] if old else None,
                    "after": [new["mode"], new["sha"]] if new else None,
                })
        return sorted(records, key=lambda record: record["path"])

    def verify_history(self, head, source):
        current, visited = head, set()
        while current != source:
            if current in visited or len(visited) >= 100:
                raise PublishError(OWNERSHIP_ERROR)
            visited.add(current)
            commit = self.commit(current)
            message = commit.get("message")
            marker = MARKER.fullmatch(message) if isinstance(message, str) else None
            if (
                not marker or marker.group(1) != source
                or marker.group(2) != commit["tree"]["sha"] or len(commit["parents"]) != 1
            ):
                raise PublishError(OWNERSHIP_ERROR)
            parent = commit["parents"][0]["sha"]
            actual = self.diff(self.commit(parent)["tree"]["sha"], commit["tree"]["sha"])
            if not actual or marker.group(3) != _digest(actual):
                raise PublishError(OWNERSHIP_ERROR)
            current = parent
        if not visited:
            raise PublishError(OWNERSHIP_ERROR)


def _body(revision, sha256, version, licensed):
    license_note = (
        "An upstream license file is included; review its terms and provenance."
        if licensed else
        "No upstream license file was supplied. Confirm upstream distribution permission; "
        "the repository license is not asserted over this artifact."
    )
    return (
        "Draft dependency update for the vendored AVMigrate gist.\n\n"
        f"- Version: `{version}`\n- Git revision: `{revision}`\n- SHA-256: `{sha256}`\n"
        f"- License: {license_note}\n\n"
        "Original upstream attribution and provenance are retained without asserting a new license.\n\n"
        "The isolated compatibility checks passed for this exact engine hash. These are repository "
        "compatibility checks, not the upstream engine's original tests. They do not establish "
        "cloud deployment safety, Terraform state correctness, or permission to distribute.\n\n"
        "Manual dependency, attribution, license, and diff review is required. The existing pinned "
        "engine remains in the base branch until a reviewed merge. No automatic merge is performed."
    )


def publish(revision, sha256, repository, base_commit, base_branch, api=None, fetcher=None):
    """Re-fetch the tested immutable revision and propose only its vendored files.

    ``fetcher(revision)`` returns a mapping of exact upstream filenames to bytes.
    The calling workflow, not an artifact or test log, authorizes successful tests.
    """
    _validate_inputs(revision, sha256, repository, base_commit, base_branch)
    files, version = _candidate(revision, sha256, fetcher)
    repo = _GistRepository(
        api if api is not None else GitHubAPI(os.environ.get("GITHUB_TOKEN")),
        repository, "vendor/avmigrate",
    )
    if repo.ref(base_branch) != base_commit:
        raise PublishError(BASE_ERROR)
    source_tree = repo.commit(base_commit)["tree"]["sha"]
    head = repo.ref(BRANCH, optional=True)
    if head is not None:
        repo.verify_history(head, base_commit)
    entries, desired = [], []
    for name in sorted(FILENAMES):
        path = DIRECTORY + name
        old = repo.source_file(source_tree, path)
        content = files.get(name)
        old_bytes = repo.blob(old)
        if content == old_bytes:
            continue
        mode = old["mode"] if old else "100644"
        blob_sha = (
            hashlib.sha1(b"blob " + str(len(content)).encode("ascii") + b"\0" + content).hexdigest()
            if content is not None else None
        )
        entries.append({"path": path, "mode": mode, "type": "blob", "sha": blob_sha})
        desired.append({
            "path": path, "before": [old["mode"], old["sha"]] if old else None,
            "after": [mode, blob_sha] if content is not None else None,
        })
    if not desired:
        return {"status": "no_changes"}
    query = urllib.parse.urlencode({
        "state": "open", "head": repository.split("/")[0] + ":" + BRANCH, "per_page": 100,
    })
    pulls = repo.call("GET", "/pulls?" + query)
    if not isinstance(pulls, list) or len(pulls) > 1:
        raise PublishError("Updater branch has ambiguous pull requests; review them manually.")
    pr = pulls[0] if pulls else None
    if pr is not None and (
        not isinstance(pr, dict) or head is None or pr.get("state") != "open"
        or pr.get("draft") is not True or pr.get("merged_at") is not None
        or pr.get("base", {}).get("ref") != base_branch
        or pr.get("head", {}).get("ref") != BRANCH
        or pr.get("head", {}).get("repo", {}).get("full_name", "").lower() != repository.lower()
        or type(pr.get("number")) is not int or pr["number"] <= 0
    ):
        raise PublishError("Existing updater pull request is not the expected open draft; review it manually.")
    parent = head or base_commit
    parent_tree = repo.commit(parent)["tree"]["sha"]
    identical = head is not None and repo.diff(source_tree, parent_tree) == desired
    if repo.ref(base_branch) != base_commit:
        raise PublishError(BASE_ERROR)
    if not identical:
        for entry in entries:
            if entry["sha"] is not None:
                content = files[entry["path"][len(DIRECTORY):]]
                blob = repo.call("POST", "/git/blobs", {
                    "encoding": "base64", "content": base64.b64encode(content).decode("ascii"),
                })
                if not isinstance(blob, dict) or blob.get("sha") != entry["sha"]:
                    raise PublishError("GitHub created an unexpected blob; publication stopped.")
        tree = repo.call("POST", "/git/trees", {"base_tree": source_tree, "tree": entries})
        tree_sha = tree.get("sha", "") if isinstance(tree, dict) else ""
        if not isinstance(tree_sha, str) or not SHA.fullmatch(tree_sha) or repo.diff(source_tree, tree_sha) != desired:
            raise PublishError("GitHub created an unexpected vendor tree; publication stopped.")
        message = (
            "AVMigrate gist dependency update\n\n"
            f"avmigrate-gist:v1 source={base_commit} tree={tree_sha} "
            f"diff={_digest(repo.diff(parent_tree, tree_sha))}"
        )
        commit = repo.call("POST", "/git/commits", {
            "tree": tree_sha, "parents": [parent], "message": message,
        })
        new_head = commit.get("sha", "") if isinstance(commit, dict) else ""
        if not isinstance(new_head, str) or not SHA.fullmatch(new_head):
            raise PublishError("GitHub returned an invalid new commit.")
        if repo.ref(base_branch) != base_commit:
            raise PublishError(BASE_ERROR)
        if repo.ref(BRANCH, optional=True) != head:
            raise PublishError("Updater branch changed concurrently; review it and rerun. No force update was attempted.")
        try:
            if head is None:
                repo.call("POST", "/git/refs", {"ref": "refs/heads/" + BRANCH, "sha": new_head})
            else:
                repo.call("PATCH", "/git/refs/heads/" + urllib.parse.quote(BRANCH, safe=""),
                          {"sha": new_head, "force": False})
        except APIError:
            raise PublishError(
                "Updater branch changed concurrently; review it and rerun. No force update was attempted."
            ) from None
        head = new_head
    if repo.ref(base_branch) != base_commit:
        raise PublishError(BASE_ERROR)
    if repo.ref(BRANCH) != head:
        raise PublishError("Updater branch changed concurrently; review it before publishing.")
    payload = {
        "title": "Draft: Update vendored AVMigrate to " + version,
        "body": _body(revision, sha256, version, "upstream-license.txt" in files),
    }
    if pr is None:
        payload.update({"head": BRANCH, "base": base_branch, "draft": True})
        pr = repo.call("POST", "/pulls", payload)
    elif any(pr.get(key) != value for key, value in payload.items()):
        repo.call("PATCH", "/pulls/" + str(pr["number"]), payload)
    if not isinstance(pr, dict) or type(pr.get("number")) is not int or pr["number"] <= 0:
        raise PublishError("GitHub did not return a valid draft pull request.")
    return {
        "status": "unchanged" if identical else ("updated" if parent != base_commit else "published"),
        "branch": BRANCH, "pull_request": pr["number"],
    }
