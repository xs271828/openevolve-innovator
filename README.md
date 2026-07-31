# OpenEvolve Scientist

[![CI](https://github.com/xs271828/openevolve-scientist/actions/workflows/ci.yml/badge.svg)](https://github.com/xs271828/openevolve-scientist/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/xs271828/openevolve-scientist)](https://github.com/xs271828/openevolve-scientist/releases/latest)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License](https://img.shields.io/github/license/xs271828/openevolve-scientist)](LICENSE)

OpenEvolve Scientist is a Codex Skill and platform-neutral research CLI for reproducible and auditable [OpenEvolve](https://github.com/algorithmicsuperintelligence/openevolve)-based algorithm-discovery experiments. Its default `codex-cli` backend uses a saved-login Codex CLI to automatically generate each OpenEvolve mutation, while the workflow adds baseline reproduction, holdout validation, ablation studies, budgets, checkpoint recovery, failure analysis, and novelty audits.

“Scientist” describes the research workflow, not autonomous scientific ability. This project is a governance layer around OpenEvolve—not a new evolution engine, not an AlphaEvolve implementation, not an official OpenEvolve component, and not a guarantee of algorithmic novelty.

[Install the Codex Skill](#codex-skill) · [Use the generic CLI](#generic-cli) · [Understand the relationship](#relationship-to-alphaevolve-and-openevolve) · [Read the limitations](#limitations) · [Machine-readable overview](llms.txt)

## Why use it?

OpenEvolve provides the evolutionary coding engine. OpenEvolve Scientist adds the controls needed to turn an optimization run into a reviewable research experiment:

- reproducible baselines and explicit search/holdout separation;
- multi-seed candidate validation, strong-baseline comparisons, and ablation studies;
- context-aware code limits and public Python API contract checks;
- cumulative iteration, wall-time, and estimated-cost budgets;
- Docker-first execution, resource profiles, OOM/timeout classification, and checkpoint-safe resume;
- failure analysis, prior-art review, novelty auditing, and explicit validity threats.

## Codex Skill

Download [`openevolve-scientist.zip`](https://github.com/xs271828/openevolve-scientist/releases/latest/download/openevolve-scientist.zip), unzip the `openevolve-scientist/` folder into your Codex skills directory, and start a new Codex task. Then invoke:

```text
$openevolve-scientist
```

The default `codex-cli` path uses a separately installed and saved-login Codex CLI as the automatic per-generation mutation model. It requires no external API key or model gateway, but it does require `openevolve==0.3.2`, `codex login`, and an account with available Codex usage. The child CLI runs are separate non-interactive tasks: they do not inherit this chat's hidden state. The Skill guides Codex through:

1. initializing a structured experiment;
2. automatically running bounded Codex-driven OpenEvolve searches, or configuring the legacy in-session, OpenAI-compatible, Claude Code, or manual backends;
3. validating the evaluator, public interface, context, resources, evidence, and budget;
4. running/resuming OpenEvolve safely and evaluating generated candidates locally;
5. comparing candidates on held-out data and multiple seeds;
6. generating a research report with ablations, failures, limitations, and claim status.

The Skill archive intentionally excludes this repository README, CI configuration, release tools, caches, credentials, and experiment outputs.

## Generic CLI

Any AI agent or human workflow that can execute Python and edit files can use the core CLI. With a saved-login Codex CLI, the default path is a real automatic evolution loop:

```text
python -m pip install openevolve==0.3.2
python skill/openevolve-scientist/scripts/openevolve_skill.py init experiment --name example --backend codex-cli
python skill/openevolve-scientist/scripts/openevolve_skill.py doctor experiment
python skill/openevolve-scientist/scripts/openevolve_skill.py validate experiment --for-run --mode host
python skill/openevolve-scientist/scripts/openevolve_skill.py run experiment --mode host --acknowledge-host-risk
```

For unattended external OpenEvolve runs, use Python 3.10+ and install the pinned engine in an isolated environment:

```text
python -m pip install openevolve==0.3.2
python skill/openevolve-scientist/scripts/openevolve_skill.py doctor
python skill/openevolve-scientist/scripts/openevolve_skill.py init experiment --name example --backend openai-compatible
python skill/openevolve-scientist/scripts/openevolve_skill.py validate experiment --for-run
```

For an OpenAI-compatible backend, set the model name, API base, minimum model context, and a credential environment variable before running. Never place a credential value in YAML.

## Model compatibility

| Backend | Typical models and services | Credentials | Execution |
|---|---|---|---|
| `codex-cli` | Saved-login Codex CLI (automatic per-generation mutations) | Codex CLI login; no API key | Host only in V1 |
| `codex-native` | Active Codex model in the current task (legacy manual candidate loop) | None | Codex host + local evaluator |
| `openai-compatible` | OpenAI, OpenRouter, LiteLLM, Ollama, vLLM, and compatible Gemini endpoints | One or more environment variables | Host or Docker |
| `claude-code` | Models available through an authenticated Claude Code CLI | Claude CLI session | Host only in V1 |
| `manual` | Any model, agent, or human process that answers queue files | None | Host or Docker |

“OpenAI-compatible” describes an API shape, not identical behavior. Private provider protocols that do not expose this shape require a gateway or the manual bridge.

Manual mode is transport-agnostic, not constraint-free: declare the smallest context window of the model or agent that actually answers queue requests. Use zero estimated cost only for a reviewed local, human, or otherwise unbilled responder; use a conservative upper bound for billed or uncertain responders. Codex CLI subscription use is controlled by iterations and wall time; the project does not misrepresent it as an exact USD meter.

## Relationship to AlphaEvolve and OpenEvolve

[AlphaEvolve](https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/) is Google DeepMind's evolutionary coding agent for general-purpose algorithm discovery and optimization. The upstream [OpenEvolve](https://github.com/algorithmicsuperintelligence/openevolve) project presents itself as an open-source implementation of the AlphaEvolve approach.

OpenEvolve Scientist is a separate workflow around OpenEvolve:

| Project | Role |
|---|---|
| AlphaEvolve | DeepMind research system for evolving algorithms with language models and automated evaluators |
| OpenEvolve | External open-source evolutionary coding engine used by this project |
| OpenEvolve Scientist | Codex Skill and generic CLI that add research protocol, validation, safety, budget, recovery, and reporting controls |

OpenEvolve Scientist does not implement or reproduce AlphaEvolve, is not affiliated with Google DeepMind or the OpenEvolve maintainers, and should not be presented as an implementation of or substitute for AlphaEvolve. It pins `openevolve==0.3.2` and does not redistribute OpenEvolve source code.

## Evidence and claim discipline

Reports use four evidence levels:

1. **Draft / not run-ready** — execution or evidence gates are incomplete.
2. **Candidate improvement** — the search evaluator beats the unchanged baseline, but independent evidence remains incomplete.
3. **Validated task-specific improvement** — holdout, multi-seed, weak/strong baseline, ablation, and limitations gates pass for the declared task distribution.
4. **Research novelty audited** — task validation and documented paper, code, and nearest-method audits are complete.

The final level records an audit outcome; it does not mathematically prove that no prior method exists. OpenEvolve diversity, code difference, or MAP-Elites coverage is not evidence of research novelty.

## Limitations

OpenEvolve Scientist does not guarantee a new algorithm or autonomously complete scientific research. `codex-cli` automates the mutation/search loop but every child run is a separate CLI task, affected by service availability, quotas, and model changes; it cannot recursively reuse this chat's private context. The workflow searches candidates against an evaluator, so reward hacking, development-set overfitting, leakage, and missing constraints remain central risks. Research novelty still requires prior-art review, strong baselines, held-out evaluation, ablations, and independent scrutiny.

Generated code is untrusted. Docker reduces host exposure but is not a complete sandbox, and code in the same container can access mounted experiment files and passed model credentials. Host mode has no meaningful isolation.

The current workflow is strongest for Python and one primary evolvable program. Large multi-file systems, GPU workloads, distributed search, exact provider billing, and native support for every private model API are outside its reliable scope. See [`references/limitations.md`](skill/openevolve-scientist/references/limitations.md) for the complete boundary.

## Migrating from OpenEvolve Innovator

Release [`v1.0.0`](https://github.com/xs271828/openevolve-innovator/releases/tag/v1.0.0) remains available and unchanged. Version 2 renames the Skill and invocation from `$openevolve-innovator` to `$openevolve-scientist`; the new archive intentionally contains no compatibility alias.

Existing experiment directories remain compatible because the CLI subcommands, `problem.yaml`, OpenEvolve configuration, budget ledger, run manifest, and summary schemas are unchanged. GitHub redirects the former repository URL after the repository rename, but new documentation and automation should use `xs271828/openevolve-scientist`.

## 中文快速开始

OpenEvolve Scientist 是面向 Codex 和其他可执行 Python 的 AI Agent 的 OpenEvolve 科研工作流。默认 `codex-cli` 后端会通过已登录的独立 Codex CLI，为每一代 OpenEvolve 搜索自动生成候选变异；工作流同时提供可复现基线、留出集验证、多随机种子复测、消融、预算、接口契约、失败分析和新颖性审计。

“Scientist”描述的是科研流程，不表示它能够自主完成科学研究。它不是“保证发明新算法”的工具，也不是 AlphaEvolve 或 OpenEvolve 官方组件。`codex-cli` 不需要外部 API Key，但需要安装独立 Codex CLI、完成 `codex login` 且账号仍有可用额度；每次变异是独立 CLI 任务，不能继承当前聊天的隐藏上下文。所谓模型通用，是指支持 Codex CLI、OpenAI-compatible 接口、Claude Code 和 manual queue；不兼容这些入口的模型需要通过网关或外部执行器连接。

将 Release 中的 `openevolve-scientist/` 文件夹放入 Codex skills 目录，开始一个新的 Codex 任务后调用：

```text
$openevolve-scientist
```

使用通用 CLI 初始化自动 Codex 演化实验（需要 Python 3.10+、`openevolve==0.3.2` 和已登录的 Codex CLI）：

```text
python skill/openevolve-scientist/scripts/openevolve_skill.py init experiment --name 示例 --backend codex-cli
python skill/openevolve-scientist/scripts/openevolve_skill.py doctor experiment
python skill/openevolve-scientist/scripts/openevolve_skill.py run experiment --mode host --acknowledge-host-risk
```

默认的 `codex-cli` 模式由 OpenEvolve 自动调用 Codex 生成并评估候选；旧的 `codex-native` 是当前会话内的手动候选循环。`manual`、`openai-compatible` 和 `claude-code` 仍可用于其他后端。

开始实验前必须阅读局限性说明，并在最终报告中完成任务特定的有效性威胁审计。

## License and attribution

This wrapper is licensed under Apache-2.0. OpenEvolve is an external Apache-2.0 dependency pinned to version 0.3.2; its source is not redistributed here. See [`NOTICE`](NOTICE).
