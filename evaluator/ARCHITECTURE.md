# ARCHITECTURE — Recursive Self-Training Stack (RLHF-free)

**One-sentence design goal:** replace human feedback with *verifiable ground truth + evaluator-driven selection*, so the system generates its own training data, filters it by machine-checkable correctness, and fine-tunes itself — recursively.

This is not a new invention; it is a disciplined composition of four proven ideas:

| Prior art | What it proved | What we take |
|---|---|---|
| **RLVR** (verifiable rewards) | Models improve dramatically when the reward is *checkable* (math answers, passing tests) instead of human preference | Our evaluator IS the reward signal |
| **STaR / ReST** (iterated self-training) | Generate rationales, keep only the ones that reach correct answers, fine-tune on them, repeat — the model bootstraps itself | Our trace-harvest → SFT loop |
| **Constitutional AI / RLAIF** | AI judges can replace human labelers for domains without programmatic checkers | Fallback oracle for non-verifiable tasks — always subordinate to programmatic checks |
| **AlphaEvolve** | An LLM + a programmatic evaluator + evolutionary selection discovers better algorithms than direct prompting | Our generate→score→select→mutate loop |

---

## The six layers

```
┌─────────────────────────────────────────────────────────────┐
│ L5 GOVERNANCE  held-out canary set · contamination checks · │
│                score reproducibility · small human audit    │
├─────────────────────────────────────────────────────────────┤
│ L4 TRAINING    traces → LoRA SFT (open model) → new generator│
│                optional DPO on evaluator-ranked pairs       │
├─────────────────────────────────────────────────────────────┤
│ L3 SEARCH LOOP evolve.py: generate N → score → keep top-k → │
│                mutate → G generations · harvests traces     │
├─────────────────────────────────────────────────────────────┤
│ L2 EVALUATOR   code-repair-evaluator.py — programmatic      │
│                oracle. Private tests weighted 0.65          │
├─────────────────────────────────────────────────────────────┤
│ L1 GENERATORS  today: cheap API models · later: your own    │
│                fine-tuned open-weight model (L4 output)     │
├─────────────────────────────────────────────────────────────┤
│ L0 GROUND TRUTH task batches (JSONL): spec, buggy code,     │
│                public + held-out private tests, reference   │
└─────────────────────────────────────────────────────────────┘
```

**L0 — Ground truth.** Everything stands on task batches whose correctness is machine-checkable. Built: 15 tasks, validated. Target: 50 → 200. This is the moat — anyone can call an API, nobody else has your graded, held-out, self-checked task distribution.

**L1 — Generators.** Pluggable. Today: whatever cheap model API you have (the mock mode in `evolve.py` validates the pipeline at zero cost; a real generator is a 5-line insertion). Later: the model *you* fine-tuned in L4 — that is what makes the loop recursive instead of rented.

**L2 — Evaluator.** Built and validated. Binary gate: no human taste involved, only "does the code pass tests it has never seen."

**L3 — Search loop.** `evolve.py`. Per task per generation: N candidate patches → evaluator scores → top-k elites survive → next generation mutates elites. Every scoring event is logged as a run-record (task, score, tokens, cost). Every candidate scoring exactly 1.0 is harvested with its rationale into `sft_traces.jsonl`.

**L4 — Training (the RLHF replacement proper).** Two stages, neither uses a human label:
1. **SFT (STaR/ReST pattern):** `sft_traces.jsonl` = (spec + failing tests → rationale → verified fix) pairs where *the evaluator certified correctness*. LoRA fine-tune a small open model (Unsloth + free Colab GPU is enough at this scale). The fine-tuned model becomes the L1 generator for the next iteration.
2. **DPO (optional, later):** every task where generation produced both a 1.0 and a <1.0 candidate yields a (winner, loser) preference pair — AI feedback derived from ground truth, not from humans.

**L5 — Governance.** The honest version of "no human feedback": humans stop *labeling*, they don't stop *auditing*. A small held-out canary set is never trained on and never shown to generators; every claimed improvement must reproduce on it. Sample-audit traces for nonsense rationales that accidentally pass (reward hacking). Log provenance of every trace.

## The recursive loop

```
iteration i:
  1. SEARCH:   generator_i attempts all tasks × N candidates × G generations
  2. EVALUATE: oracle scores everything (0 humans involved)
  3. HARVEST:  traces with score == 1.0 + rationale → dataset_i
  4. TRAIN:    LoRA SFT on dataset_0..i → generator_{i+1}
  5. MEASURE:  generator_{i+1} scored on the CANARY set (never trained on)
  6. GATE:     accept generator_{i+1} only if canary score > generator_i's
  → repeat
```

The PoC demo is the step-6 chart: **canary score rising across iterations with zero human labels.** That single chart is the fundable artifact.

## Failure modes (and the designed mitigations)

| Failure | What it looks like | Mitigation |
|---|---|---|
| Reward hacking | Code that passes tests without solving the problem | Private split (0.65 weight), edge-case probes, audit samples |
| Eval contamination | Generator memorized the tests | Canary set never enters any prompt, log, or training trace |
| Distribution collapse | Model overfits 15 tasks, loses generality | Grow batch before training; keep difficulty mix; cap iterations per batch |
| Rationale nonsense | Right answer, garbage reasoning (correlation not causation) | Audit sample per iteration; DPO stage penalizes |
| Metric drift | "Improvement" is an artifact of harness changes | Harness is versioned in git; selftest must stay 1.000 |

## Feasibility ladder — what runs where

| Layer | Hardware | Cost | Status |
|---|---|---|---|
| L0–L3 (tasks, evaluator, search loop, trace harvesting) | Your phone (Termux) | $0 with mock generator; pennies with a flash-class API | **Built & validated** |
| L4 SFT (LoRA on a 1–3B open model, thousands of traces) | Free Colab GPU tier | $0 | Next build step |
| L4 DPO | Same | $0 | Later |
| Full RL at scale | Real GPUs | Real money | Not needed for the PoC — don't buy it |

## Why this is "useful to people" (the product framing)

The PoC's outward face is a **self-improving code-repair agent**: point it at a buggy function, it returns a fix *proven* by tests, and it measurably gets better every week without anyone labeling data. The inward asset is the auditable pipeline: every improvement has a receipt (run-records + traces + canary scores). Usefulness for users = working fixes. Usefulness for investors = the receipt.
