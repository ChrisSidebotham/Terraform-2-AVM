"""Discover an untrusted upstream candidate; never execute it or change the pin."""

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from .publish import PublishError, _NoRedirect

GIST = "0a3b2e9809a9831692c3bb2bff4c0a54"
AUTHOR = "ChrisSidebotham"
ORIGIN = f"https://gist.github.com/{AUTHOR}/{GIST}"
API = f"https://api.github.com/gists/{GIST}"
MAX_ARTIFACT = 1024 * 1024
MAX_METADATA = 4 * 1024 * 1024
MAX_LICENSE = 128 * 1024
REVISION = re.compile(r"[0-9a-f]{40}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
VERSION = re.compile(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?\Z")


class UpdateError(ValueError):
    """Safe diagnostics without arbitrary upstream content."""


def retrieve(url, limit):
    """Bounded anonymous HTTPS. Do not send publication credentials to the gist."""
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json" if url.startswith(API) else "text/plain",
        "User-Agent": "terraform-to-avm-compatibility",
    })
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=30) as response:
            data = response.read(limit + 1)
    except (OSError, urllib.error.URLError):
        raise UpdateError("Cannot retrieve upstream gist; check connectivity and API rate limits.") from None
    if len(data) > limit:
        raise UpdateError("Upstream response exceeds the size limit.")
    return data


def metadata(url, get):
    try:
        value = json.loads(get(url, MAX_METADATA))
        if not isinstance(value, dict) or value.get("id") != GIST or (
                value.get("owner", {}).get("login", "").lower() != AUTHOR.lower()):
            raise ValueError
        if not isinstance(value.get("files"), dict):
            raise ValueError
        return value
    except UpdateError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        raise UpdateError("Upstream gist metadata has an invalid origin or structure.") from None


def head_revision(value):
    try:
        revision = value["history"][0]["version"]
        if not isinstance(revision, str) or not REVISION.fullmatch(revision):
            raise ValueError
        return revision
    except (KeyError, IndexError, TypeError, ValueError):
        raise UpdateError("Upstream did not provide an immutable Git revision.") from None


def raw_url(revision, filename):
    return (f"https://gist.githubusercontent.com/{AUTHOR}/{GIST}/raw/"
            f"{revision}/{urllib.parse.quote(filename, safe='')}")


def artifact_version(artifact):
    """Read the declared version as syntax, not by importing an untrusted bundle."""
    try:
        text = artifact.decode("utf-8")
        tree = ast.parse(text)
        versions = [node.value.value for node in tree.body
                    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                    and any(isinstance(target, ast.Name) and target.id == "__version__"
                            for target in node.targets)]
        if (len(versions) != 1 or not isinstance(versions[0], str)
                or len(versions[0]) > 128 or not VERSION.fullmatch(versions[0])):
            raise ValueError
        return versions[0], text
    except (UnicodeError, SyntaxError, ValueError, RecursionError):
        raise UpdateError("Candidate must be UTF-8 Python with one literal semantic __version__.") from None


