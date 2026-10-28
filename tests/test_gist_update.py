import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import urllib.error

from terraform_to_avm import gist_update as update


REVISION = "a" * 40
OTHER = "b" * 40
ARTIFACT = b'''"""avmigrate single-file build
Built:          2026-09-07 09:16:17 UTC
Catalog:        2026-09-07T08:53:47Z
"""
__version__ = "1.11.0"
raise RuntimeError("Discovery must not execute this candidate")
'''


def gist(revision=REVISION, license_name=None):
    files = {"avmigrate.py": {"filename": "avmigrate.py",
                              "raw_url": "https://untrusted.invalid/never-used"}}
    if license_name:
        files[license_name] = {"filename": license_name}
    return {"id": update.GIST, "owner": {"login": update.AUTHOR},
            "history": [{"version": revision}], "files": files}


class GistTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.latest = gist()
        self.pinned = gist()
        self.artifact = ARTIFACT
        self.license = b"Original upstream license notice\n"

    def get(self, url, limit):
        self.calls.append((url, limit))
        if url == update.API:
            return json.dumps(self.latest).encode()
        if url == f"{update.API}/{REVISION}":
            return json.dumps(self.pinned).encode()
        if url == update.raw_url(REVISION, "avmigrate.py"):
            return self.artifact
        if url == update.raw_url(REVISION, "LICENSE.md"):
            return self.license
        raise AssertionError(f"Unexpected URL: {url}")

    def test_latest_is_resolved_once_then_only_immutable_urls_are_used(self):
        files = update.download(get=self.get)
        provenance = json.loads(files["provenance.json"])
        self.assertEqual(self.calls, [
            (update.API, update.MAX_METADATA),
            (f"{update.API}/{REVISION}", update.MAX_METADATA),
            (update.raw_url(REVISION, "avmigrate.py"), update.MAX_ARTIFACT)])
        self.assertEqual(provenance["version"], "1.11.0")
        self.assertEqual(provenance["git_revision"], REVISION)
        self.assertEqual(provenance["sha256"], hashlib.sha256(ARTIFACT).hexdigest())
        self.assertIn(REVISION, provenance["artifact_origin"])
        self.assertEqual(provenance["catalogue_generated"], "2026-09-07T08:53:47Z")
        self.assertIn("No separate license", provenance["license_status"])
        self.assertNotIn("upstream-license.txt", files)

    def test_explicit_revision_never_resolves_latest(self):
        update.download(REVISION, get=self.get)
        self.assertNotIn(update.API, [url for url, _ in self.calls])

    def test_moving_latest_does_not_change_selected_revision(self):
        def moving_get(url, limit):
            data = self.get(url, limit)
            if url == update.API:
                self.latest = gist(OTHER)
            return data

        update.download(get=moving_get)
        self.assertEqual(self.latest["history"][0]["version"], OTHER)
        self.assertNotIn(OTHER, " ".join(url for url, _ in self.calls))

    def test_pinned_revision_mismatch_is_rejected(self):
        self.pinned = gist(OTHER)
        with self.assertRaisesRegex(update.UpdateError, "does not match"):
            update.download(get=self.get)
        self.assertEqual(len(self.calls), 2)

    def test_origin_must_match_expected_gist_and_author(self):
        for value in ({"id": OTHER}, {"owner": {"login": "someone-else"}}):
            self.latest = {**gist(), **value}
            with self.assertRaises(update.UpdateError):
                update.download(get=self.get)
        self.assertEqual({url for url, _ in self.calls}, {update.API})

    def test_only_literal_version_is_accepted_without_execution(self):
        for artifact in (b'__version__ = compute_version()', b'__version__ = "bad"',
                         b'__version__ = "1.0.0"\n__version__ = "2.0.0"',
                         b"\xff", b"this is not python [", b""):
            self.artifact = artifact
            with self.subTest(artifact=artifact), self.assertRaises(update.UpdateError):
                update.download(get=self.get)

    def test_oversize_artifact_rejected(self):
        self.artifact = b"x" * (update.MAX_ARTIFACT + 1)
        with self.assertRaisesRegex(update.UpdateError, "size limit"):
            update.download(get=self.get)

    def test_upstream_license_is_preserved_and_hashed(self):
        self.pinned = gist(license_name="LICENSE.md")
        files = update.download(get=self.get)
        self.assertEqual(files["upstream-license.txt"], self.license)
        provenance = json.loads(files["provenance.json"])
        self.assertEqual(provenance["license_files"], ["LICENSE.md"])
        self.assertEqual(provenance["license_sha256"], hashlib.sha256(self.license).hexdigest())
        self.assertIn((update.raw_url(REVISION, "LICENSE.md"), update.MAX_LICENSE), self.calls)

    def test_empty_or_invalid_license_fails_closed(self):
        self.pinned = gist(license_name="LICENSE.md")
        for value in (b" ", b"\xff", b"x" * (update.MAX_LICENSE + 1)):
            self.license = value
            with self.subTest(value=value[:20]), self.assertRaises(update.UpdateError):
                update.download(get=self.get)

    def test_metadata_failures_are_actionable_and_do_not_leak_content(self):
        for raw in (b"not JSON containing-sensitive-data", b"[]", b'{"id":"wrong"}'):
            with self.assertRaises(update.UpdateError) as error:
                update.download(get=lambda *_: raw)
            self.assertNotIn("sensitive-data", str(error.exception))

    def test_revision_requires_full_lowercase_sha(self):
        for revision in ("main", OTHER.upper(), "../raw", "a" * 39, 42):
            with self.subTest(revision=revision), self.assertRaises(update.UpdateError):
                update.download(revision, get=self.get)
        self.assertEqual(self.calls, [])

    def test_fetch_writes_only_expected_files_to_new_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "candidate"
            provenance = update.fetch(directory, get=self.get)
            self.assertEqual({p.name for p in directory.iterdir()}, {"avmigrate.py", "provenance.json"})
            self.assertEqual((directory / "avmigrate.py").read_bytes(), ARTIFACT)
            self.assertEqual(provenance["git_revision"], REVISION)
            with self.assertRaisesRegex(update.UpdateError, "empty"):
                update.fetch(directory, get=self.get)

    def test_symlink_or_relative_output_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.mkdir()
            link = Path(tmp) / "linked"
            link.symlink_to(target, target_is_directory=True)
            for path in (link, link / "candidate", Path("relative-candidate")):
                with self.assertRaises(update.UpdateError):
                    update.fetch(path, get=self.get)
        self.assertEqual(self.calls, [])

    def test_network_failure_creates_no_partial_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "candidate"
            with self.assertRaises(update.UpdateError):
                update.fetch(directory, get=mock.Mock(side_effect=update.UpdateError("network")))
            self.assertFalse(directory.exists())

    def test_network_diagnostic_is_not_mislabeled_as_invalid_metadata(self):
        with self.assertRaisesRegex(update.UpdateError, "connectivity"):
            update.download(get=mock.Mock(side_effect=update.UpdateError(
                "Check upstream connectivity and API rate limits.")))

    def test_retrieve_is_anonymous_bounded_and_disallows_redirects(self):
        opener = mock.MagicMock()
        opener.open.return_value.__enter__.return_value = io.BytesIO(b"payload")
        with mock.patch.dict("os.environ", {"GITHUB_TOKEN": "never-send-this"}), mock.patch(
                "urllib.request.build_opener", return_value=opener) as factory:
            self.assertEqual(update.retrieve(update.API, 10), b"payload")
        request = opener.open.call_args.args[0]
        self.assertNotIn("Authorization", request.headers)
        self.assertIsInstance(factory.call_args.args[0], update._NoRedirect)

    def test_retrieve_enforces_limit_and_hides_network_errors(self):
        opener = mock.MagicMock()
        opener.open.return_value.__enter__.return_value = io.BytesIO(b"too big")
        with mock.patch("urllib.request.build_opener", return_value=opener):
            with self.assertRaisesRegex(update.UpdateError, "size limit"):
                update.retrieve(update.API, 3)
            opener.open.side_effect = urllib.error.URLError("sensitive upstream message")
            with self.assertRaises(update.UpdateError) as error:
                update.retrieve(update.API, 3)
            self.assertNotIn("sensitive upstream message", str(error.exception))


if __name__ == "__main__":
    unittest.main()
