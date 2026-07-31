---
name: openevolve-scientist
description: Run reproducible, auditable OpenEvolve algorithm-discovery experiments. By default, use the saved-login Codex CLI as the automatic mutation backend, then validate candidates with baselines, holdout data, ablations, budgets, and novelty audits without overstating evidence.
---

# OpenEvolve Scientist

Turn an algorithm question into a bounded, reproducible candidate-discovery experiment. Read [limitations.md](references/limitations.md) before starting and before writing conclusions. “Scientist” describes a research workflow: it does not prove novelty or guarantee a new algorithm.

## Default: automatic Codex-driven evolution

For a normal Codex task, use `codex-cli`. It launches the separately installed, saved-login Codex CLI once per OpenEvolve generation. OpenEvolve then selects parents, receives diffs or rewrites, evaluates candidates, writes traces, checkpoints, and supports resume. It uses no external API key.

```text
python <SKILL_DIR>/scripts/openevolve_skill.py init <experiment-dir> --name "<name>" --backend codex-cli
python <SKILL_DIR>/scripts/openevolve_skill.py doctor <experiment-dir>
python <SKILL_DIR>/scripts/openevolve_skill.py validate <experiment-dir> --for-run --mode host
python <SKILL_DIR>/scripts/openevolve_skill.py run <experiment-dir> --mode host --acknowledge-host-risk
python <SKILL_DIR>/scripts/openevolve_skill.py resume <experiment-dir> --mode host --checkpoint <checkpoint> --acknowledge-host-risk
python <SKILL_DIR>/scripts/openevolve_skill.py summarize <experiment-dir>
```

`doctor` must find a standalone `codex` executable with `codex exec` and a successful `codex login status`. The child CLI has its own saved login; it does **not** recursively reuse this chat's hidden state. It runs read-only and returns only the OpenEvolve mutation response; the parent OpenEvolve process owns evaluation and experiment files. Codex account quotas and rate limits still apply.

Prefer the existing Anaconda/conda environment for local evaluation. Do not install packages or create environments without approval. `codex-cli` is host-only in V1 because Docker cannot safely reuse the host Codex login. Host execution of generated code requires explicit confirmation on every run.

When the user gives a problem:

1. Define one executable objective, hard constraints, a search/holdout split, context/code limits, and stop budgets.
2. Read the research protocol and limitations. For research claims, record dated papers, code, and strong baselines.
3. Implement or inspect `initial_program.py` and `evaluator.py`. Keep mutable code inside `EVOLVE-BLOCK`; preserve the public contract.
4. Reproduce the unchanged baseline on at least three search seeds. Never use holdout labels during evolution.
5. Run the automatic search only after `validate --for-run` passes. Monitor the trace and stop reason; do not silently enlarge limits or budgets.
6. Freeze search, then compare the selected candidate and baselines on holdout data across the declared seeds. Run component ablations and failure analysis.
7. Run `summarize` and report the candidate, measured evidence, reproduction command, stop reason, and limitations. Incomplete gates mean “candidate improvement,” not “new algorithm.”

## Other backends and compatibility

`codex-native` remains a legacy, in-session manual loop: the active agent writes and evaluates a small candidate set, and `run`/`resume` intentionally return guidance instead of starting OpenEvolve. It is not automatic evolution.

Use other backends only when requested:

```text
python <SKILL_DIR>/scripts/openevolve_skill.py init <experiment-dir> --name "<name>" --backend <openai-compatible|claude-code|manual>
```

Use `openai-compatible` for cloud APIs, gateways, Ollama, or vLLM; `claude-code` only in acknowledged host mode with an authenticated Claude CLI; and `manual` for a file queue. Never put credential values in configuration, prompts, logs, or candidate code. Use the Python environment containing `openevolve==0.3.2`.

## Non-negotiable research and safety gates

- Higher `combined_score` must always mean better, and correctness must be a hard evaluator gate.
- Reproduce the unchanged baseline before evolution; keep search and holdout data separate.
- Docker reduces risk but is not a complete sandbox. Host mode has no memory, CPU, file, network, or process isolation.
- Enforce declared context, code-growth, iteration, wall-clock, and resource budgets. Codex subscription use has no exact per-call USD invoice in this workflow; do not claim otherwise.
- Keep the public-signature guard enabled unless interface evolution is intentional and documented.
- Do not infer novelty from score, code difference, diversity, or MAP-Elites. Complete a dated nearest-method audit before calling anything novel.

## Outputs

Automatic `codex-cli` and external runs write `openevolve_output/`, including trace, checkpoints, and best program. All modes use `results/` for effective configuration, budget ledger, run manifest, summaries, comparisons, and ablations; use `research/` for prior art, novelty audit, and task-specific limitations.

Before changing the evaluator or configuration, read [openevolve-interface.md](references/openevolve-interface.md). Before novelty claims, read [research-protocol.md](references/research-protocol.md).
