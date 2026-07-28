---
name: openevolve-innovator
description: Evolve, test, and audit candidate algorithms with OpenEvolve through reproducible research experiments. Use when an AI coding agent needs to invent or optimize executable algorithms, explore alternative algorithmic strategies, build rigorous evaluators and baselines, connect OpenAI-compatible, Claude Code, or manual model backends, control search resources and cost, run ablations, or determine whether an evolved result may be genuinely novel.
---

# OpenEvolve Innovator

Turn an optimization question into a reproducible search for candidate algorithmic innovations. Treat OpenEvolve as the search engine and use this skill to enforce evidence, evaluation, resource, safety, and novelty gates.

## Use the runner

Resolve a Python 3.10+ executable; do not assume `python` is on `PATH`. Set `SKILL_DIR` to this skill directory and invoke:

```text
python <SKILL_DIR>/scripts/openevolve_skill.py doctor
python <SKILL_DIR>/scripts/openevolve_skill.py init <experiment-dir> --name "<name>" --backend <openai-compatible|claude-code|manual>
python <SKILL_DIR>/scripts/openevolve_skill.py validate <experiment-dir>
python <SKILL_DIR>/scripts/openevolve_skill.py run <experiment-dir>
python <SKILL_DIR>/scripts/openevolve_skill.py resume <experiment-dir> --checkpoint <path>
python <SKILL_DIR>/scripts/openevolve_skill.py summarize <experiment-dir>
```

Run `--help` on a command before guessing an option. Use the Python environment that contains `openevolve==0.3.2`. Do not install Python, Docker, OpenEvolve, or model clients without user approval.

Use `openai-compatible` for cloud APIs, gateways, Ollama, or vLLM; use `claude-code` only in acknowledged host mode with an authenticated Claude CLI; use `manual` for a file-queue bridge to another model or human. OpenAI compatibility does not imply identical model behavior. Manual mode still requires the real responder's smallest context window and a conservative per-iteration cost estimate; use zero only for a reviewed unbilled setup.

## Follow the workflow

1. Read [limitations.md](references/limitations.md), then run `doctor`. Report missing dependencies without exposing environment-variable values. Use `doctor --docker-smoke` when Docker is available before publication or first container use.
2. Run `init` in an empty or nonexistent experiment directory.
3. Define one executable objective, hard constraints, the smallest model context window, code-growth policy, Docker resources, and estimated per-iteration cost in `problem.yaml`.
4. Search current papers and repositories. Write dated, linked evidence to `research/prior_art.md`. Read [research-protocol.md](references/research-protocol.md) before evaluating novelty.
5. Implement `initial_program.py` and `evaluator.py`. Preserve only intended code inside `EVOLVE-BLOCK` markers.
6. Evaluate the unchanged initial program for at least three declared seeds. Store one JSON object per run in `results/baseline_runs.jsonl`.
7. Separate search data from holdout data. Never expose holdout labels or results to evolution prompts or search-time artifacts.
8. Run `validate --for-run`; inspect the resolved context/code budget, runtime resources, and remaining cumulative budget.
9. Use Docker by default. Select `standard` or `large`, or pass reviewed `--docker-memory` and `--docker-cpus` overrides. Explain that Docker limits host access but does not hide the model credential from candidate code in the same container. Prefer a local model or a short-lived, spend-limited proxy credential.
10. Run on the host only after explaining that generated code can access host files, processes, network, and secrets, and obtaining explicit confirmation for that run. Then pass `--mode host --acknowledge-host-risk`.
11. Preserve `openevolve_output`, checkpoints, trace, effective config, budget ledger, and run manifest. Resume rather than restarting after an interruption; resumed work consumes the same cumulative budget.
12. Re-evaluate top candidates on holdout data and multiple seeds. Compare weak and strong baselines, run component ablations, and record failures.
13. Complete `research/novelty_audit.md`, then run `summarize`.
14. Complete `research/limitations.md`. If it remains incomplete, keep every result explicitly provisional.

## Design the evaluator

Read [openevolve-interface.md](references/openevolve-interface.md) before changing the evaluator or config.

- Return a numeric `combined_score`; higher must always be better.
- Return raw continuous values for custom MAP-Elites dimensions.
- Make correctness a hard gate before rewarding speed, cost, or elegance.
- Penalize timeouts, invalid output, constraint violations, and failed trials.
- Use several cases and seeds; report reliability and resource metrics separately.
- Keep holdout evaluation outside the search evaluator.
- Return concise artifacts that help repair failures without leaking holdout answers.
- Keep the public-signature guard enabled unless intentional interface evolution is documented.

## Enforce claim discipline

Use **draft / not run-ready** before execution gates pass. Use **candidate improvement** only when the search score beats the unchanged baseline across at least three runs. Upgrade to **validated task-specific improvement** only when all of these hold:

- the unchanged baseline and strong external baselines were reproduced;
- the candidate, one configured weak baseline, and one configured strong baseline have at least three common held-out seeds;
- the candidate beats the strongest measured method in both baseline categories;
- at least one component ablation has numeric deltas across three held-out seeds;
- the task-specific limitations audit is complete.

Use **research novelty audited** only after those gates pass and dated paper/code searches plus the nearest-method audit are complete. This wording reports an audit, not a proof of novelty.

Never infer research novelty from OpenEvolve's internal diversity or embedding-similarity score.

Never claim universal native model support. This Skill supports OpenAI-compatible transports, Claude Code, and a manual queue; other private protocols require a compatible gateway or external adapter.

## Deliver results

Keep these outputs:

- `openevolve_output/` for checkpoints, database, trace, and logs;
- `results/effective_config.yaml`, `results/budget_ledger.json`, `results/run_segments.jsonl`, and `results/run_manifest.json`;
- `results/summary.json`, `results/best_program.*`, comparison CSVs, and failure cases;
- `research/prior_art.md`, `research/novelty_audit.md`, and `research_report.md`.
- `research/limitations.md` with task-specific validity threats and unsupported generalizations.

Match the report language requested in `problem.yaml`. Label configured per-iteration USD accounting as an estimate, never as provider-measured billing.
