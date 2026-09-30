"""Export only fixed PUBLIC Git objects for connector artifact delivery.

Run in public CI, not in a private licensed-tool workspace. No fetched code is
executed. The receiver must verify the parent pins independently. This is NOT
a complete parent workspace, a compiler run, or reconstruction evidence.
"""
from __future__ import annotations
import argparse
import base64
import datetime
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import urllib.request
import zipfile

INPUTS = (
    ("janrysavy/PyPC-MCP", "tools/pypc/src/", "ec610f38ca40ba97fdf61d09bf9ff43c325e3635"),
    ("janrysavy/GLaBIOS", "tools/pypc/src/firmware/glabios/", "5cd99653737beb31ab9997edf7fdd2036ebd5bb8"),
    ("janrysavy/pynasm", "tools/pynasm/", "80e1262f44a6a04a7dd29a8f482d1ebdd49d8987"),
    ("janrysavy/pydasm", "tools/pydasm/", "8f48899dbbfebe40e2b501c4bd2160f7dd9c5989"),
    ("janrysavy/dosbox-x-mcp", "tools/dosbox-x/src/", "18efceaf41b26d2b7567725c68c722035f163615"),
)
ALLOWED = frozenset(row[0] for row in INPUTS)


def oid(kind, data):
    return hashlib.sha1(kind.encode("ascii") + b" " + str(len(data)).encode("ascii")
                        + b"\0" + data).hexdigest()


def safe_name(name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or "\\" in name or ":" in name
            or any(part in ("", ".", "..", ".git") for part in name.split("/"))):
        raise ValueError("unsafe path: " + name)
    return name


def tree_hash(entries):
    root = {}
    for row in entries:
        parts = safe_name(row["path"]).split("/")
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise ValueError("file/directory conflict")
        if parts[-1] in node:
            raise ValueError("duplicate Git entry")
        if row["mode"] not in ("100644", "100755", "120000", "160000"):
            raise ValueError("unsupported Git mode")
        if not re.fullmatch(r"[0-9a-f]{40}", row["oid"]):
            raise ValueError("invalid Git object ID")
        node[parts[-1]] = row["mode"], row["oid"]
    def visit(node):
        records = []
        for name, item in node.items():
            directory = isinstance(item, dict)
            mode, sha = ("40000", visit(item)) if directory else item
            key = name.encode() + (b"/" if directory else b"")
            raw = mode.encode() + b" " + name.encode() + b"\0" + bytes.fromhex(sha)
            records.append((key, raw))
        return oid("tree", b"".join(raw for _, raw in sorted(records)))
    return visit(root)


def raw_commit(commit):
    """Recover canonical bytes only when they hash to the supplied Git SHA."""
    expected = commit["sha"]
    verification = commit.get("verification") or {}
    payload, signature = verification.get("payload"), verification.get("signature")
    if payload and signature:
        header, body = payload.split("\n\n", 1)
        for sig in (signature.rstrip("\n"), signature):
            data = (header + "\ngpgsig " + sig.replace("\n", "\n ") + "\n\n" + body).encode()
            if oid("commit", data) == expected:
                return data
        raise ValueError("signed commit cannot be reconstructed")
    if payload:
        data = payload.encode()
        if oid("commit", data) == expected:
            return data
    header = "tree " + commit["tree"]["sha"] + "\n"
    header += "".join("parent " + row["sha"] + "\n" for row in commit["parents"])
    def identity(role, zone):
        row = commit[role]
        epoch = int(datetime.datetime.fromisoformat(row["date"].replace("Z", "+00:00")).timestamp())
        return f'{role} {row["name"]} <{row["email"]}> {epoch} {zone}\n'
    zones = ["+0000", "+0200", "+0100"]
    for minutes in range(-12 * 60, 14 * 60 + 1, 15):
        zone = ("-" if minutes < 0 else "+") + f"{abs(minutes)//60:02d}{abs(minutes)%60:02d}"
        if zone not in zones:
            zones.append(zone)
    pairs = [(zone, zone) for zone in zones]
    pairs.extend((a, c) for a in zones for c in zones if a != c)
    messages = tuple(dict.fromkeys((commit["message"], commit["message"] + "\n")))
    for author_zone, committer_zone in pairs:
        lead = header + identity("author", author_zone) + identity("committer", committer_zone) + "\n"
        for message in messages:
            data = (lead + message).encode("utf-8")
            if oid("commit", data) == expected:
                return data
    raise ValueError("commit bytes unavailable; no unverified identity substitute")


def api_json(repo, suffix=""):
    if repo not in ALLOWED:
        raise ValueError("only fixed public repositories are allowed")
    url = "https://api.github.com/repos/" + repo + suffix
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "PyPC-public-input-receipt"}
    # The token is used only for GitHub API rate limits, never for source archives.
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=90) as response:
        return json.load(response)


