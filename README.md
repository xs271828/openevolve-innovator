# OpenEvolve Innovator

A research-grade, model-backend-agnostic workflow for evolving, validating, and auditing candidate algorithms with [OpenEvolve](https://github.com/algorithmicsuperintelligence/openevolve).

OpenEvolve Innovator combines a platform-neutral Python CLI with a Codex Skill adapter. Any AI agent that can execute commands and edit files can use the core workflow; Codex users can invoke the packaged Skill as `$openevolve-innovator`.

## What it adds

- reproducible baselines, search/holdout separation, ablations, failure analysis, and novelty audits;
- context-aware code limits, public Python API contract checks, cumulative budgets, and checkpoint-safe resume;
- Docker-first execution with resource profiles and OOM/timeout classification;
- OpenAI-compatible, Claude Code, and manual queue model transports;
- explicit limitations and validity-threat reporting.

## Model compatibility

| Backend | Typical models and services | Credentials | Execution |
|---|---|---|---|
| `openai-compatible` | OpenAI, OpenRouter, LiteLLM, Ollama, vLLM, and compatible Gemini endpoints | One or more environment variables | Host or Docker |
| `claude-code` | Models available through an authenticated Claude Code CLI | Claude CLI session | Host only in V1 |
| `manual` | Any model, agent, or human process that answers queue files | None | Host or Docker |

“OpenAI-compatible” describes an API shape, not identical behavior. Private provider protocols that do not expose this shape require a gateway or the manual bridge.

Manual mode is transport-agnostic, not constraint-free: declare the smallest context window of the model or agent that actually answers queue requests. Use zero estimated cost only for a reviewed local, human, or otherwise unbilled responder; use a conservative upper bound for billed or uncertain responders.

## Generic CLI

Use Python 3.10+ and install the pinned external engine in an isolated environment:

```text
python -m pip install openevolve==0.3.2
python skill/openevolve-innovator/scripts/openevolve_skill.py doctor
python skill/openevolve-innovator/scripts/openevolve_skill.py init experiment --name example --backend openai-compatible
python skill/openevolve-innovator/scripts/openevolve_skill.py validate experiment --for-run
```

For an OpenAI-compatible backend, set the model name, API base, minimum model context, and a credential environment variable before running. Never place a credential value in YAML.

## Codex Skill

Install the release asset `openevolve-innovator.zip` as a Codex Skill, then invoke:

```text
$openevolve-innovator
```

The Skill archive intentionally excludes this README, repository CI, release tools, caches, credentials, and experiment outputs.

## Limitations

OpenEvolve Innovator does not guarantee a new algorithm. It searches candidates against an evaluator, so reward hacking, development-set overfitting, leakage, and missing constraints remain central risks. Research novelty still requires prior-art review, strong baselines, held-out evaluation, ablations, and independent scrutiny.

Generated code is untrusted. Docker reduces host exposure but is not a complete sandbox, and code in the same container can access mounted experiment files and passed model credentials. Host mode has no meaningful isolation.

The V1 workflow is strongest for Python and one primary evolvable program. Large multi-file systems, GPU workloads, distributed search, exact provider billing, and native support for every private model API are outside its reliable scope. See `skill/openevolve-innovator/references/limitations.md` for the complete boundary.

Reports use four evidence levels: draft/not run-ready, candidate improvement, validated task-specific improvement, and research novelty audited. The final level still means that a documented audit was completed, not that novelty was mathematically proven.

## 中文快速开始

OpenEvolve Innovator 把 OpenEvolve 封装成可复现的候选算法搜索流程，并增加基线、留出集、消融、预算、Docker、接口契约和新颖性审计。

它并非“保证发明新算法”的工具。所谓模型通用，是指支持 OpenAI-compatible 接口、Claude Code 和 manual queue；不兼容这些入口的模型需要通过网关或外部执行器连接。

manual 模式仍要填写实际回答模型的最小上下文窗口；只有经过确认的本地模型、人工或其他不计费流程才能把每轮估算成本设为 0。

```text
python skill/openevolve-innovator/scripts/openevolve_skill.py init experiment --name 示例 --backend manual
```

开始实验前必须阅读局限性说明，并在最终报告中完成任务特定的有效性威胁审计。

## License and attribution

This wrapper is licensed under Apache-2.0. OpenEvolve is an external Apache-2.0 dependency pinned to version 0.3.2; its source is not redistributed here. See `NOTICE`.
