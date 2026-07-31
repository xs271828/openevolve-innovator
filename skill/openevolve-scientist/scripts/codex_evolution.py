#!/usr/bin/env python3
"""Run OpenEvolve with Codex CLI as the per-candidate mutation backend.

This wrapper deliberately uses ``codex exec`` instead of an API key.  It is
launched by ``openevolve_skill.py`` only after the experiment and the user's
saved Codex CLI login have been checked.  The child Codex process is read-only:
OpenEvolve receives its response and remains the only process that writes
candidate programs and evaluates them.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence


CODEX_COMMAND = "codex"
CODEX_EXECUTABLE_ENV = "OPENEVOLVE_CODEX_CLI"
DEFAULT_MODEL_NAMES = {"", "default", "codex-default", "codex-current-session"}
logger = logging.getLogger(__name__)


class CodexCLILLM:
    """Small OpenEvolve-compatible async adapter for the saved-login Codex CLI."""

    def __init__(self, model_cfg: Any) -> None:
        self.model = getattr(model_cfg, "name", "default") or "default"
        self.system_message = getattr(model_cfg, "system_message", None)
        self.timeout = int(getattr(model_cfg, "timeout", 300) or 300)
        self.retries = int(getattr(model_cfg, "retries", 2) or 0)
        self.retry_delay = float(getattr(model_cfg, "retry_delay", 5) or 0)
        self.weight = float(getattr(model_cfg, "weight", 1.0) or 1.0)
        self.cwd = getattr(model_cfg, "_codex_cli_cwd", None)

    async def generate(self, prompt: str, **kwargs: Any) -> str:
        system_message = kwargs.pop("system_message", self.system_message) or ""
        return await self.generate_with_context(
            system_message,
            [{"role": "user", "content": prompt}],
            **kwargs,
        )

    async def generate_with_context(
        self,
        system_message: str,
        messages: List[Dict[str, str]],
        **kwargs: Any,
    ) -> str:
        prompt = compose_prompt(system_message, messages)
        timeout = int(kwargs.get("timeout", self.timeout) or self.timeout)
        retries = int(kwargs.get("retries", self.retries) or 0)
        for attempt in range(retries + 1):
            try:
                return await self._run(prompt, timeout)
            except (RuntimeError, asyncio.TimeoutError) as exc:
                if attempt >= retries:
                    raise
                logger.warning(
                    "Codex CLI generation attempt %s/%s failed: %s",
                    attempt + 1,
                    retries + 1,
                    exc,
                )
                await asyncio.sleep(self.retry_delay)
        raise RuntimeError("Codex CLI generation failed")

    async def _run(self, prompt: str, timeout: int) -> str:
        command = codex_exec_command(self.model)
        kwargs: dict[str, Any] = {
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "cwd": self.cwd,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        process = await asyncio.create_subprocess_exec(*command, **kwargs)
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(prompt.encode("utf-8")), timeout=timeout
            )
        except (asyncio.TimeoutError, asyncio.CancelledError):
            await stop_process(process)
            raise
        output = stdout.decode("utf-8", errors="replace").strip()
        error = stderr.decode("utf-8", errors="replace").strip()
        if process.returncode != 0:
            raise RuntimeError(
                f"codex exec exited with {process.returncode}: {error[:500]}"
            )
        if not output:
            raise RuntimeError(f"codex exec returned an empty response: {error[:500]}")
        return output


def compose_prompt(system_message: str, messages: Sequence[Dict[str, str]]) -> str:
    """Preserve OpenEvolve's response contract while keeping the child read-only."""
    parts: list[str] = []
    if system_message.strip():
        parts.append("# OpenEvolve system instructions\n" + system_message.strip())
    for message in messages:
        content = str(message.get("content", "")).strip()
        if content:
            parts.append(content)
    parts.append(
        """# Codex execution constraints
You are the mutation model inside an automated OpenEvolve experiment. Return the
requested program rewrite or exact SEARCH/REPLACE diff directly in stdout.
Respect the response format specified above. Do not wrap it in commentary or
Markdown fences unless that format explicitly requires them. Do not run shell commands,
call nested agents, access files, or attempt to evaluate the program:
the parent OpenEvolve process performs evaluation and records the trace."""
    )
    return "\n\n".join(parts)


def codex_exec_command(model: str | None = None) -> list[str]:
    """Return a non-interactive, read-only Codex invocation with no API key argv."""
    command = [
        codex_executable(),
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
    ]
    if model and model.strip().lower() not in DEFAULT_MODEL_NAMES:
        command.extend(["--model", model.strip()])
    command.append("-")
    return command


def codex_executable() -> str:
    configured = os.environ.get(CODEX_EXECUTABLE_ENV)
    if configured and Path(configured).is_file():
        return configured

    # The desktop app may put a protected WindowsApps stub first on PATH.  A
    # user-installed standalone CLI is the runnable command for child runs.
    app_data = os.environ.get("APPDATA")
    if app_data:
        npm_cli = Path(app_data) / "npm" / "codex.cmd"
        try:
            npm_cli_exists = npm_cli.is_file()
        except OSError:
            # A restricted preflight sandbox may not be allowed to stat the
            # user profile.  The real child process receives the resolved
            # executable from the runner when it can inspect that location.
            npm_cli_exists = False
        if npm_cli_exists:
            return str(npm_cli)

    for suffix in ("", ".cmd", ".exe"):
        discovered = shutil.which(f"{CODEX_COMMAND}{suffix}")
        if discovered and "\\WindowsApps\\" not in discovered:
            return discovered
    return CODEX_COMMAND


async def stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.terminate()
        await asyncio.wait_for(process.wait(), timeout=5)
        return
    except (OSError, asyncio.TimeoutError, ProcessLookupError):
        pass
    try:
        process.kill()
        await asyncio.wait_for(process.wait(), timeout=5)
    except (OSError, asyncio.TimeoutError, ProcessLookupError):
        pass


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run OpenEvolve with the saved-login Codex CLI mutation backend"
    )
    parser.add_argument("initial_program")
    parser.add_argument("evaluation_file")
    parser.add_argument("--config", "-c", required=True)
    parser.add_argument("--output", "-o", required=True)
    parser.add_argument("--iterations", "-i", type=int, required=True)
    parser.add_argument("--target-score", "-t", type=float)
    parser.add_argument("--checkpoint")
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    executable = codex_executable()
    if executable == CODEX_COMMAND and not shutil.which(CODEX_COMMAND):
        raise RuntimeError("Codex CLI was not found on PATH")
    from openevolve import OpenEvolve
    from openevolve.config import load_config

    config = load_config(args.config)
    if not config.llm.models:
        raise RuntimeError("config.yaml must define llm.models for the codex-cli backend")
    experiment_root = str(Path(args.initial_program).resolve().parent)
    for model_cfg in config.llm.models + config.llm.evaluator_models:
        model_cfg.init_client = lambda cfg: CodexCLILLM(cfg)
        model_cfg._codex_cli_cwd = experiment_root
    controller = OpenEvolve(
        initial_program_path=args.initial_program,
        evaluation_file=args.evaluation_file,
        config=config,
        output_dir=args.output,
    )
    best_program = await controller.run(
        iterations=args.iterations,
        target_score=args.target_score,
        checkpoint_path=args.checkpoint,
    )
    metrics = getattr(best_program, "metrics", {}) if best_program else {}
    print(f"Evolution complete. Best metrics: {metrics}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
