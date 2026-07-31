---
name: openevolve-scientist
description: Use the active Codex model or an optional external backend to discover, test, and audit candidate algorithms with reproducible OpenEvolve research workflows. Use when an AI coding agent needs to define an executable optimization problem, build baselines and evaluators, generate algorithm candidates, run local scoring, perform holdout validation and ablations, control resources and budgets, or assess novelty without overstating the evidence.
---

# OpenEvolve Scientist

Turn an algorithm question into a bounded, reproducible, auditable candidate-discovery experiment. Read [limitations.md](references/limitations.md) before starting and before writing conclusions. “Scientist” describes the workflow role; it does not mean autonomous scientific ability, prove novelty, or guarantee a new algorithm.

## Default: use the active Codex model

For a normal Codex request, use `codex-native`. This mode does not need an API key, model name, gateway, Docker, or a second model client. The current Codex agent is the candidate generator and reasoning controller; Python is the local evaluator.

Use the runner to create and inspect the experiment:

```text
python <SKILL_DIR>/scripts/openevolve_skill.py init <experiment-dir> --name "<name>" --backend codex-native
python <SKILL_DIR>/scripts/openevolve_skill.py validate <experiment-dir> --for-run --mode host
```

Prefer the existing Anaconda/conda environment for local evaluation. Do not create environments or install packages without approval. The native mode is an agent-managed bounded loop, not an unattended OpenEvolve subprocess and not a recursive call to Codex.

When the user gives a problem, perform the work in the current task:

1. Define the objective, constraints, search/holdout split, context/code budget, and stopping budget in the experiment files.
2. Read the limitations and research protocol. Search papers, code, and strong baselines when novelty or research claims matter; record dated sources.
3. Implement or inspect `initial_program.py` and `evaluator.py`. Keep intended mutations inside `EVOLVE-BLOCK`; preserve the public signature contract.
4. Run the unchanged baseline on at least three declared search seeds. Never use holdout labels during search.
5. Generate a small, bounded set of candidate programs yourself as the active Codex model. Each candidate must have a concrete hypothesis and a short mutation description. Do not claim that OpenEvolve generated it unless an external OpenEvolve run actually did.
6. Run the evaluator locally for every candidate, record one JSON line per candidate in `results/codex_native_trace.jsonl`, keep the best program, and stop when the configured iteration, wall-clock, or cost estimate is exhausted. Treat native model cost as unmeasured unless the user supplies an explicit accounting rule.
7. Re-test the selected candidate and baselines on holdout data and multiple seeds only after search is frozen. Run component ablations and failure analysis.
8. Write `results/summary.json`, `research_report.md`, and the required comparison/ablation files. Return the final answer with the candidate, measured evidence, reproduction command, and limitations. If required gates are incomplete, call it a provisional candidate improvement.

The runner's `run` and `resume` commands intentionally do not launch OpenEvolve for `codex-native`; they return guidance to follow this loop. Do not switch to an external API merely to make those commands run.

## Optional external OpenEvolve modes

Use the runner's other backends only when the user explicitly wants an unattended or separately hosted search:

```text
python <SKILL_DIR>/scripts/openevolve_skill.py doctor
python <SKILL_DIR>/scripts/openevolve_skill.py init <experiment-dir> --name "<name>" --backend <openai-compatible|claude-code|manual>
python <SKILL_DIR>/scripts/openevolve_skill.py validate <experiment-dir> --for-run
python <SKILL_DIR>/scripts/openevolve_skill.py run <experiment-dir>
python <SKILL_DIR>/scripts/openevolve_skill.py resume <experiment-dir> --checkpoint <path>
python <SKILL_DIR>/scripts/openevolve_skill.py summarize <experiment-dir>
```

Use `openai-compatible` for cloud APIs, gateways, Ollama, or vLLM; `claude-code` only in acknowledged host mode with an authenticated Claude CLI; and `manual` for a file queue. Never place credential values in configuration, logs, prompts, or candidate code. OpenAI compatibility does not imply identical model behavior. Use the Python environment containing the pinned `openevolve==0.3.2`; do not install it without approval.

## Non-negotiable research and safety gates

- Make correctness a hard evaluator gate; higher `combined_score` must always be better.
- Reproduce the unchanged baseline before evolution and keep search data separate from holdout data.
- Treat Docker as risk reduction, not a complete sandbox. Host execution requires explicit confirmation because candidate code can access host files, processes, network, and secrets.
- Enforce the declared model context, code-growth, Docker resource, wall-clock, iteration, and estimated-cost budgets. A dollar limit is an estimate, not a provider billing hard limit.
- Keep the public-signature guard enabled unless interface evolution is intentional and documented.
- Do not infer novelty from a score, diversity, similarity, or MAP-Elites archive. Complete a dated paper/code/nearest-method audit before using “novel”.
- If `research/limitations.md` still has TODOs, report only “candidate result; limitations audit incomplete”.

## Outputs

Keep `openevolve_output/` only for real external OpenEvolve runs. Native runs use `results/codex_native_trace.jsonl`, `results/best_program.*`, `results/summary.json`, baseline and ablation CSVs, and `research_report.md`. All modes keep prior-art, novelty-audit, limitations, effective-config, budget, and provenance records when applicable. Match the report language requested in `problem.yaml`.

Before changing the evaluator or OpenEvolve configuration, read [openevolve-interface.md](references/openevolve-interface.md). Before making novelty claims, read [research-protocol.md](references/research-protocol.md).
