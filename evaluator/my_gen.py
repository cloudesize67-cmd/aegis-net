#!/usr/bin/env python3
"""
my_gen.py — Gemini generator for evolve.py (L1 of the self-training stack).

Contract (required by evolve.py):
    def generate(task, n, elites, gen, rng) -> list of candidate dicts

Rules baked in:
- API key comes from the GEMINI_API_KEY environment variable. Never hardcode it.
- The prompt contains ONLY: task['spec'], task['buggy_code'], and the previous
  generation's elite rationales. task['private_tests'] is NEVER included.
- Stdlib only (urllib) — runs on Termux without pip installs.

Setup on Termux:
    echo 'export GEMINI_API_KEY="your-key"' >> ~/.bashrc && source ~/.bashrc
    # optional: export GEMINI_MODEL="gemini-2.5-flash"

Smoke test (1 task, 1 candidate):
    python my_gen.py --selftest --cases code-repair-test-cases.jsonl

Real run:
    python evolve.py --cases code-repair-test-cases.jsonl \
        --generator-file my_gen.py --generations 3 --n 4 \
        --out results.jsonl --traces sft_traces.jsonl
"""

import argparse
import importlib.util
import json
import os
import random
import re
import urllib.request

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{}:generateContent"

PROMPT_TEMPLATE = """You are repairing a Python function so it passes unit tests.

Specification:
{spec}

Buggy code:
```python
{buggy}
```
{elite_block}
First line: one sentence stating the ROOT CAUSE of the bug (this is logged as your rationale).
Then output the complete corrected file in a single ```python code block.
No other explanation."""

# Rough per-token USD prices for logging (flash-class; adjust if you change models).
PRICE_IN_PER_TOKEN = 0.30 / 1_000_000
PRICE_OUT_PER_TOKEN = 2.50 / 1_000_000


def _call_api(prompt, api_key):
    url = ENDPOINT.format(MODEL) + "?key=" + api_key
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.8},
    }).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def _extract(text):
    m = re.search(r"```python\n(.*?)```", text, re.S)
    code = (m.group(1) if m else text).strip() + "\n"
    first = text.strip().splitlines()[0] if text.strip() else ""
    return code, first[:300]


def generate(task, n, elites, gen, rng):
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set. Export it first (see module docstring).")

    elite_block = ""
    if elites:
        notes = "; ".join(e.get("rationale", "")[:120] for e in elites if e.get("rationale"))
        if notes:
            elite_block = f"Previous attempts' root-cause notes (improve on them): {notes}\n"

    prompt = PROMPT_TEMPLATE.format(spec=task["spec"], buggy=task["buggy_code"],
                                    elite_block=elite_block)
    cands = []
    for _ in range(n):
        try:
            resp = _call_api(prompt, api_key)
            text = resp["candidates"][0]["content"]["parts"][0]["text"]
            code, rationale = _extract(text)
            usage = resp.get("usageMetadata", {})
            pt = usage.get("promptTokenCount", 0)
            ct = usage.get("candidatesTokenCount", 0)
            cost = pt * PRICE_IN_PER_TOKEN + ct * PRICE_OUT_PER_TOKEN
            cands.append({"code": code, "rationale": rationale,
                          "prompt_tokens": pt, "completion_tokens": ct,
                          "api_cost_usd": round(cost, 6)})
        except Exception as e:  # never crash the loop; log and fall back
            cands.append({"code": task["buggy_code"],
                          "rationale": f"[generator error: {e}]",
                          "prompt_tokens": 0, "completion_tokens": 0,
                          "api_cost_usd": 0.0})
    return cands


def main():
    ap = argparse.ArgumentParser(description="Gemini generator for evolve.py")
    ap.add_argument("--selftest", action="store_true",
                    help="Generate 1 candidate for CR-001 and score it with the evaluator")
    ap.add_argument("--cases", default="code-repair-test-cases.jsonl")
    ap.add_argument("--evaluator", default="code-repair-evaluator.py")
    args = ap.parse_args()

    if args.selftest:
        ev = importlib.util.spec_from_file_location("creval", args.evaluator)
        mod = importlib.util.module_from_spec(ev)
        ev.loader.exec_module(mod)
        task = next(t for t in mod.load_cases(args.cases) if t["task_id"] == "CR-001")
        cand = generate(task, 1, [], 0, random.Random(0))[0]
        rec = mod.score_task(task, cand["code"])
        print("rationale:", cand["rationale"])
        print("tokens:", cand["prompt_tokens"], "in /", cand["completion_tokens"], "out",
                  " cost=$%.6f" % cand["api_cost_usd"])
        print("score: %.3f  (pub %d/%d, prv %d/%d)" % (
            rec["score"], rec["public_pass"], rec["public_total"],
            rec["private_pass"], rec["private_total"]))
        print("--- candidate code ---")
        print(cand["code"])


if __name__ == "__main__":
    main()
