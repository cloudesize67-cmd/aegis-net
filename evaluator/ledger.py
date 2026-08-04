#!/usr/bin/env python3
"""
ledger.py — tamper-evident hash-chained ledger for run-records and SFT traces.

Direct application of the enterprise-blockchain doc's core mechanism to the
self-training stack: every record is cryptographically linked to the one
before it (SHA-256 over prev_hash + record). Alter or delete ANY historical
record and the chain breaks — verify() detects it instantly.

This gives the PoC its "receipt": investors/auditors can verify that the
training data and score history were not retroactively edited.

Stdlib only. Termux-compatible.

Usage as a module:
    from ledger import append, verify
    append("results.jsonl", record_dict)     # adds _chain fields, writes line
    ok, msg = verify("results.jsonl")        # True/False + detail

Usage as CLI:
    python ledger.py --verify results.jsonl
    python ledger.py --verify sft_traces.jsonl
"""

import argparse
import hashlib
import json
import os

GENESIS = "0" * 64  # hash of the empty chain


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def _hash(prev_hash, record):
    return hashlib.sha256(prev_hash.encode() + _canonical(record)).hexdigest()


def _last_hash(path):
    if not os.path.exists(path):
        return GENESIS
    prev = GENESIS
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                prev = json.loads(line)["_chain"]["hash"]
    return prev


def append(path, record):
    """Append record to the chain at path. Mutates a copy; returns the stored record."""
    rec = dict(record)
    prev = _last_hash(path)
    h = _hash(prev, rec)
    rec["_chain"] = {"prev": prev, "hash": h}
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


def append_many(path, records):
    return [append(path, r) for r in records]


def verify(path):
    """Re-walk the chain. Returns (ok, detail). Detects edits, deletions, reordering."""
    if not os.path.exists(path):
        return False, "file not found"
    prev = GENESIS
    n = 0
    with open(path) as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            chain = rec.pop("_chain", None)
            if chain is None:
                return False, f"line {lineno}: missing _chain (record written outside ledger)"
            if chain["prev"] != prev:
                return False, f"line {lineno}: broken link (record deleted or reordered before it)"
            if _hash(prev, rec) != chain["hash"]:
                return False, f"line {lineno}: content altered after writing"
            prev = chain["hash"]
            n += 1
    return True, f"chain intact: {n} records verified"


def main():
    ap = argparse.ArgumentParser(description="Tamper-evident hash-chained ledger")
    ap.add_argument("--verify", metavar="PATH", help="Verify the chain at PATH")
    args = ap.parse_args()
    if args.verify:
        ok, msg = verify(args.verify)
        print(("PASS  " if ok else "FAIL  ") + msg)
        raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
