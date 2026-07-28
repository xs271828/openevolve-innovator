# Limitations and validity threats

STATUS: incomplete

## Inherited limitations

- OpenEvolve produces candidate improvements; it does not prove research novelty.
- Evaluator misspecification can reward hacking, development-set overfitting, leakage, or constraint bypasses.
- Model-side nondeterminism can remain even when seeds and configuration are recorded.
- OpenAI-compatible endpoints do not guarantee identical parameters, token accounting, errors, or reasoning behavior.
- Docker reduces host exposure but does not hide mounted experiment files or in-container model credentials from candidate code.
- Estimated USD accounting is not a provider billing hard limit.
- The default contract guard checks Python signatures, not semantic equivalence or side effects.
- V1 is designed around Python and one primary evolvable program, not large multi-file, GPU, or distributed workloads.

## Task-specific threats

- TODO: identify untested inputs, environments, scale ranges, or operating conditions.
- TODO: document likely evaluator blind spots and ways a candidate could exploit them.
- TODO: state what populations, datasets, systems, or workloads the result must not be generalized to.

## Residual risk

- TODO: record unresolved safety, reproducibility, cost, or external-validity risks.

Replace every TODO with evidence-based text and set `STATUS: complete` before making a validated innovation claim.