def archive_bytes(repo, sha):
    if repo not in ALLOWED or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("only pinned public source archives are allowed")
    url = f"https://codeload.github.com/{repo}/zip/{sha}"
    with urllib.request.urlopen(url, timeout=180) as response:
        data = response.read(512 * 1024 * 1024 + 1)
    if len(data) > 512 * 1024 * 1024:
        raise ValueError("source archive too large")
    return data


def export_repository(repo, prefix, sha, output, manifest, get_json=api_json, get_archive=archive_bytes):
    if repo not in ALLOWED or (repo, prefix, sha) not in INPUTS:
        raise ValueError("repository/ref/path not in the fixed public input set")
    safe_name(prefix[:-1])
    info = get_json(repo)
    if info.get("private") is not False or info.get("full_name") != repo:
        raise ValueError("refusing non-public or redirected repository")
    commit = get_json(repo, "/git/commits/" + sha)
    if commit["sha"] != sha:
        raise ValueError("unexpected commit")
    raw = raw_commit(commit)
    tree = get_json(repo, "/git/trees/" + commit["tree"]["sha"] + "?recursive=1")
    if tree.get("truncated") is not False:
        raise ValueError("truncated or incomplete tree")
    entries = [{"path": row["path"], "mode": row["mode"], "oid": row["sha"]}
               for row in tree["tree"] if row["type"] != "tree"]
    if tree_hash(entries) != commit["tree"]["sha"] or tree["sha"] != commit["tree"]["sha"]:
        raise ValueError("Git tree hash differs")
    source = get_archive(repo, sha)
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError("duplicate archive member")
        roots = {name.split("/", 1)[0] for name in names}
        if len(roots) != 1:
            raise ValueError("source archive has multiple roots")
        archive_root = next(iter(roots)) + "/"
        for row in entries:
            path = prefix + row["path"]
            safe_name(path)
            if row["mode"] == "160000":
                continue
            try:
                data = archive.read(archive_root + row["path"])
            except KeyError:
                data = None
            if data is not None and oid("blob", data) != row["oid"]:
                # Source archives may apply eol filters. Accept a restoration
                # only if it reproduces the exact committed Git object.
                lf = data.replace(b"\r\n", b"\n")
                data = next((candidate for candidate in (lf, lf.replace(b"\n", b"\r\n"))
                             if oid("blob", candidate) == row["oid"]), None)
            if data is None:
                # export-ignore/export-subst or a noncanonical archive requires
                # canonical Git bytes, not a relaxed equality/masking rule.
                blob = get_json(repo, "/git/blobs/" + row["oid"])
                if blob.get("encoding") != "base64":
                    raise ValueError("non-base64 Git blob")
                data = base64.b64decode("".join(blob["content"].split()), validate=True)
            if oid("blob", data) != row["oid"]:
                raise ValueError("source blob differs: " + path)
            if path in manifest["files"]:
                raise ValueError("duplicate destination")
            record = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            if row["mode"] == "120000":
                record["symlink_blob"] = base64.b64encode(data).decode()
            output.writestr(path, data)  # Symlink targets are literal blob bytes, never followed.
            manifest["files"][path] = record
    manifest["repositories"][prefix] = {
        "commit": sha, "commit_object": base64.b64encode(raw).decode(),
        "entries": entries, "repository": repo,
    }
    print(json.dumps({"repository": repo, "commit": sha, "files": len(entries),
                      "archive_sha256": hashlib.sha256(source).hexdigest()}), flush=True)


def build(output_path):
    manifest = {"version": 1, "scope": "PUBLIC_SUBREPOSITORIES_ONLY",
                "repositories": {}, "files": {}, "compiler_execution": "NOT_RUN",
                "validation_gates": "NOT_RUN"}
    output_path = Path(output_path)
    partial = output_path.with_suffix(output_path.suffix + ".partial")
    if output_path.exists() or partial.exists():
        raise FileExistsError("refusing to replace an existing bundle")
    try:
        with zipfile.ZipFile(partial, "x", zipfile.ZIP_DEFLATED) as output:
            for repo, prefix, sha in INPUTS:
                export_repository(repo, prefix, sha, output, manifest)
            for prefix, row in manifest["repositories"].items():
                for entry in row["entries"]:
                    if entry["mode"] == "160000":
                        child = manifest["repositories"].get(prefix + entry["path"] + "/")
                        if not child or child["commit"] != entry["oid"]:
                            raise ValueError("unresolved recursive pin")
            output.writestr("PUBLIC_MANIFEST.json", json.dumps(manifest, indent=2) + "\n")
        partial.rename(output_path)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    print(json.dumps({"scope": manifest["scope"], "files": len(manifest["files"]),
                      "repositories": len(manifest["repositories"]),
                      "sha256": hashlib.sha256(output_path.read_bytes()).hexdigest()}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    build(args.out)
