# Research protocol

## Before search

Define:

- the task distribution and intended user;
- one primary metric and hard correctness constraints;
- search, development, and holdout data boundaries;
- compute, iteration, latency, and cost budgets;
- weak and strong baselines;
- conditions that would falsify the proposed improvement.

Record at least three independent baseline runs. Use this JSONL schema:

```json
{"seed": 11, "combined_score": 0.61, "program_sha256": "<sha256>", "dataset": "search", "notes": ""}
```

The `program_sha256` must match the unchanged `initial_program.py`.

## Prior-art search

Search papers and source repositories. Record the search date, query, URL or DOI, method summary, closest overlap, and remaining difference. Include negative searches when a promising query found nothing.

Use at least:

- one scholarly index or publisher search;
- arXiv or an equivalent preprint source;
- GitHub or another source-code index.

Do not use repository stars, automated reviewer scores, or LLM assertions as novelty evidence.

## Search and confirmation

Use search data only during evolution. Lock the holdout data before the first evolution run. Do not tune prompts, weights, thresholds, or code after seeing holdout outcomes; if tuning resumes, create a new holdout split.

Re-evaluate finalists with at least three seeds. Report mean, spread, failures, and resource metrics. Compare against the unchanged initial program and at least one strong external baseline.

## Ablation and failure analysis

Remove or revert one proposed component at a time. Record component, seed, score, delta, and interpretation in `results/ablation_results.csv`.

Store machine-readable failures in `results/failure_cases.jsonl`, including input identifier, expected behavior, observed behavior, category, severity, and candidate identifier.

## Claim levels

Use the narrowest wording supported by the recorded evidence:

1. **Draft / not run-ready**: validation gates, evaluator evidence, or baseline reproduction are incomplete. Do not call it an improvement.
2. **Candidate improvement**: the search evaluator score exceeds the unchanged baseline mean across at least three runs, but independent holdout, strong-baseline, replicated ablation, or limitations evidence is incomplete.
3. **Validated task-specific improvement**: the candidate, a configured weak baseline, and a configured strong baseline share at least three locked holdout seeds; the candidate beats the strongest measured method in both baseline categories; at least one component ablation has numeric deltas across three holdout seeds; and the limitations audit is complete. Scope the claim to the declared task distribution.
4. **Research novelty audited**: all task-specific validation gates pass and the dated paper, code, nearest-method, and novelty audits are complete. This records an audit outcome, not a proof that no prior method exists.

Separately distinguish code novelty, task-specific novelty, and research novelty inside `research/novelty_audit.md`. Set `STATUS: complete` only after every required search and nearest-method comparison is documented. Do not use OpenEvolve diversity metrics as evidence for any claim level.

## Reporting

Report objective, data boundaries, baseline protocol, search budget, model/provider, OpenEvolve version, best candidate, holdout results, ablations, failures, cost/latency, limitations, and novelty status. Label unavailable measurements as unmeasured.

Complete `research/limitations.md` with task-specific evaluator blind spots, untested environments, unsupported generalizations, and residual risk. An incomplete limitations audit keeps the result provisional even when the novelty audit is complete.
