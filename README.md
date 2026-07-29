# OpenEvolve Innovator

[![CI](https://github.com/xs271828/openevolve-innovator/actions/workflows/ci.yml/badge.svg)](https://github.com/xs271828/openevolve-innovator/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/xs271828/openevolve-innovator)](https://github.com/xs271828/openevolve-innovator/releases/latest)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/github/license/xs271828/openevolve-innovator)](LICENSE)

OpenEvolve Innovator is a Codex Skill and platform-neutral CLI for reproducible [OpenEvolve](https://github.com/algorithmicsuperintelligence/openevolve)-based algorithm discovery and evolutionary coding experiments. It helps AI agents evolve and validate candidate algorithms with baseline reproduction, holdout validation, ablation studies, budget and resource controls, checkpoint recovery, failure analysis, and novelty audits.

It is a research workflow and governance layer around OpenEvolve—not a new evolution engine, not an AlphaEvolve implementation, and not a guarantee of algorithmic novelty.

[Install the Codex Skill](#codex-skill) · [Use the generic CLI](#generic-cli) · [Understand the relationship](#relationship-to-alphaevolve-and-openevolve) · [Read the limitations](#limitations) · [Machine-readable overview](llms.txt)

## Why use it?

OpenEvolve provides the evolutionary coding engine. OpenEvolve Innovator adds the controls needed to turn an optimization run into a reviewable research experiment:

- reproducible baselines and explicit search/holdout separation;
- multi-seed candidate validation, strong-baseline comparisons, and ablation studies;
- context-aware code limits and public Python API contract checks;
- cumulative iteration, wall-time, and estimated-cost budgets;
- Docker-first execution, resource profiles, OOM/timeout classification, and checkpoint-safe resume;
- failure analysis, prior-art review, novelty auditing, and explicit validity threats.

## Codex Skill

Download [`openevolve-innovator.zip`](https://github.com/xs271828/openevolve-innovator/releases/latest/download/openevolve-innovator.zip), install it as a Codex Skill, and invoke:

```text
$openevolve-innovator
```

The Skill guides Codex through:

1. initializing a structured experiment;
2. configuring OpenAI-compatible, Claude Code, or manual model backends;
3. validating the evaluator, public interface, context, resources, evidence, and budget;
4. running or resuming OpenEvolve safely;
5. comparing candidates on held-out data and multiple seeds;
6. generating a research report with ablations, failures, limitations, and claim status.

The Skill archive intentionally excludes this repository README, CI configuration, release tools, caches, credentials, and experiment outputs.

## Generic CLI

Any AI agent or human workflow that can execute Python and edit files can use the core CLI. Use Python 3.10+ and install the pinned external engine in an isolated environment:

```text
python -m pip install openevolve==0.3.2
python skill/openevolve-innovator/scripts/openevolve_skill.py doctor
python skill/openevolve-innovator/scripts/openevolve_skill.py init experiment --name example --backend openai-compatible
python skill/openevolve-innovator/scripts/openevolve_skill.py validate experiment --for-run
```

For an OpenAI-compatible backend, set the model name, API base, minimum model context, and a credential environment variable before running. Never place a credential value in YAML.

## Model compatibility

| Backend | Typical models and services | Credentials | Execution |
|---|---|---|---|
| `openai-compatible` | OpenAI, OpenRouter, LiteLLM, Ollama, vLLM, and compatible Gemini endpoints | One or more environment variables | Host or Docker |
| `claude-code` | Models available through an authenticated Claude Code CLI | Claude CLI session | Host only in V1 |
| `manual` | Any model, agent, or human process that answers queue files | None | Host or Docker |

“OpenAI-compatible” describes an API shape, not identical behavior. Private provider protocols that do not expose this shape require a gateway or the manual bridge.

Manual mode is transport-agnostic, not constraint-free: declare the smallest context window of the model or agent that actually answers queue requests. Use zero estimated cost only for a reviewed local, human, or otherwise unbilled responder; use a conservative upper bound for billed or uncertain responders.

## Relationship to AlphaEvolve and OpenEvolve

[AlphaEvolve](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/) is Google DeepMind's evolutionary coding agent for general-purpose algorithm discovery and optimization. The upstream [OpenEvolve](https://github.com/algorithmicsuperintelligence/openevolve) project presents itself as an open-source implementation of the AlphaEvolve approach.

OpenEvolve Innovator is a separate workflow around OpenEvolve:

| Project | Role |
|---|---|
| AlphaEvolve | DeepMind research system for evolving algorithms with language models and automated evaluators |
| OpenEvolve | External open-source evolutionary coding engine used by this project |
| OpenEvolve Innovator | Codex Skill and generic CLI that add research protocol, validation, safety, budget, recovery, and reporting controls |

OpenEvolve Innovator does not implement or reproduce AlphaEvolve, is not affiliated with Google DeepMind, and should not be presented as an implementation of or substitute for AlphaEvolve. It pins `openevolve==0.3.2` and does not redistribute OpenEvolve source code.

## Evidence and claim discipline

Reports use four evidence levels:

1. **Draft / not run-ready** — execution or evidence gates are incomplete.
2. **Candidate improvement** — the search evaluator beats the unchanged baseline, but independent evidence remains incomplete.
3. **Validated task-specific improvement** — holdout, multi-seed, weak/strong baseline, ablation, and limitations gates pass for the declared task distribution.
4. **Research novelty audited** — task validation and documented paper, code, and nearest-method audits are complete.

The final level records an audit outcome; it does not mathematically prove that no prior method exists. OpenEvolve diversity, code difference, or MAP-Elites coverage is not evidence of research novelty.

## Limitations

OpenEvolve Innovator does not guarantee a new algorithm. It searches candidates against an evaluator, so reward hacking, development-set overfitting, leakage, and missing constraints remain central risks. Research novelty still requires prior-art review, strong baselines, held-out evaluation, ablations, and independent scrutiny.

Generated code is untrusted. Docker reduces host exposure but is not a complete sandbox, and code in the same container can access mounted experiment files and passed model credentials. Host mode has no meaningful isolation.

The V1 workflow is strongest for Python and one primary evolvable program. Large multi-file systems, GPU workloads, distributed search, exact provider billing, and native support for every private model API are outside its reliable scope. See [`references/limitations.md`](skill/openevolve-innovator/references/limitations.md) for the complete boundary.

## 中文快速开始

OpenEvolve Innovator 是面向 Codex 和其他可执行 Python 的 AI Agent 的 OpenEvolve 科研工作流。它为候选算法搜索增加可复现基线、留出集验证、多随机种子复测、消融、预算、Docker、接口契约、失败分析和新颖性审计。

它不是“保证发明新算法”的工具，也不是 AlphaEvolve 的实现。所谓模型通用，是指支持 OpenAI-compatible 接口、Claude Code 和 manual queue；不兼容这些入口的模型需要通过网关或外部执行器连接。

安装 Release 中的 Codex Skill 后调用：

```text
$openevolve-innovator
```

使用通用 CLI 初始化 manual 后端实验：

```text
python skill/openevolve-innovator/scripts/openevolve_skill.py init experiment --name 示例 --backend manual
```

开始实验前必须阅读局限性说明，并在最终报告中完成任务特定的有效性威胁审计。

## License and attribution

This wrapper is licensed under Apache-2.0. OpenEvolve is an external Apache-2.0 dependency pinned to version 0.3.2; its source is not redistributed here. See [`NOTICE`](NOTICE).
