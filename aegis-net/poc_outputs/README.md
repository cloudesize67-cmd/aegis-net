# Aegis.net POC Outputs

This directory stores all proof-of-concept pipeline outputs.

Each run produces:
- `poc_output_TIMESTAMP.json` — full signed pipeline log with all agent outputs
- `poc_summary_TIMESTAMP.md` — human-readable summary with final answer

Every file committed here is verifiable proof of execution:
- Pipeline signature covers the entire agent chain
- Each agent output has its own HMAC-SHA256 signature
- Git commit history is the immutable audit log

## How to trigger a run
1. Go to Actions tab in GitHub
2. Select "Aegis.net — Proof of Concept Pipeline"
3. Click "Run workflow"
4. Type your task
5. Watch it run — output committed here automatically
