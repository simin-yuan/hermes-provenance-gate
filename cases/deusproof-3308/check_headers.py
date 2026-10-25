#!/usr/bin/env python3
"""Check a DEUSPROOF OpenTimestamps anchor without trusting the register.

Inputs (all public):
  GET https://deusproof.com/api/verify/{id}          -> certificate JSON
  GET https://deusproof.com/api/anchors/seed/{id}    -> the bytes that were stamped
  GET https://deusproof.com/api/anchors/ots/{id}     -> the .ots proof
  https://mempool.space/api/block/{hash}/header      -> 80-byte block headers

Steps:
  1. seed == "prompt_hash:output_hash:id" and sha256(seed) == the digest inside the .ots
  2. every header hashes (sha256d) to its block hash, so it is self-validating
  3. the header's merkle_root equals the terminal value of the OTS op chain
     (that comparison is done by ots_path.js — this script checks the headers and the seed)

Usage:
  python check_headers.py verify.json seed.bin 971330:<blockhash> 971331:<blockhash>
"""
import hashlib
import json
import sys
import urllib.request

OTS_MAGIC = b"\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94"


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"user-agent": "ots-anchor-check"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return r.read()


def ots_digest(proof: bytes) -> str:
    assert proof.startswith(OTS_MAGIC), "not an OpenTimestamps proof"
    i = len(OTS_MAGIC)
    version, i = _varint(proof, i)
    assert version == 1, f"unsupported version {version}"
    dtype, i = _varint(proof, i)
    assert dtype == 8, f"unsupported digest type {dtype}"  # 8 = OpSHA256
    return proof[i:i + 32].hex()


def _varint(b: bytes, i: int):
    n = shift = 0
    while True:
        c = b[i]
        i += 1
        n |= (c & 0x7F) << shift
        if not c & 0x80:
            return n, i
        shift += 7


def main() -> int:
    cert_path, seed_path = sys.argv[1], sys.argv[2]
    blocks = [a.split(":", 1) for a in sys.argv[3:]]
    cert = json.load(open(cert_path, encoding="utf-8"))

    seed = open(seed_path, "rb").read()
    expected = f"{cert['prompt_hash']}:{cert['output_hash']}:{cert['id']}".encode()
    print(f"seed bytes           : {len(seed)}")
    print(f"seed == p:o:id       : {seed == expected}")
    print(f"sha256(seed)         : {hashlib.sha256(seed).hexdigest()}")

    proof = fetch(f"https://deusproof.com/api/anchors/ots/{cert['id']}")
    dig = ots_digest(proof)
    print(f"digest inside .ots   : {dig}")
    print(f"DIGEST MATCH         : {dig == hashlib.sha256(seed).hexdigest()}")

    ok = True
    for height, block_hash in blocks:
        hdr = fetch(f"https://mempool.space/api/block/{block_hash}/header").strip()
        raw = bytes.fromhex(hdr.decode())
        assert len(raw) == 80, f"header is {len(raw)} bytes, expected 80"
        block_id = hashlib.sha256(hashlib.sha256(raw).digest()).digest()[::-1].hex()
        merkle = raw[36:68][::-1].hex()
        ts = int.from_bytes(raw[68:72], "little")
        same = block_id == block_hash
        ok &= same
        print(f"\nblock {height}")
        print(f"  header sha256d     : {block_id}")
        print(f"  header is this block: {same}")
        print(f"  merkle_root        : {merkle}")
        print(f"  header timestamp   : {ts} (unix)")
    print("\nHEADERS OK" if ok else "\nHEADER MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
