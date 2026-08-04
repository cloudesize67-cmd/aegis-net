#!/usr/bin/env python3
"""
code-repair-evaluator.py — ground-truth evaluator for the code-repair vertical.

Runs a candidate solution against each task's PUBLIC and PRIVATE test suites in
isolated subprocesses, then emits a graded score record (JSONL) per task.

Stdlib only. Works on Linux, macOS, and Android/Termux.

Usage:
  # Score one candidate file against one task
  python code-repair-evaluator.py --cases code-repair-test-cases.jsonl \
      --task CR-001 --candidate my_fix.py

  # Score one candidate file against ALL tasks (candidate must define every entry point;
  # mainly useful for smoke tests)
  python code-repair-evaluator.py --cases code-repair-test-cases.jsonl \
      --candidate my_fix.py --out results.jsonl

  # Self-check: score every task's reference_solution (must score 1.000 everywhere)
  python code-repair-evaluator.py --cases code-repair-test-cases.jsonl --selftest

  # Baseline: score every task's buggy_code (shows the harness detects real bugs)
  python code-repair-evaluator.py --cases code-repair-test-cases.jsonl --baseline
"""

import argparse
import json
import subprocess
import sys
import tempfile
import time
import os

RUNNER = r'''
import importlib, json, sys, traceback
sys.path.insert(0, ".")
results = []
try:
    import tests
except Exception:
    print("RESULT_JSON:" + json.dumps([{"name": "<import>", "ok": False,
        "err": traceback.format_exc()[-300:]}]))
    sys.exit(0)
for name in sorted(dir(tests)):
    if name.startswith("test_"):
        fn = getattr(tests, name)
        try:
            fn()
            results.append({"name": name, "ok": True})
        except Exception:
            results.append({"name": name, "ok": False,
                            "err": traceback.format_exc()[-300:]})
print("RESULT_JSON:" + json.dumps(results))
'''

PUBLIC_WEIGHT = 0.35   # visible to the agent
PRIVATE_WEIGHT = 0.65  # held out — resists overfitting / reward hacking


def run_split(workdir, candidate_code, test_src, timeout):
    """Write solution.py + tests.py + runner into workdir, execute, return (passed, total, fails, status)."""
    sol_path = os.path.join(workdir, "solution.py")
    tst_path = os.path.join(workdir, "tests.py")
    run_path = os.path.join(workdir, "runner.py")
    with open(sol_path, "w") as f:
        f.write(candidate_code)
    with open(tst_path, "w") as f:
        f.write(test_src)
    with open(run_path, "w") as f:
        f.write(RUNNER)
    try:
        proc = subprocess.run(
            [sys.executable, "runner.py"],
            cwd=workdir, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 0, 0, [], "timeout"
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT_JSON:")), None)
    if line is None:
        return 0, 0, [], f"harness_error: {proc.stderr.strip()[-200:]}"
    results = json.loads(line[len("RESULT_JSON:"):])
    passed = sum(1 for r in results if r["ok"])
    fails = [r for r in results if not r["ok"]]
    return passed, len(results), fails, "ok"


def score_task(task, candidate_code):
    """Grade candidate_code on one task. Returns a score record dict."""
    timeout = task.get("timeout_sec", 10)
    t0 = time.time()
    with tempfile.TemporaryDirectory() as d:
        pub = run_split(d, candidate_code, task["public_tests"], timeout)
    with tempfile.TemporaryDirectory() as d:
        prv = run_split(d, candidate_code, task["private_tests"], timeout)
    latency = round(time.time() - t0, 3)

    pub_rate = (pub[0] / pub[1]) if pub[1] else 0.0
    prv_rate = (prv[0] / prv[1]) if prv[1] else 0.0
    score = round(PUBLIC_WEIGHT * pub_rate + PRIVATE_WEIGHT * prv_rate, 4)

    status = "ok"
    if pub[3] == "timeout" or prv[3] == "timeout":
        status = "timeout"
    elif pub[3].startswith("harness_error") or prv[3].startswith("harness_error"):
        status = "harness_error"

    return {
        "task_id": task["task_id"],
        "difficulty": task["difficulty"],
        "bug_category": task["bug_category"],
        "public_pass": pub[0], "public_total": pub[1],
        "private_pass": prv[0], "private_total": prv[1],
        "score": score,
        "solved": score == 1.0,
        "status": status,
        "latency_s": latency,
        "failures": {
            "public": [{"test": r["name"], "err": r.get("err", "")} for r in pub[2]],
            "private": [{"test": r["name"], "err": r.get("err", "")} for r in prv[2]],
        },
    }


def load_cases(path):
    tasks = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                tasks.append(json.loads(line))
    return tasks


def main():
    ap = argparse.ArgumentParser(description="Ground-truth evaluator for the code-repair vertical.")
    ap.add_argument("--cases", required=True, help="Path to JSONL test-case file")
    ap.add_argument("--task", help="Evaluate only this task_id")
    ap.add_argument("--candidate", help="Path to a .py candidate solution file")
    ap.add_argument("--selftest", action="store_true", help="Score reference_solution of every task")
    ap.add_argument("--baseline", action="store_true", help="Score buggy_code of every task")
    ap.add_argument("--out", help="Append score records to this JSONL file")
    args = ap.parse_args()

    tasks = load_cases(args.cases)
    if args.task:
        tasks = [t for t in tasks if t["task_id"] == args.task]
        if not tasks:
            sys.exit(f"No task with id {args.task}")

    if args.selftest:
        mode = "reference_solution"
    elif args.baseline:
        mode = "buggy_code"
    elif args.candidate:
        mode = "candidate"
        with open(args.candidate) as f:
            candidate_src = f.read()
    else:
        ap.error("Provide --candidate, --selftest, or --baseline")

    records = []
    for task in tasks:
        code = candidate_src if mode == "candidate" else task[mode]
        rec = score_task(task, code)
        rec["mode"] = mode
        records.append(rec)
        print(f"{rec['task_id']}  score={rec['score']:.3f}  "
              f"pub {rec['public_pass']}/{rec['public_total']}  "
              f"prv {rec['private_pass']}/{rec['private_total']}  "
              f"{rec['status']}  ({rec['latency_s']}s)")

    if records:
        mean = sum(r["score"] for r in records) / len(records)
        solved = sum(1 for r in records if r["solved"])
        print(f"\nMEAN SCORE: {mean:.4f}   SOLVED: {solved}/{len(records)}")

    if args.out:
        with open(args.out, "a") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")


if __name__ == "__main__":
    main()
