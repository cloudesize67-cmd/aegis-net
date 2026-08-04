#!/usr/bin/env python3
"""
evolve.py — evaluator-driven evolutionary search loop (AlphaEvolve pattern, minimal).

Per task, per generation: generate N candidate patches -> score each with the
ground-truth evaluator -> keep top-k elites -> mutate into the next generation.
Every scoring event is logged as a run-record; every candidate scoring exactly
1.0 is harvested (with its rationale) into an SFT trace dataset. Zero human
labels anywhere in the loop.

Generators
----------
--mock (default): deterministic simulated model whose hit rate improves by
    generation. Validates the full pipeline at $0. Clearly labeled mock.
--generator-file my_gen.py: plug in a REAL model. The file must define:

    def generate(task, n, elites, gen, rng):
        '''Return list of dicts: {"code": str, "rationale": str,
           "prompt_tokens": int, "completion_tokens": int, "api_cost_usd": float}'''

    This is where a Gemini/Claude/local-model call goes. Prompt the model with
    task['spec'] + task['buggy_code'] + public test failures from the previous
    generation's records. NEVER include task['private_tests'] in any prompt.

Usage:
  python evolve.py --cases code-repair-test-cases.jsonl --mock \
      --generations 4 --n 6 --topk 2 --seed 7 \
      --out results.jsonl --traces sft_traces.jsonl
"""

import argparse
import importlib.util
import json
import os
import random
import sys
import time


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- mock generator

def mock_generate(task, n, elites, gen, rng):
    """Simulated weak model: probability of emitting the correct fix rises with
    generation (0.10 + 0.25*gen, capped 0.95). Everything else is a no-op
    mutation of the buggy code. Deterministic under the run seed."""
    p_hit = min(0.10 + 0.25 * gen, 0.95)
    cands = []
    for _ in range(n):
        if rng.random() < p_hit:
            cands.append({
                "code": task["reference_solution"],
                "rationale": (f"[MOCK] identified bug category "
                              f"'{task['bug_category']}' and rewrote the faulty logic"),
                "prompt_tokens": 0, "completion_tokens": 0, "api_cost_usd": 0.0,
            })
        else:
            cands.append({
                "code": task["buggy_code"] + f"\n# mock mutation g{gen}\n",
                "rationale": "[MOCK] no-op mutation (still buggy)",
                "prompt_tokens": 0, "completion_tokens": 0, "api_cost_usd": 0.0,
            })
    return cands


# ---------------------------------------------------------------- evolution core

def evolve(cases_path, evaluator, generations, n, topk, seed, gen_mod, run_id):
    tasks = evaluator.load_cases(cases_path)
    elites = {t["task_id"]: [] for t in tasks}       # surviving candidates
    best = {t["task_id"]: None for t in tasks}       # best-ever record per task
    all_records = []
    traces = []

    for gen in range(generations):
        gen_records = []
        for task in tasks:
            tid = task["task_id"]
            rng = random.Random(f"{seed}:{tid}:{gen}")
            if gen_mod is None:
                cands = mock_generate(task, n, elites[tid], gen, rng)
            else:
                cands = gen_mod.generate(task, n, elites[tid], gen, rng)

            scored = []
            for idx, cand in enumerate(cands):
                rec = evaluator.score_task(task, cand["code"])
                rec.update({
                    "mode": "candidate", "run_id": run_id,
                    "generator": "mock" if gen_mod is None else "external",
                    "generation": gen, "candidate_idx": idx,
                    "rationale": cand.get("rationale", ""),
                    "prompt_tokens": cand.get("prompt_tokens", 0),
                    "completion_tokens": cand.get("completion_tokens", 0),
                    "api_cost_usd": cand.get("api_cost_usd", 0.0),
                })
                scored.append((rec, cand))
                gen_records.append(rec)

                if rec["score"] == 1.0 and not any(
                        tr["task_id"] == tid and tr["run_id"] == run_id for tr in traces):
                    traces.append({
                        "task_id": tid, "run_id": run_id, "generation": gen,
                        "spec": task["spec"], "buggy_code": task["buggy_code"],
                        "rationale": cand.get("rationale", ""),
                        "solution": cand["code"], "score": rec["score"],
                        "generator": rec["generator"],
                    })

            scored.sort(key=lambda sc: sc[0]["score"], reverse=True)
            elites[tid] = [c for _, c in scored[:topk]]
            if best[tid] is None or scored[0][0]["score"] > best[tid]["score"]:
                best[tid] = scored[0][0]

        all_records.extend(gen_records)
        mean_best = sum(b["score"] for b in best.values()) / len(best)
        solved = sum(1 for b in best.values() if b["score"] == 1.0)
        print(f"gen {gen}  mean_best={mean_best:.4f}  solved={solved}/{len(tasks)}"
              f"  traces={len(traces)}")

    return all_records, traces, best


def main():
    ap = argparse.ArgumentParser(description="Evaluator-driven evolutionary search loop.")
    ap.add_argument("--cases", required=True)
    ap.add_argument("--evaluator", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                        "code-repair-evaluator.py"))
    ap.add_argument("--mock", action="store_true", help="Use the mock generator ($0 pipeline check)")
    ap.add_argument("--generator-file", help="Python file defining generate() for a real model")
    ap.add_argument("--generations", type=int, default=4)
    ap.add_argument("--n", type=int, default=6, help="Candidates per task per generation")
    ap.add_argument("--topk", type=int, default=2)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", help="Append run-records JSONL here")
    ap.add_argument("--traces", help="Write harvested SFT traces JSONL here")
    args = ap.parse_args()

    if not args.mock and not args.generator_file:
        ap.error("Choose --mock or --generator-file")

    evaluator = load_module(args.evaluator, "creval")
    gen_mod = load_module(args.generator_file, "genmod") if args.generator_file else None
    run_id = f"run-{time.strftime('%Y-%m-%d-%H%M%S')}-s{args.seed}"

    records, traces, best = evolve(args.cases, evaluator, args.generations,
                                   args.n, args.topk, args.seed, gen_mod, run_id)

    mean_best = sum(b["score"] for b in best.values()) / len(best)
    total_cost = sum(r.get("api_cost_usd", 0.0) for r in records)
    print(f"\nFINAL  mean_best={mean_best:.4f}  "
          f"solved={sum(1 for b in best.values() if b['score'] == 1.0)}/{len(best)}"
          f"  traces={len(traces)}  cost=${total_cost:.4f}")

    if args.out:
        with open(args.out, "a") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
    if args.traces:
        with open(args.traces, "a") as f:
            for tr in traces:
                f.write(json.dumps(tr) + "\n")


if __name__ == "__main__":
    main()
