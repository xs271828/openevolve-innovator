# Limitations

Read this before starting an experiment and again before interpreting a result.

## Scientific limits

- “Scientist” names a structured research workflow; it does not mean the Skill can autonomously formulate complete scientific programs, establish truth, or replace expert review.
- OpenEvolve and the native Codex loop produce candidate improvements; they cannot establish research novelty. Novelty requires dated paper and code searches, strong-baseline reproduction, holdout evaluation, ablations, and independent scrutiny.
- The evaluator defines the optimization target. Incomplete tests can cause reward hacking, overfitting, data leakage, invalid shortcuts, or apparently strong but unusable candidates.
- Internal diversity, embedding similarity, MAP-Elites coverage, or generated-code difference is not evidence of a new algorithm.
- Seeds improve bookkeeping but cannot remove nondeterminism from remote model services, provider routing, changing model weights, hardware, or asynchronous execution.

## Codex-native and model limits

- `codex-native` uses the active Codex model through the Skill instructions; the Python runner cannot invoke the current Codex session as an OpenEvolve plugin or recursively call Codex. It therefore provides a bounded, agent-managed loop rather than an unattended model server.
- A native run depends on the active agent actually following the candidate/evaluate/record protocol. The runner can validate files and budgets, but it cannot guarantee that a conversational agent performed every research step unless the artifacts document it.
- A generic AI agent must still be able to run commands and edit files. A chat-only model needs a human or external executor.
- OpenAI-compatible describes a protocol surface, not identical semantics. Providers differ in supported parameters, token counting, reasoning controls, context truncation, errors, and seed behavior.
- The implementation has no native adapter for every private provider API. Use an OpenAI-compatible gateway, the Claude Code backend, or manual queue for unsupported models.
- Claude Code is host-only by default. Manual mode can connect any model but adds queue latency and external orchestration.

## Program and evaluator limits

- The default workflow is optimized for Python and one primary program file. Large multi-file repositories, GPU jobs, distributed evaluators, and interactive systems are outside the current reliable envelope.
- The public-contract guard checks top-level Python functions, classes, methods, and signatures. It does not prove semantic behavior, typing, state, performance, security, or side-effect equivalence.
- Context and code limits use conservative character/token estimates rather than the selected model's exact tokenizer.

## Security, resource, and cost limits

- Generated code is untrusted. Docker reduces host exposure but is not a complete sandbox: network access remains enabled, the experiment is mounted, and model credentials passed to the container are visible to code in that container.
- Host and native modes run local evaluator code with the current user's permissions and provide no file, process, network, memory, or CPU isolation.
- Docker limits vary across Linux, Docker Desktop, operating systems, and local-model networking. The default template does not configure GPUs.
- USD limits are enforced estimates based on configured per-iteration cost. They are not provider billing measurements; use provider or gateway spend caps for a true hard limit.
- When completed iterations cannot be reconciled, the budget ledger charges the reserved amount to fail closed.
