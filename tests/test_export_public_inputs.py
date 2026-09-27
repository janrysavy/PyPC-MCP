"""Standard-library tests for public-only connector input export."""
import base64
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

FILE = Path(__file__).resolve().parents[1] / "ci/export_public_inputs.py"
spec = importlib.util.spec_from_file_location("public_inputs", FILE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class PublicInputs(unittest.TestCase):
    def make_commit(self, entries, message="fixture\n", zone="+0200"):
        tree = m.tree_hash(entries)
        raw = (f"tree {tree}\nauthor Probe <probe@example.invalid> 0 {zone}\n"
               f"committer Probe <probe@example.invalid> 0 {zone}\n\n{message}").encode()
        person = {"name": "Probe", "email": "probe@example.invalid", "date": "1970-01-01T00:00:00Z"}
        return {"sha": m.oid("commit", raw), "tree": {"sha": tree}, "parents": [],
                "author": person, "committer": person, "message": message,
                "verification": {"payload": None, "signature": None}}, raw

    def fixture(self, files=None, missing=False, mutate=False, duplicate=False, roots=False, private=False):
        files = files or {"binary.bin": b"\x00\xff\xfa\x80", "nested/a.py": b"pass\n"}
        entries = [{"path": path, "mode": "100644", "oid": m.oid("blob", data)}
                   for path, data in files.items()]
        commit, raw = self.make_commit(entries)
        repo, prefix = "janrysavy/PyPC-MCP", "tools/pypc/src/"
        tree = {"sha": commit["tree"]["sha"], "truncated": False,
                "tree": [{"path": e["path"], "mode": e["mode"], "sha": e["oid"], "type": "blob"}
                         for e in entries]}
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as z:
            for path, data in files.items():
                if not missing:
                    z.writestr("checkout/" + path, data + (b"BAD" if mutate else b""))
            if missing:
                z.writestr("checkout/", b"")
            if duplicate:
                z.writestr("checkout/binary.bin", b"duplicate")
            if roots:
                z.writestr("second/extra", b"extra")
        calls = []
        def get_json(name, suffix=""):
            calls.append(suffix)
            if not suffix:
                return {"private": private, "full_name": repo}
            if suffix.startswith("/git/commits/"):
                return commit
            if suffix.startswith("/git/trees/"):
                return tree
            for path, data in files.items():
                if suffix == "/git/blobs/" + m.oid("blob", data):
                    return {"encoding": "base64", "content": base64.b64encode(data).decode()}
            raise AssertionError(suffix)
        return repo, prefix, commit, raw, tree, archive.getvalue(), get_json, calls

    def run_export(self, fixture):
        repo, prefix, commit, raw, tree, archive, get_json, calls = fixture
        result = io.BytesIO()
        manifest = {"files": {}, "repositories": {}}
        with mock.patch.object(m, "INPUTS", ((repo, prefix, commit["sha"]),)):
            with zipfile.ZipFile(result, "w") as output:
                m.export_repository(repo, prefix, commit["sha"], output, manifest,
                                    get_json, lambda *_: archive)
        return result.getvalue(), manifest

    def test_binary_roundtrip_and_raw_commit_identity(self):
        fixture = self.fixture()
        data, manifest = self.run_export(fixture)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            self.assertEqual(z.read("tools/pypc/src/binary.bin"), b"\x00\xff\xfa\x80")
        row = manifest["repositories"]["tools/pypc/src/"]
        self.assertEqual(base64.b64decode(row["commit_object"]), fixture[3])

    def test_private_repository_refused_before_commit_or_archive(self):
        fixture = self.fixture(private=True)
        with self.assertRaisesRegex(ValueError, "non-public"):
            self.run_export(fixture)
        self.assertEqual(fixture[-1], [""])

    def test_arbitrary_repo_or_ref_refused(self):
        with self.assertRaisesRegex(ValueError, "fixed public"):
            m.export_repository("janrysavy/dostools", "private/", "0"*40, None, {})
        with self.assertRaisesRegex(ValueError, "fixed public"):
            m.export_repository("janrysavy/PyPC-MCP", "tools/pypc/src/", "0"*40, None, {})

    def test_truncated_tree_refused(self):
        fixture = self.fixture()
        fixture[4]["truncated"] = True
        with self.assertRaisesRegex(ValueError, "truncated"):
            self.run_export(fixture)

    def test_tree_tampering_refused(self):
        fixture = self.fixture()
        fixture[4]["tree"][0]["sha"] = "0"*40
        with self.assertRaisesRegex(ValueError, "tree hash"):
            self.run_export(fixture)

    def test_corrupt_archive_blob_refused(self):
        with self.assertRaisesRegex(ValueError, "blob differs"):
            self.run_export(self.fixture(mutate=True))

    def test_export_ignored_file_uses_verified_git_blob(self):
        fixture = self.fixture(missing=True)
        _, manifest = self.run_export(fixture)
        self.assertEqual(len(manifest["files"]), 2)
        self.assertEqual(sum(s.startswith("/git/blobs/") for s in fixture[-1]), 2)

    def test_duplicate_or_multiroot_archive_refused(self):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            for options in ({"duplicate": True}, {"roots": True}):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    self.run_export(self.fixture(**options))

    def test_unsafe_git_paths_and_duplicate_entries_refused(self):
        for path in ("../x", "a/../x", "/x", "x\\y", "a/.git/config", "C:/x", "a//x"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                m.tree_hash([{"path": path, "mode": "100644", "oid": "0"*40}])
        row = {"path": "x", "mode": "100644", "oid": "0"*40}
        with self.assertRaisesRegex(ValueError, "duplicate"):
            m.tree_hash([row, row])

    def test_git_ordering_modes_and_unusual_unsigned_timezone(self):
        entries = [{"path": "a.c", "mode": "100755", "oid": "1"*40},
                   {"path": "a/z", "mode": "120000", "oid": "2"*40},
                   {"path": "module", "mode": "160000", "oid": "3"*40}]
        self.assertEqual(m.tree_hash(entries), m.tree_hash(list(reversed(entries))))
        commit, raw = self.make_commit(entries, zone="+0545")
        self.assertEqual(m.raw_commit(commit), raw)

    def test_signed_commit_reconstruction_is_hash_checked(self):
        commit, unsigned = self.make_commit([])
        signature = "-----BEGIN PGP SIGNATURE-----\n\nexample\n-----END PGP SIGNATURE-----\n"
        head, body = unsigned.decode().split("\n\n", 1)
        signed = (head + "\ngpgsig " + signature.rstrip("\n").replace("\n", "\n ") + "\n\n" + body).encode()
        commit.update(sha=m.oid("commit", signed),
                      verification={"payload": unsigned.decode(), "signature": signature})
        self.assertEqual(m.raw_commit(commit), signed)
        commit["verification"]["signature"] += "corrupt"
        with self.assertRaisesRegex(ValueError, "signed commit"):
            m.raw_commit(commit)

    def test_failed_bundle_is_removed_and_existing_bundle_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/"inputs.zip"
            with mock.patch.object(m, "export_repository", side_effect=RuntimeError("offline")):
                with self.assertRaisesRegex(RuntimeError, "offline"):
                    m.build(output)
            self.assertFalse(output.exists())
            self.assertFalse(output.with_suffix(".zip.partial").exists())
            output.write_bytes(b"KEEP")
            with self.assertRaises(FileExistsError):
                m.build(output)
            self.assertEqual(output.read_bytes(), b"KEEP")


if __name__ == "__main__":
    unittest.main()
