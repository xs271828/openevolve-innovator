# OpenEvolve interface

## Compatibility

- Tested package: `openevolve==0.3.2`
- Supported Python: 3.10+
- Package: <https://pypi.org/project/openevolve/0.3.2/>
- Source: <https://github.com/algorithmicsuperintelligence/openevolve>
- License: Apache-2.0

This skill calls OpenEvolve as an external dependency and does not redistribute its source.

## Model backends

The runner exposes three transport profiles:

| Backend | OpenEvolve configuration | Credentials | Docker |
|---|---|---|---|
| `openai-compatible` | `provider: openai` plus `api_base` | One or more `${ENV_VAR}` references | Supported |
| `claude-code` | `provider: claude_code` | Authenticated `claude` CLI session | Host-only in V1 |
| `manual` | `manual_mode: true` | None; answers arrive through the manual queue | Supported |

OpenAI-compatible endpoints cover many hosted gateways and local servers, but providers may differ in parameter support, token accounting, context handling, errors, and reproducibility. Unknown provider names are rejected because OpenEvolve 0.3.2 otherwise falls back to its OpenAI backend.

Manual mode removes the API transport requirement; it does not remove model constraints. Set `model_context.minimum_context_window_tokens` to the smallest context window of the model or agent that will actually answer queue requests. Set `estimated_cost_per_iteration_usd: 0` only when the responder is a reviewed local model, an unbilled human workflow, or another setup with no marginal billed cost; otherwise use a conservative upper-bound estimate.

For ensembles, each model may reference a different environment variable. The runner discovers every exact `${ENV_VAR}` reference and passes only those variable names to Docker. Never put a literal key in YAML or an API URL.

In Docker, use `host.docker.internal` for a model server running on the host. The runner adds the Linux host-gateway mapping only when that hostname is configured; it rejects `localhost`, `127.0.0.1`, and `::1` because those addresses point back to the container.

## Program contract

Place mutable code between:

```python
# EVOLVE-BLOCK-START
# code OpenEvolve may change
# EVOLVE-BLOCK-END
```

Keep interfaces, test data, secrets, and holdout logic outside the block. Prefer a small evolvable function over an entire application.

The template evaluator derives a contract from public top-level functions, classes, and public methods in `initial_program.py`. It rejects candidates that remove them or change parameter names, kinds, or defaults. Keep `program_contract.enforce_public_signatures: true` unless interface evolution is an explicit part of the research question; disabling it produces a validation warning.

## Evaluator contract

Define:

```python
def evaluate(program_path):
    return {
        "combined_score": 0.0,
        "correctness": 0.0,
        "runtime_seconds": 0.0,
    }
```

An `EvaluationResult` with `metrics` and `artifacts` is also valid. Custom feature metrics must be raw values; OpenEvolve performs binning.

Use `evaluate_stage1`, `evaluate_stage2`, and optionally `evaluate_stage3` only when cascade evaluation is enabled. Stage 1 should cheaply reject syntax, correctness, timeout, and contract failures.

## Configuration requirements

- Declare at least one model under `llm.models`.
- Use only `openai`, `claude_code`, or `manual_mode`; do not rely on provider fallback.
- Use environment variables for credentials. Never place API keys in YAML.
- Set `random_seed` and `database.random_seed`.
- Set `early_stopping_metric: combined_score`.
- Enable JSONL evolution trace and include generated code for reproducibility.
- Keep output and checkpoint paths inside the experiment directory.

OpenEvolve 0.3.2 exposes `memory_limit_mb` and `cpu_limit`, but its source marks those resource limits as not implemented. Do not treat them as a sandbox.

## Context and code budget

Declare the smallest context window among all configured models in `model_context.minimum_context_window_tokens`. The runner does not infer this value from model names.

The preflight reserves model output, fixed prompt, and artifact tokens; then divides the remaining input budget across the current program and configured historical examples. It calculates a desired code-growth limit from the initial program, then caps it by:

- the estimated prompt-context capacity;
- the generation capacity for diff or full-rewrite mode;
- `code_budget.hard_max_chars`;
- a legacy top-level `config.yaml max_code_length`, when present.

The resolved value is written to `results/effective_config.yaml`; the source `config.yaml` remains unchanged. Treat the calculation as a conservative preflight estimate, not an exact tokenizer result.

## Runtime resources

Docker profiles are:

| Profile | Memory | CPUs | PID limit | tmpfs |
|---|---:|---:|---:|---:|
| `standard` | 4 GB | 2 | 256 | 512 MB |
| `large` | 16 GB | 4 | 512 | 2 GB |

Use `runtime.docker` for persistent experiment settings. For one run, `--docker-profile`, `--docker-memory`, and `--docker-cpus` take precedence. Values are passed as structured argv after validation. Docker exit state is inspected before container cleanup so an OOM kill is not reported as an algorithm failure.

## Cumulative budget

`budget.max_iterations` and `budget.max_wall_time_minutes` apply to the whole experiment across run and resume segments. If `max_estimated_cost_usd` is set, declare `estimated_cost_per_iteration_usd`; use `0` only for a reviewed local-model setup.

The runner reserves budget before execution, stores cumulative state in `results/budget_ledger.json`, and appends segment records to `results/run_segments.jsonl`. It reconciles completed iterations from the trace when possible. If progress cannot be measured reliably, it charges the reservation so a crash cannot reset the budget.

USD accounting is an enforced estimate. A true billing hard limit requires a provider or gateway credential with its own spend cap.

## Result evidence files

Use the exact configured method names in `results/baseline_comparison.csv`. Use `method=candidate` for the evolved candidate and `split=holdout` for locked holdout results. A validated task-specific claim requires the candidate, one configured weak baseline, and one configured strong baseline to share at least three holdout seeds, with the candidate beating the strongest measured baseline in each category.

For `results/ablation_results.csv`, record `split=holdout`, a stable `removed_component`, at least three seeds, and numeric `combined_score` and `delta_vs_full`. The report generator treats missing, malformed, search-set-only, or single-seed rows as incomplete evidence.

## Docker and host modes

Docker mode mounts only the experiment directory at `/experiment`, drops Linux capabilities, enables `no-new-privileges`, and limits memory, CPU, and process count. Network remains available because the model API usually requires it. A unique container name allows post-run OOM inspection; the wrapper removes the container after recording its state.

OpenEvolve and generated candidate code share the container and required provider credential. Docker therefore protects the host better but does not make the model credential secret from a malicious candidate. Pass only the credential referenced by `config.yaml`; prefer a local model, a scoped gateway token, or a short-lived key with a strict spend limit.

Host mode executes generated code with the current user's permissions. Static inspection cannot make future LLM-generated candidates safe. Require explicit user confirmation for each host run.

Read [limitations.md](limitations.md) for the full scientific, model, program, security, resource, and cost boundaries.

## Output layout

OpenEvolve writes the final candidate to:

```text
openevolve_output/best/best_program.<suffix>
openevolve_output/best/best_program_info.json
```

Checkpoints contain corresponding `best_program` and `best_program_info.json` files.