def download(revision=None, get=retrieve):
    """Resolve latest once, then fetch metadata/artifacts only at that revision."""
    if revision is None:
        revision = head_revision(metadata(API, get))
    if not isinstance(revision, str) or not REVISION.fullmatch(revision):
        raise UpdateError("revision must be a full lowercase Git SHA")
    pinned = metadata(f"{API}/{revision}", get)
    if head_revision(pinned) != revision:
        raise UpdateError("Pinned gist metadata does not match the requested revision.")
    entry = pinned["files"].get("avmigrate.py")
    if not isinstance(entry, dict) or entry.get("filename") != "avmigrate.py":
        raise UpdateError("Pinned gist has no avmigrate.py distribution.")
    artifact = get(raw_url(revision, "avmigrate.py"), MAX_ARTIFACT)
    if not isinstance(artifact, bytes) or not artifact or len(artifact) > MAX_ARTIFACT:
        raise UpdateError("Candidate artifact is empty or exceeds the size limit.")
    version, text = artifact_version(artifact)
    licenses = []
    for name in sorted(pinned["files"]):
        if name.lower() not in ("license", "license.md", "license.txt", "copying", "copying.txt"):
            continue
        item = pinned["files"][name]
        if not isinstance(item, dict) or item.get("filename") != name:
            raise UpdateError("Invalid license metadata.")
        data = get(raw_url(revision, name), MAX_LICENSE)
        try:
            if not isinstance(data, bytes) or not data.strip() or len(data) > MAX_LICENSE:
                raise ValueError
            data.decode("utf-8")
        except (ValueError, UnicodeError):
            raise UpdateError("Upstream license must be bounded, nonempty UTF-8 text.") from None
        licenses.append((name, data))
    # Keep upstream bytes verbatim, with source-file separators when multiple notices exist.
    license_data = (licenses[0][1] if len(licenses) == 1 else b"\n\n".join(
        f"--- {name} ---\n".encode() + data for name, data in licenses))
    if len(license_data) > MAX_LICENSE:
        raise UpdateError("Combined upstream license exceeds the size limit.")
    catalogue = re.search(r"(?m)^Catalog:\s*(\d{4}-\d{2}-\d{2}T[\d:]+Z)\s*$", text)
    built = re.search(r"(?m)^Built:\s*(\d{4}-\d{2}-\d{2} [\d:]+ UTC)\s*$", text)
    provenance = {
        "name": "avmigrate", "version": version, "origin": ORIGIN,
        "artifact_origin": raw_url(revision, "avmigrate.py"),
        "revision": revision, "git_revision": revision,
        "git_revision_status": "Verified against immutable gist revision metadata.",
        "sha256": hashlib.sha256(artifact).hexdigest(),
        "catalogue_version": None,
        "catalogue_generated": catalogue.group(1) if catalogue else None,
        "catalogue_status": "Header metadata only; catalogue schema is not inferred or executed during discovery.",
        "built": built.group(1) if built else None, "author": AUTHOR,
        "distribution": "Original single-file bundled Python artifact; original source tree and upstream tests are not included.",
        "license_status": ("Upstream license files captured verbatim; review their terms before merging."
                           if licenses else "No separate license file found at this revision. "
                           "Confirm upstream distribution permission; repository MIT license is not asserted over this artifact."),
        "license_files": [name for name, _ in licenses],
        "license_sha256": hashlib.sha256(license_data).hexdigest() if licenses else None,
    }
    files = {"avmigrate.py": artifact,
             "provenance.json": (json.dumps(provenance, indent=2) + "\n").encode()}
    if licenses:
        files["upstream-license.txt"] = license_data
    return files


def fetch(output, get=retrieve):
    directory = Path(output)
    if not directory.is_absolute() or any(
            item.is_symlink() for item in (directory, *directory.parents)):
        raise UpdateError("output must be an absolute directory without symlinks")
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        raise UpdateError("output must be a new or empty directory")
    candidate = download(get=get)
    directory.mkdir(parents=True, exist_ok=True)
    for name, data in candidate.items():
        fd = os.open(directory / name, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                     getattr(os, "O_NOFOLLOW", 0), 0o644)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
    return json.loads(candidate["provenance.json"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    intake = commands.add_parser("fetch")
    intake.add_argument("--output", required=True)
    promote = commands.add_parser("publish")
    promote.add_argument("--revision", required=True)
    promote.add_argument("--sha256", required=True)
    promote.add_argument("--repository", required=True)
    promote.add_argument("--base-commit", required=True)
    promote.add_argument("--base-branch", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "fetch":
            value = fetch(args.output)
            print(json.dumps({key: value[key] for key in ("version", "git_revision", "sha256")}))
        else:
            from .gist_publish import publish
            value = publish(args.revision, args.sha256, args.repository,
                            args.base_commit, args.base_branch)
            print(json.dumps(value))
    except (UpdateError, PublishError) as error:
        parser.exit(1, f"Gist update stopped: {error}\n")
    except OSError:
        parser.exit(1, "Gist update stopped: cannot write candidate output; "
                    "use a new absolute directory with appropriate permissions.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
