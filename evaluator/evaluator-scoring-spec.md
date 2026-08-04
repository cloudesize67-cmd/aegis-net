# Code-Repair Evaluator — Scoring Spec v1.0

**Vertical:** automated repair of small Python functions.
**Why this vertical:** correctness is *machine-checkable* (tests execute), bugs have known ground-truth fixes, and improvement is measurable per dollar of compute. This is the AlphaFold/AlphaEvolve pattern applied to a domain you can build on a phone.

**Files in this package**

| File | Role |
|---|---|
| `evaluator-scoring-spec.md` | This document — the contract everything else obeys |
| `code-repair-test-cases.jsonl` | First graded batch: 15 tasks, difficulties 1–3, 15 distinct bug categories |
| `code-repair-evaluator.py` | Stdlib-only harness. Runs candidates in isolated subprocesses, emits JSONL score records |

Validation status: `--selftest` (reference solutions) = **1.000 on 15/15 tasks**; `--baseline` (buggy code) = **0.391 mean, 0/15 solved**. The harness separates working code from broken code with graded resolution.

---

## 1. Task schema (one JSON object per line)

| Field | Type | Meaning |
|---|---|---|
| `task_id` | string | Stable ID, `CR-NNN`. Never reuse an ID. |
| `title` | string | Human label. |
| `difficulty` | 1–3 | 1 = single-line bug, 2 = logic/edge case, 3 = semantic/spec bug. |
| `bug_category` | string | Off-by-one, aliasing, wrong base case, etc. Used for per-category analytics. |
| `entry_point` | string | Function the tests import. |
| `spec` | string | Natural-language intended behavior. **This is the agent's only problem statement.** |
| `buggy_code` | string | Starting code given to the agent. |
| `public_tests` | string | Python source, `test_*` functions. Visible to the agent during repair. |
| `private_tests` | string | Python source, `test_*` functions. **Held out from the agent.** |
| `reference_solution` | string | Known-good fix. Never shown to the agent; used for harness self-checks. |
| `timeout_sec` | int | Per-suite wall-clock limit (default 10). |

## 2. Scoring formula

For each task:

```
public_rate  = public_passed  / public_total
private_rate = private_passed / private_total
score        = 0.35 * public_rate + 0.65 * private_rate     # range [0, 1]
solved       = (score == 1.0)
```

- **Private tests weigh 0.65** so a candidate cannot win by overfitting to what it can see. This is the single most important anti-gaming decision in the system.
- Timeout or non-importable code scores 0 for that split.
- Partial credit is deliberate: a repair that fixes 3 of 4 edge cases scores higher than one that fixes none, giving the evolutionary loop a gradient to climb instead of a flat 0/1 landscape.

**Aggregate metrics per run** (this is the chart investors see):

- `mean_score` across the batch
- `solve_rate` = tasks solved / total
- `score_by_difficulty` and `score_by_bug_category`
- `cost per +0.01 mean score` = total API spend / mean-score gain vs. previous run

## 3. Anti-gaming rules (grounded-truth integrity)

1. **Structural separation.** The candidate only ever writes `solution.py`. Test files live outside its reach — tests cannot be edited by the agent because they are never in its workspace.
2. **Held-out split.** Private tests are never placed in the agent's context, prompt, or logs shown to it.
3. **Edge-case probes.** Every task's private suite includes at least one input class absent from public tests (empty input, all-negative, aliasing mutation, out-of-range probe). Hardcoding public answers fails privately.
4. **Reference self-check.** Any change to the JSONL must be followed by `--selftest` = 1.000 everywhere. A broken test file is indistinguishable from a broken agent — guard the tests first.
5. **Determinism.** No randomness, no network, no wall-clock dependence inside tasks. Same candidate → same score, always. If a future task needs randomness, seed it inside the test file.

## 4. Run-record schema (append to results JSONL)

```json
{
  "task_id": "CR-007",
  "difficulty": 2,
  "bug_category": "boundary_error",
  "public_pass": 2, "public_total": 2,
  "private_pass": 3, "private_total": 3,
  "score": 1.0, "solved": true, "status": "ok",
  "latency_s": 0.07,
  "failures": {"public": [], "private": []},
  "mode": "candidate",
  "run_id": "run-2026-08-03-001",
  "model": "<model used to produce the fix>",
  "prompt_tokens": 0, "completion_tokens": 0, "api_cost_usd": 0.0
}
```

The last four fields are filled by the orchestrator that calls the model — log them without exception. **Cost is part of the score story**: the whole pitch is capability-per-dollar.

## 5. The loop this evaluator plugs into

This harness is step 5 (evaluation oracle) of the planning loop. The full agent cycle for each task:

1. **Goal spec** → the task's `spec` + passing all tests
2. **State model** → `buggy_code`, public test failures, traceback text
3. **Gap analysis** → the agent must state, in one sentence, *why* the code fails before patching (log this — it becomes training data)
4. **Acquisition** → read the failing test output; re-run with probe inputs it invents
5. **Evaluate** → this harness. Score feeds the evolutionary selector: keep mutations that raise private-test pass rate

Evolutionary pass (AlphaEvolve pattern, minimal version): generate N candidate patches per task with a cheap model → score all N → keep top-k → mutate winners (another N patches) → repeat G generations → log the curve. Mean score vs. generation vs. dollars spent is your proof-of-concept chart.

## 6. Extending the batch

Rules for new tasks (target: 50, then 200):

- Realistic bugs only — the kind a tired human writes, not puzzles.
- Every task needs: a public test the buggy code *might* partially pass, and a private test that specifically kills the obvious wrong fix.
- Include the `reference_solution` and run `--selftest` before committing the task.
- Balance difficulty: roughly 40% level-1, 40% level-2, 20% level-3.
- New bug categories are welcome; add them to the analytics groupby.

Suggested next categories: timezone/date handling, unicode case-folding, float precision, iterator exhaustion, exception-swallowing, off-by-one in slicing, wrong sort key on tuples, integer overflow in other languages ported to Python semantics.

## 7. PoC success criteria (what "investable" means here)

| Milestone | Threshold |
|---|---|
| Harness works end-to-end | ✅ done — 15/15 selftest, baseline caught |
| Batch size | 50 tasks, then 200 |
| Self-improvement curve | mean score rises across ≥5 generations on the same budget |
| Cost efficiency | solve a level-2 task for < $0.01 average API cost |
| Auditability | every score reproducible from run-records alone |

When the first four rows are green, you have a demo: *"this system measurably teaches itself to fix code, and here is the receipt for every point of improvement."*
