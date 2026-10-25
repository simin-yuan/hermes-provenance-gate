#!/usr/bin/env python
"""
Self-anchoring: give every post we publish our own Bitcoin timestamp.

RULE (fixed, do not change per-post): the anchored digest is
    sha256(exact UTF-8 bytes of the published body)
with no normalization of any kind — no strip(), no CRLF->LF, no NFC, no
whitespace collapsing, no trailing-newline trimming. Whatever bytes went out
are the bytes that get hashed, and the same bytes are kept on disk forever.
Whether a text editor would consider two files "the same" is irrelevant; the
hash is over the file, byte for byte. This is the rule DEUSPROOF never
published, and the reason their content hash could not be recomputed.

Layout produced for each post:
    self_anchor/<YYYY-MM-DD>_<platform>_<ref>/
        body.txt        the exact published bytes
        body.txt.ots    detached OpenTimestamps proof of sha256(body.txt)
        manifest.json   hash, size, platform, ref, stamp metadata

Stamping needs network access to the public OpenTimestamps calendars. Set
HTTPS_PROXY / HTTP_PROXY if the machine needs a proxy; curl/node pick it up.
"""
import argparse
import datetime as dt
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path.home() / "shiqing-state" / "self_anchor"
OTS_CLI = r"C:/Users/Simon/node_modules/javascript-opentimestamps/ots-cli.js"
RULE = ("sha256 over the exact UTF-8 bytes of the published body; "
        "no normalization, no stripping, no line-ending conversion")


def build(src: pathlib.Path, platform: str, ref: str, date: str | None) -> pathlib.Path:
    raw = src.read_bytes()
    if not raw:
        raise SystemExit("refusing to anchor an empty file")
    day = date or dt.datetime.now(dt.timezone.utc).astimezone().strftime("%Y-%m-%d")
    directory = ROOT / f"{day}_{platform}_{ref}"
    directory.mkdir(parents=True, exist_ok=True)
    body = directory / "body.txt"
    shutil.copyfile(src, body)                      # bytes, not text
    manifest = {
        "hash_rule": RULE,
        "algo": "sha256",
        "digest": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "platform": platform,
        "ref": ref,
        "source_file": str(src),
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return directory


def stamp(directory: pathlib.Path) -> None:
    body = directory / "body.txt"
    if not OTS_CLI or not pathlib.Path(OTS_CLI).exists():
        raise SystemExit(f"ots-cli not found at {OTS_CLI}")
    out = subprocess.run(
        ["node", OTS_CLI, "stamp", str(body)],
        capture_output=True, text=True, timeout=180,
    )
    sys.stdout.write(out.stdout)
    sys.stderr.write(out.stderr)
    if not (directory / "body.txt.ots").exists():
        raise SystemExit("stamp did not produce body.txt.ots (calendar unreachable?)")


def main() -> None:
    ap = argparse.ArgumentParser(description="anchor a published body to Bitcoin")
    ap.add_argument("file", help="the exact published text (UTF-8)")
    ap.add_argument("--platform", required=True, help="x | moltbook | github | ...")
    ap.add_argument("--ref", required=True, help="post id / status id / commit")
    ap.add_argument("--date", help="YYYY-MM-DD (default: today, local)")
    ap.add_argument("--no-stamp", action="store_true", help="record only, skip OTS")
    a = ap.parse_args()
    d = build(pathlib.Path(a.file), a.platform, a.ref, a.date)
    print("dir:", d)
    print("digest:", json.loads((d / "manifest.json").read_text(encoding="utf-8"))["digest"])
    if not a.no_stamp:
        stamp(d)
        print("stamped:", d / "body.txt.ots")


if __name__ == "__main__":
    main()
