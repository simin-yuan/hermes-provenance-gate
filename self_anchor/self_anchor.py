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

Layout produced for each post, in the directory given by ``--out``:
    <out>/body.txt        the exact published bytes
    <out>/body.txt.ots    detached OpenTimestamps proof of sha256(body.txt)
    <out>/manifest.json   hash, size, platform, ref, stamp metadata

Two rules that exist because breaking them is silent:

1. ``--out`` is required. The directory used to be derived from the date and the platform, so a
   second post to the same platform on the same day landed on top of the first one.
2. An existing body is never overwritten. If ``<out>/body.txt`` already holds different bytes, the
   run refuses. Copying over it leaves ``body.txt.ots`` behind as a proof of the bytes that used to
   be there, and ``ots stamp`` then reports success for a file whose proof covers something else —
   the failure mode is a timestamp that looks valid and proves nothing. Same bytes is not a conflict:
   the run is idempotent and leaves the directory alone.

Stamping needs network access to the public OpenTimestamps calendars. Set
HTTPS_PROXY / HTTP_PROXY if the machine needs a proxy; curl/node pick it up.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path.home() / "shiqing-state" / "self_anchor"
PRIVATE_ROOT = pathlib.Path.home() / "shiqing-state" / "priority"


def resolve_ots_cli() -> pathlib.Path:
    """Where ots-cli.js is: $OTS_JS, else Node's own module lookup, else a hard stop.

    ``verify_self_anchor.js`` reads ``OTS_JS`` the same way. ``OTS_JS`` may point at the package
    directory or at the cli file itself; without it, ``require.resolve`` finds the package wherever
    ``npm install javascript-opentimestamps`` put it. There is deliberately no fallback path: a
    hardcoded developer path only works on the machine it was written on, and it leaks that path to
    everyone who installs the plugin.
    """
    env = os.environ.get("OTS_JS")
    if env:
        p = pathlib.Path(env).expanduser()
        return p if p.suffix.lower() == ".js" else p / "ots-cli.js"
    try:
        found = subprocess.run(
            ["node", "-e",
             "process.stdout.write(require.resolve('javascript-opentimestamps/package.json'))"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        found = None
    if found is not None and found.returncode == 0 and found.stdout.strip():
        return pathlib.Path(found.stdout.strip()).parent / "ots-cli.js"
    raise SystemExit(
        "ots-cli.js not found: set OTS_JS to the javascript-opentimestamps package directory (or "
        "its ots-cli.js), or install the package where Node can resolve it "
        "(npm install javascript-opentimestamps)")
RULE = ("sha256 over the exact UTF-8 bytes of the published body; "
        "no normalization, no stripping, no line-ending conversion")


def assert_not_in_git(directory: pathlib.Path) -> None:
    """A private finding must never be committable by accident.

    shiqing-state is itself a git repo pushed to a remote, so 'inside a work
    tree' is true even for the right directory. The real test is whether git
    would ever pick these bytes up: `git check-ignore` must say yes.
    """
    probe = directory / "body.txt"
    try:
        res = subprocess.run(["git", "-C", str(directory), "check-ignore", "-q", str(probe)],
                             capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        return
    if res.returncode != 0:      # 0 = ignored; 1 = NOT ignored (would be committed)
        raise SystemExit(
            f"refusing: {directory} is not git-ignored — add it to .gitignore first, "
            "a private body must not be committable")


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(src: pathlib.Path, out: pathlib.Path, platform: str, ref: str,
          private: bool = False) -> tuple[pathlib.Path, bool]:
    """Write the body, proof metadata and manifest into ``out``. Returns (directory, reused).

    ``reused`` is True when ``out`` already held exactly these bytes, which makes a repeat run
    harmless instead of destructive.

    Refuses, rather than overwrites, when:

    - ``out/body.txt`` exists with different bytes. Overwriting it would leave ``body.txt.ots``
      standing as a proof of the previous bytes, and the next ``ots stamp`` would report success
      while the proof says nothing about the file now sitting there.
    - a proof exists with no body next to it — nothing to check it against.
    - ``out/manifest.json`` records a different digest than the body on disk.
    """
    raw = src.read_bytes()
    if not raw:
        raise SystemExit("refusing to anchor an empty file")
    digest = hashlib.sha256(raw).hexdigest()
    out = out.expanduser()
    body = out / "body.txt"
    proof = out / "body.txt.ots"
    manifest_path = out / "manifest.json"

    if out.exists():
        if body.exists():
            existing = body.read_bytes()
            if existing != raw:
                raise SystemExit(
                    f"refusing: {body} already holds different bytes "
                    f"(sha256 {hashlib.sha256(existing).hexdigest()}, new {digest}). "
                    "Overwriting it would leave body.txt.ots proving the old bytes. "
                    "Write this one to a new --out directory.")
        elif proof.exists():
            raise SystemExit(
                f"refusing: {proof} exists with no body.txt beside it — the proof would have "
                "nothing to verify against. Pick a new --out directory or remove the orphan.")
        if manifest_path.exists():
            try:
                recorded = json.loads(manifest_path.read_text(encoding="utf-8")).get("digest")
            except Exception:
                recorded = None
            if recorded and recorded != digest:
                raise SystemExit(
                    f"refusing: {manifest_path} records digest {recorded}, this body is {digest}")

    out.mkdir(parents=True, exist_ok=True)
    if private:
        assert_not_in_git(out)

    reused = body.exists()
    if not reused:
        tmp = out / ".body.txt.tmp"
        tmp.write_bytes(raw)                        # bytes, not text
        tmp.replace(body)
    manifest = {
        "hash_rule": RULE,
        "algo": "sha256",
        "digest": digest,
        "bytes": len(raw),
        "platform": platform,
        "ref": ref,
        "source_file": str(src),
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "mode": "private" if private else "public",
        "body_published": not private,
    }
    if private:
        manifest["handling"] = ("body stays on this machine; only the sha256 digest was "
                                "sent to the OpenTimestamps calendars; never commit this directory")
    if reused and manifest_path.exists():
        # Keep the original manifest: it records when these bytes were first anchored.
        return out, True
    manifest_path.write_text(
        json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return out, reused


def stamp(directory: pathlib.Path) -> None:
    body = directory / "body.txt"
    cli = resolve_ots_cli()
    if not cli.exists():
        raise SystemExit(
            f"ots-cli not found at {cli} — set OTS_JS to the javascript-opentimestamps package "
            "directory or to its ots-cli.js (npm install javascript-opentimestamps)")
    out = subprocess.run(
        ["node", str(cli), "stamp", str(body)],
        capture_output=True, text=True, timeout=180,
    )
    sys.stdout.write(out.stdout)
    sys.stderr.write(out.stderr)
    if not (directory / "body.txt.ots").exists():
        raise SystemExit("stamp did not produce body.txt.ots (calendar unreachable?)")


def main() -> None:
    ap = argparse.ArgumentParser(description="anchor a body to Bitcoin")
    ap.add_argument("file", help="the exact text to anchor (UTF-8)")
    ap.add_argument("--out", required=True,
                    help="directory to write body.txt, body.txt.ots and manifest.json into; "
                         "it must be given explicitly, never inferred")
    ap.add_argument("--platform", help="x | moltbook | github | ... (public mode)")
    ap.add_argument("--ref", help="post id / status id / commit (public mode); dir label (private mode)")
    ap.add_argument("--private", action="store_true",
                    help="keep the body local; send only the digest to the calendars")
    ap.add_argument("--no-stamp", action="store_true", help="record only, skip OTS")
    a = ap.parse_args()

    if a.private:
        if not a.ref:
            raise SystemExit("--private needs --ref <label>")
    elif not (a.platform and a.ref):
        raise SystemExit("public mode needs --platform and --ref")

    d, reused = build(pathlib.Path(a.file), pathlib.Path(a.out),
                      a.platform or "private", a.ref, private=a.private)
    print("dir:", d)
    print("digest:", json.loads((d / "manifest.json").read_text(encoding="utf-8"))["digest"])
    if reused:
        print("reused: these exact bytes were already anchored here; nothing was rewritten")
    if a.private:
        print("mode: private — body stays local; only the digest is submitted")
    if not a.no_stamp:
        if reused and (d / "body.txt.ots").exists():
            print("proof already present for these bytes; not stamping again")
        else:
            stamp(d)
            print("stamped:", d / "body.txt.ots")


if __name__ == "__main__":
    main()
