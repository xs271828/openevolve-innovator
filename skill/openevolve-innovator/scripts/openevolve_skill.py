#!/usr/bin/env python3
"""Deterministic runner for the openevolve-innovator skill."""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import importlib.metadata
import importlib.util
import json
import math
import os
import platform
import re
import signal
import shutil
import statistics
import subprocess
import sys
import sysconfig
import time
import uuid
from urllib.parse import urlsplit, urlunsplit
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


PINNED_OPENEVOLVE_VERSION = "0.3.2"
HOST_ENTRYPOINT_MODULE = "openevolve.cli"
CONSOLE_ENTRYPOINT_NAME = "openevolve-run"
CONSOLE_ENTRYPOINT_TARGET = "openevolve.cli:main"
BACKEND_CHOICES = ("openai-compatible", "claude-code", "manual")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ENV_REFERENCE_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
SKILL_DIR = Path(__file__).resolve().parents[1]
TEMPLATE_DIR = SKILL_DIR / "assets" / "experiment-template"
REQUIRED_FILES = (
    "problem.yaml",
    "initial_program.py",
    "evaluator.py",
    "config.yaml",
    "Dockerfile",
    "requirements-evaluator.txt",
    "research/prior_art.md",
    "research/novelty_audit.md",
    "research/limitations.md",
    "results/baseline_comparison.csv",
    "results/ablation_results.csv",
)
PROVIDER_ENV_NAMES = (
    "MODEL_API_KEY",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "ANTHROPIC_API_KEY",
)
SECRET_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{16,}\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|secret)\s*[:=]\s*[\"']?([^$<{][^\s#\"']{7,})"),
)
RISKY_IMPORTS = {
    "ctypes",
    "ftplib",
    "http",
    "multiprocessing",
    "paramiko",
    "requests",
    "shutil",
    "smtplib",
    "socket",
    "subprocess",
    "urllib",
}
RISKY_CALLS = {"compile", "eval", "exec", "open", "__import__"}
RESOURCE_PROFILES: dict[str, dict[str, Any]] = {
    "standard": {
        "memory": "4g",
        "cpus": 2.0,
        "pids_limit": 256,
        "tmpfs_size": "512m",
    },
    "large": {
        "memory": "16g",
        "cpus": 4.0,
        "pids_limit": 512,
        "tmpfs_size": "2g",
    },
}
MEMORY_VALUE_RE = re.compile(r"^[1-9]\d*(?:\.\d+)?[kmgtKMGT]$")
DEFAULT_MODEL_CONTEXT = {
    "utilization": 0.8,
    "chars_per_token": 2.0,
    "fixed_prompt_reserve_tokens": 2048,
    "artifact_reserve_tokens": 4096,
}
DEFAULT_CODE_BUDGET = {
    "growth_ratio": 1.5,
    "growth_allowance_chars": 4000,
    "hard_max_chars": 20000,
}


class SkillError(RuntimeError):
    """Expected user-facing error."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_experiment(raw_path: str, require_exists: bool = True) -> Path:
    path = Path(raw_path).expanduser().resolve()
    if require_exists and not path.is_dir():
        raise SkillError(f"Experiment directory not found: {path}")
    return path


def path_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError:
        return load_simple_problem_yaml(path)
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return data if isinstance(data, dict) else {}


def load_simple_problem_yaml(path: Path) -> dict[str, Any]:
    """Parse the safe YAML subset used by problem.yaml without adding a dependency."""

    def scalar(value: str) -> Any:
        value = value.strip()
        if not value:
            return None
        if value.startswith(("'", '"')) and value.endswith(value[0]):
            return value[1:-1]
        if value.startswith("[") and value.endswith("]"):
            try:
                return json.loads(value.replace("'", '"'))
            except json.JSONDecodeError:
                return [scalar(item) for item in value[1:-1].split(",") if item.strip()]
        lowered = value.lower()
        if lowered in {"null", "~"}:
            return None
        if lowered in {"true", "false"}:
            return lowered == "true"
        try:
            return int(value)
        except ValueError:
            try:
                return float(value)
            except ValueError:
                return value

    tokens: list[tuple[int, str]] = []
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(raw_lines):
        raw_line = raw_lines[index]
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            index += 1
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        block_scalar = re.match(r"^([^:]+):\s*[|>][-+]?\s*$", stripped)
        if block_scalar:
            tokens.append((indent, f'{block_scalar.group(1).strip()}: ""'))
            index += 1
            while index < len(raw_lines):
                child = raw_lines[index]
                if child.strip() and len(child) - len(child.lstrip(" ")) <= indent:
                    break
                index += 1
            continue
        tokens.append((indent, stripped))
        index += 1

    def parse_block(index: int, indent: int) -> tuple[Any, int]:
        is_list = tokens[index][1].startswith("- ")
        container: Any = [] if is_list else {}
        while index < len(tokens):
            current_indent, content = tokens[index]
            if current_indent < indent:
                break
            if current_indent > indent:
                raise SkillError(f"Unsupported YAML indentation in {path.name}: {content}")
            if is_list:
                if not content.startswith("- "):
                    break
                item_text = content[2:].strip()
                if not item_text:
                    if index + 1 >= len(tokens) or tokens[index + 1][0] <= indent:
                        container.append(None)
                        index += 1
                    else:
                        child, index = parse_block(index + 1, tokens[index + 1][0])
                        container.append(child)
                    continue
                if ":" in item_text and not item_text.startswith(("'", '"')):
                    key, raw_value = item_text.split(":", 1)
                    item: dict[str, Any] = {key.strip(): scalar(raw_value)}
                    index += 1
                    if index < len(tokens) and tokens[index][0] > indent:
                        child, index = parse_block(index, tokens[index][0])
                        if isinstance(child, dict):
                            item.update(child)
                    container.append(item)
                    continue
                container.append(scalar(item_text))
                index += 1
                continue

            if ":" not in content:
                raise SkillError(f"Unsupported YAML line in {path.name}: {content}")
            key, raw_value = content.split(":", 1)
            key = key.strip()
            raw_value = raw_value.strip()
            index += 1
            if raw_value:
                container[key] = scalar(raw_value)
            elif index < len(tokens) and tokens[index][0] > indent:
                child, index = parse_block(index, tokens[index][0])
                container[key] = child
            else:
                container[key] = {}
        return container, index

    if not tokens:
        return {}
    parsed, final_index = parse_block(0, tokens[0][0])
    if final_index != len(tokens) or not isinstance(parsed, dict):
        raise SkillError(f"Unable to parse {path.name}")
    return parsed


def installed_openevolve_version() -> str | None:
    try:
        return importlib.metadata.version("openevolve")
    except importlib.metadata.PackageNotFoundError:
        return None


def redact_api_base(value: Any) -> tuple[str | None, list[str]]:
    if value is None:
        return None, []
    rendered = str(value).strip()
    if not rendered:
        return None, []
    if "REPLACE_WITH" in rendered or "TODO" in rendered.upper():
        return rendered, []
    errors: list[str] = []
    try:
        parsed = urlsplit(rendered)
    except ValueError:
        return rendered, ["api_base is not a valid URL"]
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        errors.append("OpenAI-compatible api_base must be an absolute http(s) URL")
        return rendered, errors
    if parsed.username or parsed.password:
        errors.append("api_base must not embed a username or password")
    sensitive_query = re.search(
        r"(?i)(?:api[_-]?key|token|secret|password|signature|credential)=",
        parsed.query,
    )
    if sensitive_query:
        errors.append("api_base must not contain sensitive query parameters")
    try:
        host = parsed.hostname or ""
        parsed_port = parsed.port
    except ValueError:
        host = ""
        parsed_port = None
        errors.append("api_base contains an invalid host or port")
    port = f":{parsed_port}" if parsed_port else ""
    safe_host = f"[{host}]" if ":" in host else host
    safe_netloc = f"{safe_host}{port}"
    return urlunsplit((parsed.scheme, safe_netloc, parsed.path, "", "")), errors


def resolve_model_runtime(
    experiment: Path,
    *,
    mode: str | None = None,
    for_run: bool = False,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    try:
        config = load_yaml(experiment / "config.yaml")
    except (OSError, SkillError) as exc:
        return {
            "status": "invalid",
            "backend_types": [],
            "models": [],
            "credential_environment_names": [],
            "credential_presence": {},
            "requires_host_gateway": False,
            "errors": [f"Unable to read model configuration: {exc}"],
            "warnings": [],
        }

    llm = config.get("llm")
    if not isinstance(llm, dict):
        llm = {}
        errors.append("config.yaml must define an llm mapping")
    raw_models = llm.get("models", [])
    if not isinstance(raw_models, list) or not raw_models:
        errors.append("config.yaml llm.models must contain at least one model")
        raw_models = []

    top_provider = llm.get("provider")
    manual_mode = llm.get("manual_mode") is True
    credentials: set[str] = set()
    backends: set[str] = set()
    model_records: list[dict[str, Any]] = []
    requires_host_gateway = False
    top_api_key = llm.get("api_key")
    if isinstance(top_api_key, str):
        top_reference = ENV_REFERENCE_RE.fullmatch(top_api_key.strip())
        if top_reference:
            credentials.add(top_reference.group(1))
        elif top_api_key.strip():
            errors.append("llm.api_key must use a ${ENV_VAR} reference")
    elif top_api_key is not None:
        errors.append("llm.api_key must be a ${ENV_VAR} string reference")

    for index, raw_model in enumerate(raw_models):
        if not isinstance(raw_model, dict):
            errors.append(f"llm.models[{index}] must be a mapping")
            continue
        name = raw_model.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"llm.models[{index}].name is required")
            name = ""
        elif for_run and ("REPLACE_WITH" in name or "TODO" in name.upper()):
            errors.append(f"Replace the model placeholder in llm.models[{index}].name")

        provider = raw_model.get("provider", top_provider)
        if manual_mode:
            if provider == "claude_code":
                errors.append("manual_mode cannot be combined with provider: claude_code")
            backend = "manual"
        elif provider in {None, "", "openai"}:
            backend = "openai-compatible"
        elif provider == "claude_code":
            backend = "claude-code"
        else:
            errors.append(
                f"Unsupported llm provider {provider!r}; use openai, claude_code, or manual_mode"
            )
            backend = "invalid"
        backends.add(backend)

        record: dict[str, Any] = {"name": name, "backend": backend}
        explicit_api_key = raw_model.get("api_key")
        if isinstance(explicit_api_key, str):
            explicit_reference = ENV_REFERENCE_RE.fullmatch(explicit_api_key.strip())
            if explicit_reference:
                credentials.add(explicit_reference.group(1))
            elif explicit_api_key.strip():
                errors.append(
                    f"llm.models[{index}].api_key must use a ${{ENV_VAR}} reference"
                )
        elif explicit_api_key is not None:
            errors.append(
                f"llm.models[{index}].api_key must be a ${{ENV_VAR}} string reference"
            )
        if backend == "openai-compatible":
            api_base = raw_model.get("api_base", llm.get("api_base"))
            redacted_base, url_errors = redact_api_base(api_base)
            errors.extend(f"llm.models[{index}]: {item}" for item in url_errors)
            if for_run and (
                redacted_base is None
                or "REPLACE_WITH" in redacted_base
                or "TODO" in redacted_base.upper()
            ):
                errors.append(
                    f"Set a real OpenAI-compatible api_base for llm.models[{index}]"
                )
            record["api_base"] = redacted_base

            effective_api_key = (
                explicit_api_key if explicit_api_key is not None else top_api_key
            )
            has_credential_reference = bool(
                isinstance(effective_api_key, str)
                and ENV_REFERENCE_RE.fullmatch(effective_api_key.strip())
            )
            if not has_credential_reference and for_run:
                errors.append(
                    f"llm.models[{index}] requires api_key: \"${{ENV_VAR}}\""
                )

            if redacted_base and "REPLACE_WITH" not in redacted_base and not url_errors:
                hostname = (urlsplit(redacted_base).hostname or "").lower()
                if mode == "docker" and hostname in {"localhost", "127.0.0.1", "::1"}:
                    errors.append(
                        "Docker cannot reach a host model through localhost; use host.docker.internal"
                    )
                if mode == "docker" and hostname == "host.docker.internal":
                    requires_host_gateway = True
        elif backend == "claude-code":
            record["api_base"] = None
            if mode == "docker":
                errors.append(
                    "claude-code backend is host-only in V1; use --mode host or an OpenAI-compatible gateway"
                )
        elif backend == "manual":
            record["api_base"] = None
        model_records.append(record)

    if manual_mode and len(backends - {"manual"}) > 0:
        errors.append("manual_mode must apply consistently to every configured model")

    credential_names = sorted(credentials)
    credential_presence = {name: bool(os.environ.get(name)) for name in credential_names}
    missing = [name for name in credential_names if not credential_presence[name]]

    return {
        "status": (
            "invalid"
            if errors
            else ("credentials_missing" if missing else "ready")
        ),
        "backend_types": sorted(backends - {"invalid"}),
        "models": model_records,
        "manual_mode": manual_mode,
        "credential_environment_names": credential_names,
        "credential_presence": credential_presence,
        "missing_credential_environment_names": missing,
        "requires_host_gateway": requires_host_gateway,
        "errors": errors,
        "warnings": warnings,
        "measurement_source": "config.yaml and process environment",
    }


def required_credential_envs(experiment: Path | None = None) -> list[str]:
    if experiment and (experiment / "config.yaml").is_file():
        runtime = resolve_model_runtime(experiment)
        return list(runtime.get("credential_environment_names", []))
    return []


def required_credential_env(experiment: Path | None = None) -> str | None:
    """Backward-compatible singular view of the first required credential."""
    names = required_credential_envs(experiment)
    return names[0] if names else None


def docker_status() -> dict[str, Any]:
    executable = shutil.which("docker")
    status: dict[str, Any] = {
        "installed": bool(executable),
        "server_available": False,
        "executable": executable,
    }
    if not executable:
        return status
    try:
        result = subprocess.run(
            [executable, "version", "--format", "{{.Server.Version}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
        status["server_available"] = result.returncode == 0
        status["server_version"] = result.stdout.strip() if result.returncode == 0 else None
        if result.returncode != 0:
            status["error"] = result.stderr.strip()[:300]
    except (OSError, subprocess.TimeoutExpired) as exc:
        status["error"] = str(exc)
    return status


def host_resource_snapshot() -> dict[str, Any]:
    memory_bytes: int | None = None
    try:
        if os.name == "nt":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [
                    ("length", ctypes.c_ulong),
                    ("memory_load", ctypes.c_ulong),
                    ("total_physical", ctypes.c_ulonglong),
                    ("available_physical", ctypes.c_ulonglong),
                    ("total_page_file", ctypes.c_ulonglong),
                    ("available_page_file", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("available_virtual", ctypes.c_ulonglong),
                    ("available_extended_virtual", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.length = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                memory_bytes = int(status.total_physical)
        elif hasattr(os, "sysconf"):
            page_size = int(os.sysconf("SC_PAGE_SIZE"))
            page_count = int(os.sysconf("SC_PHYS_PAGES"))
            memory_bytes = page_size * page_count
    except (AttributeError, OSError, TypeError, ValueError):
        memory_bytes = None
    return {
        "logical_cpus": os.cpu_count(),
        "total_memory_bytes": memory_bytes,
        "note": "Docker Desktop may expose less capacity than the host",
    }


def run_help_probe(command: Sequence[str], timeout: float = 15.0) -> dict[str, Any]:
    try:
        result = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "argv": list(command),
            "ok": False,
            "returncode": None,
            "error": str(exc),
        }
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    return {
        "argv": list(command),
        "ok": result.returncode == 0 and "OpenEvolve" in output,
        "returncode": result.returncode,
        "output_preview": output.strip()[:300],
    }


def openevolve_entrypoint_status() -> dict[str, Any]:
    module_importable = False
    module_main_callable = False
    module_error: str | None = None
    try:
        spec = importlib.util.find_spec(HOST_ENTRYPOINT_MODULE)
        module_importable = spec is not None
        if module_importable:
            module = __import__(HOST_ENTRYPOINT_MODULE, fromlist=["main"])
            module_main_callable = callable(getattr(module, "main", None))
    except (ImportError, ModuleNotFoundError, AttributeError, ValueError) as exc:
        module_error = str(exc)

    registered_target: str | None = None
    try:
        all_entries = importlib.metadata.entry_points()
        if hasattr(all_entries, "select"):
            console_entries = all_entries.select(group="console_scripts")
        else:
            console_entries = all_entries.get("console_scripts", [])
        for entry in console_entries:
            if entry.name == CONSOLE_ENTRYPOINT_NAME:
                registered_target = entry.value
                break
    except (ImportError, importlib.metadata.PackageNotFoundError) as exc:
        module_error = module_error or str(exc)

    console_executable = shutil.which(CONSOLE_ENTRYPOINT_NAME)
    if not console_executable:
        scripts_dir = Path(sysconfig.get_path("scripts"))
        for suffix in ("", ".exe", ".cmd"):
            candidate = scripts_dir / f"{CONSOLE_ENTRYPOINT_NAME}{suffix}"
            if candidate.is_file():
                console_executable = str(candidate)
                break
    module_probe = (
        run_help_probe([sys.executable, "-m", HOST_ENTRYPOINT_MODULE, "--help"])
        if module_importable and module_main_callable
        else {"argv": [sys.executable, "-m", HOST_ENTRYPOINT_MODULE, "--help"], "ok": False}
    )
    console_probe = (
        run_help_probe([console_executable, "--help"])
        if console_executable
        else {"argv": [CONSOLE_ENTRYPOINT_NAME, "--help"], "ok": False}
    )
    target_matches = registered_target == CONSOLE_ENTRYPOINT_TARGET
    return {
        "host_module": {
            "name": HOST_ENTRYPOINT_MODULE,
            "importable": module_importable,
            "main_callable": module_main_callable,
            "error": module_error,
            "help_probe": module_probe,
        },
        "console_script": {
            "name": CONSOLE_ENTRYPOINT_NAME,
            "registered_target": registered_target,
            "expected_target": CONSOLE_ENTRYPOINT_TARGET,
            "target_matches": target_matches,
            "executable": console_executable,
            "help_probe": console_probe,
        },
        "ready": bool(
            module_importable
            and module_main_callable
            and module_probe.get("ok")
            and target_matches
            and console_executable
            and console_probe.get("ok")
        ),
    }


def claude_cli_status() -> dict[str, Any]:
    executable = shutil.which("claude")
    status: dict[str, Any] = {
        "installed": bool(executable),
        "executable": executable,
        "authenticated": False,
    }
    if not executable:
        return status
    try:
        result = subprocess.run(
            [executable, "auth", "status"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        status["authenticated"] = result.returncode == 0
        status["returncode"] = result.returncode
        if result.returncode != 0:
            status["error"] = ((result.stderr or result.stdout) or "").strip()[:300]
    except (OSError, subprocess.TimeoutExpired) as exc:
        status["error"] = str(exc)
    return status


def docker_smoke_status(context: Path) -> dict[str, Any]:
    docker = docker_status()
    if not docker.get("server_available"):
        return {"requested": True, "ok": False, "status": "docker_unavailable"}
    digest = hashlib.sha256(str(context.resolve()).encode("utf-8")).hexdigest()[:12]
    tag = f"openevolve-innovator-smoke:{digest}"
    build = run_help_probe(
        ["docker", "build", "--tag", tag, str(context)],
        timeout=300,
    )
    if not build.get("ok"):
        # A Docker build does not print OpenEvolve help, so classify by return code.
        build["ok"] = build.get("returncode") == 0
    if not build.get("ok"):
        return {
            "requested": True,
            "ok": False,
            "status": "build_failed",
            "image": tag,
            "build": build,
        }
    run = run_help_probe(["docker", "run", "--rm", tag, "--help"], timeout=60)
    return {
        "requested": True,
        "ok": bool(run.get("ok")),
        "status": "passed" if run.get("ok") else "entrypoint_failed",
        "image": tag,
        "build": build,
        "run": run,
    }


def doctor_data(
    experiment: Path | None = None,
    *,
    docker_smoke: bool = False,
) -> dict[str, Any]:
    version = installed_openevolve_version()
    docker = docker_status()
    entrypoints = openevolve_entrypoint_status()
    required_envs = required_credential_envs(experiment)
    provider_names = sorted(set(PROVIDER_ENV_NAMES) | set(required_envs))
    provider_env = {name: bool(os.environ.get(name)) for name in provider_names}
    credential_ready = all(provider_env.get(name, False) for name in required_envs)
    model_runtime = (
        resolve_model_runtime(experiment, mode="host")
        if experiment and (experiment / "config.yaml").is_file()
        else None
    )
    docker_model_runtime = (
        resolve_model_runtime(experiment, mode="docker")
        if experiment and (experiment / "config.yaml").is_file()
        else None
    )
    backend_types = (
        set(model_runtime.get("backend_types", []))
        if isinstance(model_runtime, dict)
        else set()
    )
    claude = claude_cli_status() if "claude-code" in backend_types else None
    provider_ready = credential_ready
    if claude is not None:
        provider_ready = provider_ready and bool(claude.get("authenticated"))
    if isinstance(model_runtime, dict) and model_runtime.get("errors"):
        provider_ready = False
    docker_provider_ready = credential_ready
    if isinstance(docker_model_runtime, dict) and docker_model_runtime.get("errors"):
        docker_provider_ready = False
    smoke = docker_smoke_status(
        experiment if experiment and (experiment / "Dockerfile").is_file() else TEMPLATE_DIR
    ) if docker_smoke else {"requested": False}
    data: dict[str, Any] = {
        "command": "doctor",
        "python": {
            "version": platform.python_version(),
            "executable": sys.executable,
            "supported": sys.version_info >= (3, 10),
        },
        "openevolve": {
            "installed": version is not None,
            "version": version,
            "tested_version": PINNED_OPENEVOLVE_VERSION,
            "compatible": version == PINNED_OPENEVOLVE_VERSION,
        },
        "entrypoints": entrypoints,
        "docker": docker,
        "docker_smoke": smoke,
        "host_resources": host_resource_snapshot(),
        "provider_environment": provider_env,
        "required_credential_environment": required_envs[0] if required_envs else None,
        "required_credential_environments": required_envs,
        "model_runtime": model_runtime,
        "claude_cli": claude,
        "ready_for_docker": bool(
            docker["server_available"]
            and docker_provider_ready
            and (not docker_smoke or smoke.get("ok"))
        ),
        "ready_for_host": bool(
            version == PINNED_OPENEVOLVE_VERSION
            and entrypoints.get("ready")
            and provider_ready
        ),
    }
    if experiment:
        data["experiment"] = str(experiment)
        data["experiment_exists"] = experiment.is_dir()
    return data


def command_doctor(args: argparse.Namespace) -> int:
    experiment = resolve_experiment(args.experiment) if args.experiment else None
    data = doctor_data(
        experiment,
        docker_smoke=bool(getattr(args, "docker_smoke", False)),
    )
    emit(data)
    return 0 if data["ready_for_docker"] or data["ready_for_host"] else 2


def render_model_backend(
    backend: str,
    model: str | None,
    api_base: str | None,
    credential_env: str | None,
) -> str:
    if backend not in BACKEND_CHOICES:
        raise SkillError(f"Unsupported backend: {backend}")
    if backend != "openai-compatible" and (api_base or credential_env):
        raise SkillError("--api-base and --credential-env apply only to openai-compatible")

    if backend == "openai-compatible":
        selected_model = model or "REPLACE_WITH_MODEL"
        selected_base = api_base or "REPLACE_WITH_OPENAI_COMPATIBLE_BASE_URL"
        selected_env = credential_env or "MODEL_API_KEY"
        if not ENV_NAME_RE.fullmatch(selected_env):
            raise SkillError("--credential-env must be a valid environment-variable name")
        _, url_errors = redact_api_base(selected_base)
        if url_errors:
            raise SkillError("; ".join(url_errors))
        return f"""# MODEL-BACKEND-START
llm:
  provider: "openai"
  api_base: {json.dumps(selected_base)}
  api_key: "${{{selected_env}}}"
  temperature: 0.7
  max_tokens: 8192
  timeout: 120
  models:
    - name: {json.dumps(selected_model)}
      weight: 1.0
# MODEL-BACKEND-END"""

    if backend == "claude-code":
        selected_model = model or "sonnet"
        return f"""# MODEL-BACKEND-START
llm:
  provider: "claude_code"
  temperature: 0.7
  max_tokens: 8192
  timeout: 300
  models:
    - name: {json.dumps(selected_model)}
      weight: 1.0
      max_budget_usd: 1.0
# MODEL-BACKEND-END"""

    selected_model = model or "manual"
    return f"""# MODEL-BACKEND-START
llm:
  manual_mode: true
  temperature: 0.7
  max_tokens: 8192
  timeout: 120
  models:
    - name: {json.dumps(selected_model)}
      weight: 1.0
# MODEL-BACKEND-END"""


def configure_initialized_backend(
    config_path: Path,
    backend: str,
    model: str | None,
    api_base: str | None,
    credential_env: str | None,
) -> None:
    source = config_path.read_text(encoding="utf-8")
    block = render_model_backend(backend, model, api_base, credential_env)
    pattern = re.compile(
        r"(?ms)^# MODEL-BACKEND-START\s*$.*?^# MODEL-BACKEND-END\s*$"
    )
    if not pattern.search(source):
        raise SkillError("Experiment config template is missing MODEL-BACKEND markers")
    config_path.write_text(pattern.sub(block, source, count=1), encoding="utf-8")


def command_init(args: argparse.Namespace) -> int:
    target = resolve_experiment(args.experiment, require_exists=False)
    backend = getattr(args, "backend", "openai-compatible")
    if target.exists() and any(target.iterdir()):
        raise SkillError(f"Refusing to initialize non-empty directory: {target}")
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(TEMPLATE_DIR, target, dirs_exist_ok=True)
    configure_initialized_backend(
        target / "config.yaml",
        backend,
        getattr(args, "model", None),
        getattr(args, "api_base", None),
        getattr(args, "credential_env", None),
    )

    for path in target.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".md", ".yaml", ".yml", ".py", ".txt", ".csv"}:
            text = path.read_text(encoding="utf-8")
            path.write_text(text.replace("{{EXPERIMENT_NAME}}", args.name), encoding="utf-8")

    (target / "data").mkdir(exist_ok=True)
    (target / "results").mkdir(exist_ok=True)
    for relative in (
        "data/search.jsonl",
        "data/holdout.jsonl",
        "results/baseline_runs.jsonl",
        "results/failure_cases.jsonl",
    ):
        (target / relative).touch(exist_ok=True)

    emit(
        {
            "command": "init",
            "status": "created",
            "experiment": str(target),
            "backend": backend,
            "next": [
                "Complete problem.yaml and both datasets.",
                "Review config.yaml model settings and references/limitations.md in the skill.",
                "Replace the baseline and evaluator TODOs.",
                "Document prior art and record at least three baseline runs.",
                f'"{sys.executable}" "{Path(__file__).resolve()}" validate "{target}" --for-run',
            ],
        }
    )
    return 0


def scan_secrets(paths: Iterable[Path]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            if "${" in line or "REPLACE_WITH" in line or "TODO" in line:
                continue
            if any(pattern.search(line) for pattern in SECRET_VALUE_PATTERNS):
                findings.append(f"{path.name}:{line_number}: possible embedded secret")
    return findings


def scan_risky_python(path: Path) -> list[str]:
    findings: set[str] = set()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in RISKY_IMPORTS:
                    findings.add(f"imports {root}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".", 1)[0]
            if root in RISKY_IMPORTS:
                findings.add(f"imports {root}")
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in RISKY_CALLS:
                findings.add(f"calls {node.func.id}()")
            elif isinstance(node.func, ast.Attribute) and node.func.attr in {
                "Popen",
                "run",
                "system",
                "urlopen",
            }:
                findings.add(f"calls .{node.func.attr}()")
    return sorted(findings)


def validate_requirements(path: Path) -> list[str]:
    errors: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        if value.startswith(("-", "git+", "http://", "https://")) or "@" in value:
            errors.append(
                f"requirements-evaluator.txt:{line_number}: URLs, options, and direct references are not allowed"
            )
        elif "==" not in value:
            errors.append(
                f"requirements-evaluator.txt:{line_number}: dependency must use an exact == version"
            )
    return errors


def contains_todo(value: Any) -> bool:
    if isinstance(value, str):
        return "TODO" in value.upper() or "REPLACE_WITH" in value.upper()
    if isinstance(value, list):
        return any(contains_todo(item) for item in value)
    if isinstance(value, dict):
        return any(contains_todo(item) for item in value.values())
    return False


def nested_mapping(value: Any, key: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    nested = value.get(key)
    return nested if isinstance(nested, dict) else {}


def config_scalar_values(config_text: str, key: str) -> list[str]:
    pattern = re.compile(rf"(?m)^\s*{re.escape(key)}\s*:\s*([^#\r\n]+)")
    return [match.group(1).strip().strip("\"'") for match in pattern.finditer(config_text)]


def config_int(config_text: str, key: str, default: int) -> int:
    values = config_scalar_values(config_text, key)
    parsed: list[int] = []
    for value in values:
        try:
            parsed.append(int(value))
        except ValueError:
            continue
    return max(parsed) if parsed else default


def config_bool(config_text: str, key: str, default: bool) -> bool:
    values = config_scalar_values(config_text, key)
    if not values:
        return default
    return values[-1].lower() == "true"


def explicit_max_code_length(config_text: str) -> int | None:
    match = re.search(r"(?m)^max_code_length\s*:\s*(\d+)\s*(?:#.*)?$", config_text)
    return int(match.group(1)) if match else None


def resolve_context_budget(root: Path, problem: dict[str, Any]) -> dict[str, Any]:
    initial_length = len((root / "initial_program.py").read_text(encoding="utf-8"))
    config_text = (root / "config.yaml").read_text(encoding="utf-8")
    context = {**DEFAULT_MODEL_CONTEXT, **nested_mapping(problem, "model_context")}
    code_budget = {**DEFAULT_CODE_BUDGET, **nested_mapping(problem, "code_budget")}

    minimum_context = context.get("minimum_context_window_tokens")
    max_output_tokens = config_int(config_text, "max_tokens", 4096)
    top_programs = config_int(config_text, "num_top_programs", 3)
    diverse_programs = config_int(config_text, "num_diverse_programs", 2)
    changes_descriptions = config_bool(
        config_text, "programs_as_changes_description", False
    )
    diff_based = config_bool(config_text, "diff_based_evolution", True)
    explicit_limit = explicit_max_code_length(config_text)
    errors: list[str] = []
    warnings: list[str] = []

    numeric_fields = {
        "utilization": context.get("utilization"),
        "chars_per_token": context.get("chars_per_token"),
        "fixed_prompt_reserve_tokens": context.get("fixed_prompt_reserve_tokens"),
        "artifact_reserve_tokens": context.get("artifact_reserve_tokens"),
        "growth_ratio": code_budget.get("growth_ratio"),
        "growth_allowance_chars": code_budget.get("growth_allowance_chars"),
        "hard_max_chars": code_budget.get("hard_max_chars"),
    }
    for name, value in numeric_fields.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            errors.append(f"{name} must be a positive number")

    if not isinstance(minimum_context, int) or isinstance(minimum_context, bool):
        warnings.append(
            "Declare model_context.minimum_context_window_tokens before running"
        )
        return {
            "status": "unresolved",
            "initial_program_chars": initial_length,
            "minimum_context_window_tokens": minimum_context,
            "max_output_tokens": max_output_tokens,
            "resolved_code_limit": None,
            "explicit_config_limit": explicit_limit,
            "errors": errors,
            "warnings": warnings,
        }
    if minimum_context <= 0:
        errors.append("minimum_context_window_tokens must be a positive integer")
    if errors:
        return {
            "status": "invalid",
            "initial_program_chars": initial_length,
            "minimum_context_window_tokens": minimum_context,
            "max_output_tokens": max_output_tokens,
            "resolved_code_limit": None,
            "explicit_config_limit": explicit_limit,
            "errors": errors,
            "warnings": warnings,
        }

    utilization = float(context["utilization"])
    chars_per_token = float(context["chars_per_token"])
    fixed_reserve = int(context["fixed_prompt_reserve_tokens"])
    artifact_reserve = int(context["artifact_reserve_tokens"])
    if not 0.1 <= utilization <= 0.95:
        errors.append("model_context.utilization must be between 0.1 and 0.95")

    usable_input_tokens = (
        math.floor(minimum_context * utilization)
        - max_output_tokens
        - fixed_reserve
        - artifact_reserve
    )
    history_slots = 1 if changes_descriptions else 1 + top_programs + diverse_programs
    context_code_cap = (
        math.floor(usable_input_tokens * chars_per_token / max(history_slots, 1))
        if usable_input_tokens > 0
        else 0
    )
    growth_ratio = float(code_budget["growth_ratio"])
    growth_allowance = int(code_budget["growth_allowance_chars"])
    hard_max = int(code_budget["hard_max_chars"])
    desired = max(
        initial_length + growth_allowance,
        math.ceil(initial_length * growth_ratio),
    )
    generation_chars = math.floor(max_output_tokens * chars_per_token * 0.75)
    generation_cap = initial_length + generation_chars if diff_based else generation_chars
    caps = [desired, context_code_cap, generation_cap, hard_max]
    if explicit_limit is not None:
        caps.append(explicit_limit)
    resolved = min(caps)

    if usable_input_tokens <= 0:
        errors.append("Model context leaves no safe input budget after configured reserves")
    if resolved < initial_length:
        errors.append(
            "Initial program cannot fit the safe context/code budget; reduce prompt history or use a larger context window"
        )
    if resolved < desired and resolved >= initial_length:
        warnings.append(
            f"Code growth is constrained to {resolved} characters by context, generation, or hard limits"
        )

    return {
        "status": "valid" if not errors else "invalid",
        "initial_program_chars": initial_length,
        "minimum_context_window_tokens": minimum_context,
        "context_utilization": utilization,
        "chars_per_token": chars_per_token,
        "max_output_tokens": max_output_tokens,
        "fixed_prompt_reserve_tokens": fixed_reserve,
        "artifact_reserve_tokens": artifact_reserve,
        "program_slots": history_slots,
        "programs_as_changes_description": changes_descriptions,
        "diff_based_evolution": diff_based,
        "desired_code_limit": desired,
        "context_code_cap": context_code_cap,
        "generation_code_cap": generation_cap,
        "hard_max_chars": hard_max,
        "explicit_config_limit": explicit_limit,
        "resolved_code_limit": resolved if resolved >= initial_length else None,
        "errors": errors,
        "warnings": warnings,
    }


def validate_memory_value(value: Any, label: str) -> str:
    rendered = str(value)
    if not MEMORY_VALUE_RE.fullmatch(rendered):
        raise SkillError(f"{label} must be a positive Docker size such as 4g or 512m")
    return rendered.lower()


def resolve_runtime_resources(
    problem: dict[str, Any],
    profile_override: str | None = None,
    memory_override: str | None = None,
    cpus_override: float | None = None,
) -> dict[str, Any]:
    runtime = nested_mapping(problem, "runtime")
    docker = nested_mapping(runtime, "docker")
    profile = profile_override or str(docker.get("profile", "standard"))
    if profile not in RESOURCE_PROFILES:
        raise SkillError(
            f"Unknown Docker resource profile '{profile}'; use {', '.join(RESOURCE_PROFILES)}"
        )
    resolved = dict(RESOURCE_PROFILES[profile])
    resolved["profile"] = profile
    if profile_override is None:
        for key in ("memory", "cpus", "pids_limit", "tmpfs_size"):
            value = docker.get(key)
            if value is not None:
                resolved[key] = value
    if memory_override is not None:
        resolved["memory"] = memory_override
    if cpus_override is not None:
        resolved["cpus"] = cpus_override

    resolved["memory"] = validate_memory_value(resolved["memory"], "Docker memory")
    resolved["tmpfs_size"] = validate_memory_value(
        resolved["tmpfs_size"], "Docker tmpfs_size"
    )
    cpus = resolved["cpus"]
    if (
        not isinstance(cpus, (int, float))
        or isinstance(cpus, bool)
        or not 0.1 <= float(cpus) <= 1024
    ):
        raise SkillError("Docker cpus must be a number from 0.1 to 1024")
    resolved["cpus"] = float(cpus)
    pids = resolved["pids_limit"]
    if not isinstance(pids, int) or isinstance(pids, bool) or not 16 <= pids <= 65535:
        raise SkillError("Docker pids_limit must be an integer from 16 to 65535")
    return resolved


def budget_limits(problem: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    budget = nested_mapping(problem, "budget")
    errors: list[str] = []
    max_iterations = budget.get("max_iterations")
    max_wall_minutes = budget.get("max_wall_time_minutes")
    max_cost = budget.get("max_estimated_cost_usd")
    cost_per_iteration = budget.get("estimated_cost_per_iteration_usd")
    if not isinstance(max_iterations, int) or isinstance(max_iterations, bool) or max_iterations <= 0:
        errors.append("budget.max_iterations must be a positive integer")
    if (
        not isinstance(max_wall_minutes, (int, float))
        or isinstance(max_wall_minutes, bool)
        or max_wall_minutes <= 0
    ):
        errors.append("budget.max_wall_time_minutes must be positive")
    if (
        max_cost is not None
        and (
            not isinstance(max_cost, (int, float))
            or isinstance(max_cost, bool)
            or max_cost < 0
        )
    ):
        errors.append("budget.max_estimated_cost_usd must be zero or positive")
    if (
        cost_per_iteration is not None
        and (
            not isinstance(cost_per_iteration, (int, float))
            or isinstance(cost_per_iteration, bool)
            or cost_per_iteration < 0
        )
    ):
        errors.append("budget.estimated_cost_per_iteration_usd must be zero or positive")
    return (
        {
            "max_iterations": max_iterations,
            "max_wall_time_seconds": (
                float(max_wall_minutes) * 60
                if isinstance(max_wall_minutes, (int, float))
                and not isinstance(max_wall_minutes, bool)
                else None
            ),
            "max_estimated_cost_usd": float(max_cost) if isinstance(max_cost, (int, float)) else None,
            "estimated_cost_per_iteration_usd": (
                float(cost_per_iteration)
                if isinstance(cost_per_iteration, (int, float))
                and not isinstance(cost_per_iteration, bool)
                else None
            ),
            "measurement_source": "configured_per_iteration_estimate",
            "enforcement_mode": "estimate_only",
        },
        errors,
    )


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, path)


def read_budget_ledger(root: Path) -> dict[str, Any] | None:
    path = root / "results" / "budget_ledger.json"
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SkillError(f"Invalid budget ledger: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise SkillError("Unsupported or invalid budget ledger")
    return value


def budget_status(root: Path, limits: dict[str, Any]) -> dict[str, Any]:
    ledger = read_budget_ledger(root)
    consumed_iterations = int((ledger or {}).get("consumed_iterations", 0))
    consumed_wall = float((ledger or {}).get("consumed_wall_time_seconds", 0.0))
    consumed_cost = float((ledger or {}).get("estimated_cost_usd", 0.0))
    reservations = (ledger or {}).get("reservations", {})
    reserved_iterations = sum(
        int(value.get("iterations", 0))
        for value in reservations.values()
        if isinstance(value, dict)
    ) if isinstance(reservations, dict) else 0
    reserved_cost = sum(
        float(value.get("estimated_cost_usd", 0.0))
        for value in reservations.values()
        if isinstance(value, dict)
    ) if isinstance(reservations, dict) else 0.0
    max_iterations = limits.get("max_iterations")
    max_wall = limits.get("max_wall_time_seconds")
    max_cost = limits.get("max_estimated_cost_usd")
    return {
        **limits,
        "consumed_iterations": consumed_iterations,
        "consumed_wall_time_seconds": consumed_wall,
        "estimated_cost_usd": consumed_cost,
        "reserved_iterations": reserved_iterations,
        "reserved_estimated_cost_usd": reserved_cost,
        "remaining_iterations": (
            int(max_iterations) - consumed_iterations - reserved_iterations
            if isinstance(max_iterations, int)
            else None
        ),
        "remaining_wall_time_seconds": (
            float(max_wall) - consumed_wall if isinstance(max_wall, (int, float)) else None
        ),
        "remaining_estimated_cost_usd": (
            float(max_cost) - consumed_cost - reserved_cost
            if isinstance(max_cost, (int, float))
            else None
        ),
        "ledger_present": ledger is not None,
    }


def validate_budget_request(
    status: dict[str, Any], limits: dict[str, Any], iterations: int
) -> float:
    if iterations <= 0:
        raise SkillError("Requested iterations must be positive")
    if limits.get("estimated_cost_per_iteration_usd") is None and limits.get(
        "max_estimated_cost_usd"
    ) is not None:
        raise SkillError(
            "Set budget.estimated_cost_per_iteration_usd before enforcing a dollar estimate"
        )
    remaining_iterations = status.get("remaining_iterations")
    if not isinstance(remaining_iterations, int) or iterations > remaining_iterations:
        raise SkillError(
            f"Requested {iterations} iterations exceeds remaining budget {remaining_iterations}"
        )
    remaining_wall = status.get("remaining_wall_time_seconds")
    if not isinstance(remaining_wall, (int, float)) or remaining_wall <= 0:
        raise SkillError("No wall-time budget remains")
    rate = float(limits.get("estimated_cost_per_iteration_usd") or 0.0)
    estimated_cost = iterations * rate
    remaining_cost = status.get("remaining_estimated_cost_usd")
    if (
        isinstance(remaining_cost, (int, float))
        and estimated_cost > float(remaining_cost) + 1e-12
    ):
        raise SkillError(
            f"Estimated run cost {estimated_cost:.6f} USD exceeds remaining estimate budget {remaining_cost:.6f} USD"
        )
    return estimated_cost


def current_source_hashes(root: Path) -> dict[str, str]:
    return {
        name: sha256_file(root / name)
        for name in ("initial_program.py", "evaluator.py", "config.yaml")
    }


def reserve_budget(
    root: Path, limits: dict[str, Any], iterations: int
) -> tuple[str, float, dict[str, Any]]:
    ledger = read_budget_ledger(root)
    hashes = current_source_hashes(root)
    if ledger is None:
        ledger = {
            "schema_version": 1,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "source_hashes": hashes,
            "consumed_iterations": 0,
            "consumed_wall_time_seconds": 0.0,
            "estimated_cost_usd": 0.0,
            "reservations": {},
            "last_stop_reason": None,
        }
    elif ledger.get("source_hashes") != hashes:
        raise SkillError(
            "Initial program, evaluator, or config changed after budget tracking began; create a new experiment"
        )
    reservations = ledger.get("reservations")
    if not isinstance(reservations, dict):
        raise SkillError("Budget ledger reservations are invalid")
    if reservations:
        raise SkillError(
            "An unfinished budget reservation exists; inspect the previous run before resuming"
        )

    status = budget_status(root, limits)
    estimated_cost = validate_budget_request(status, limits, iterations)
    remaining_wall = float(status["remaining_wall_time_seconds"])

    reservation_id = uuid.uuid4().hex
    reservations[reservation_id] = {
        "created_at": utc_now(),
        "iterations": iterations,
        "estimated_cost_usd": estimated_cost,
    }
    ledger["limits"] = limits
    ledger["updated_at"] = utc_now()
    atomic_write_json(root / "results" / "budget_ledger.json", ledger)
    return reservation_id, float(remaining_wall), budget_status(root, limits)


def finalize_budget(
    root: Path,
    limits: dict[str, Any],
    reservation_id: str,
    elapsed_seconds: float,
    observed_iterations: int | None,
    stop_reason: str,
    mode: str,
    returncode: int,
) -> dict[str, Any]:
    ledger = read_budget_ledger(root)
    if ledger is None:
        raise SkillError("Budget ledger disappeared during execution")
    reservations = ledger.get("reservations")
    if not isinstance(reservations, dict) or reservation_id not in reservations:
        raise SkillError("Budget reservation disappeared during execution")
    reservation = reservations.pop(reservation_id)
    reserved_iterations = int(reservation["iterations"])
    charged_iterations = (
        max(0, min(int(observed_iterations), reserved_iterations))
        if observed_iterations is not None
        else reserved_iterations
    )
    rate = float(limits.get("estimated_cost_per_iteration_usd") or 0.0)
    ledger["consumed_iterations"] = int(ledger.get("consumed_iterations", 0)) + charged_iterations
    ledger["consumed_wall_time_seconds"] = float(
        ledger.get("consumed_wall_time_seconds", 0.0)
    ) + max(elapsed_seconds, 0.0)
    ledger["estimated_cost_usd"] = float(
        ledger.get("estimated_cost_usd", 0.0)
    ) + charged_iterations * rate
    ledger["limits"] = limits
    ledger["updated_at"] = utc_now()
    ledger["last_stop_reason"] = stop_reason
    atomic_write_json(root / "results" / "budget_ledger.json", ledger)

    segment = {
        "schema_version": 1,
        "reservation_id": reservation_id,
        "finished_at": utc_now(),
        "mode": mode,
        "reserved_iterations": reserved_iterations,
        "observed_iterations": observed_iterations,
        "charged_iterations": charged_iterations,
        "elapsed_seconds": elapsed_seconds,
        "estimated_cost_usd": charged_iterations * rate,
        "measurement_source": limits["measurement_source"],
        "stop_reason": stop_reason,
        "returncode": returncode,
    }
    with (root / "results" / "run_segments.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(segment, ensure_ascii=False) + "\n")
    return budget_status(root, limits)


def read_baseline_runs(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    runs: list[dict[str, Any]] = []
    errors: list[str] = []
    if not path.exists():
        return runs, [f"Missing baseline file: {path.relative_to(path.parents[1])}"]
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"baseline_runs.jsonl:{line_number}: invalid JSON ({exc.msg})")
            continue
        if not isinstance(value, dict):
            errors.append(f"baseline_runs.jsonl:{line_number}: expected an object")
            continue
        if not isinstance(value.get("combined_score"), (int, float)):
            errors.append(f"baseline_runs.jsonl:{line_number}: combined_score must be numeric")
        runs.append(value)
    return runs, errors


def validate_experiment(root: Path, for_run: bool = False, host_mode: bool = False) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    validation_level = "run" if for_run else "basic"
    if not for_run:
        warnings.append(
            "Basic validation checks structure and configuration only; use --for-run for data, evidence, baseline, and execution gates"
        )

    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            errors.append(f"Missing required file: {relative}")
    if errors:
        return {
            "valid": False,
            "validation_level": validation_level,
            "errors": errors,
            "warnings": warnings,
        }

    try:
        problem = load_yaml(root / "problem.yaml")
    except SkillError as exc:
        return {
            "valid": False,
            "validation_level": validation_level,
            "errors": [str(exc)],
            "warnings": warnings,
        }
    for key in ("name", "objective", "primary_metric", "search_dataset", "holdout_dataset", "iterations", "seeds"):
        if key not in problem:
            errors.append(f"problem.yaml is missing '{key}'")
    for key in ("name", "objective", "primary_metric"):
        if contains_todo(problem.get(key)):
            errors.append(f"problem.yaml '{key}' still contains a placeholder")

    search_value = str(problem.get("search_dataset", ""))
    holdout_value = str(problem.get("holdout_dataset", ""))
    if search_value == holdout_value and search_value:
        errors.append("Search and holdout datasets must be different files")
    for label, value in (("search", search_value), ("holdout", holdout_value)):
        candidate = (root / value).resolve()
        if value and not path_within(candidate, root):
            errors.append(f"{label} dataset must stay inside the experiment directory")
        if for_run and (not candidate.is_file() or candidate.stat().st_size == 0):
            errors.append(f"{label} dataset is missing or empty: {value}")

    seeds = problem.get("seeds", [])
    if not isinstance(seeds, list) or len(set(seeds)) < 3:
        errors.append("Declare at least three unique seeds in problem.yaml")
    iterations = problem.get("iterations")
    if not isinstance(iterations, int) or not 1 <= iterations <= 10000:
        errors.append("problem.yaml iterations must be an integer from 1 to 10000")
    if "hard_constraints" not in problem:
        errors.append("problem.yaml is missing 'hard_constraints'")
    elif contains_todo(problem.get("hard_constraints")):
        errors.append("Define hard_constraints before running")
    if "strong_baselines" not in problem:
        errors.append("problem.yaml is missing 'strong_baselines'")
    elif contains_todo(problem.get("strong_baselines")):
        errors.append("Name at least one strong baseline before running")

    initial_path = root / "initial_program.py"
    initial_text = initial_path.read_text(encoding="utf-8")
    if initial_text.count("# EVOLVE-BLOCK-START") != 1 or initial_text.count("# EVOLVE-BLOCK-END") != 1:
        errors.append("initial_program.py must contain exactly one EVOLVE-BLOCK pair")
    try:
        ast.parse(initial_text, filename=str(initial_path))
    except SyntaxError as exc:
        errors.append(f"initial_program.py has invalid Python syntax: {exc}")

    evaluator_path = root / "evaluator.py"
    evaluator_text = evaluator_path.read_text(encoding="utf-8")
    try:
        evaluator_tree = ast.parse(evaluator_text, filename=str(evaluator_path))
        function_names = {
            node.name for node in evaluator_tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        if "evaluate" not in function_names:
            errors.append("evaluator.py must define evaluate(program_path)")
    except SyntaxError as exc:
        errors.append(f"evaluator.py has invalid Python syntax: {exc}")
    if "combined_score" not in evaluator_text:
        errors.append("evaluator.py must return a combined_score metric")
    if for_run and "TODO" in evaluator_text:
        errors.append("evaluator.py still contains TODO placeholders")

    config_text = (root / "config.yaml").read_text(encoding="utf-8")
    for fragment, message in (
        ("random_seed:", "config.yaml must set random_seed"),
        ("early_stopping_metric:", "config.yaml must set early_stopping_metric"),
        ("evolution_trace:", "config.yaml must enable evolution trace"),
        ("include_code: true", "config.yaml must include code in the trace"),
    ):
        if fragment not in config_text:
            errors.append(message)
    if "early_stopping_metric: \"combined_score\"" not in config_text and "early_stopping_metric: combined_score" not in config_text:
        errors.append("early_stopping_metric must be combined_score")
    if for_run and "REPLACE_WITH_MODEL" in config_text:
        errors.append("Replace REPLACE_WITH_MODEL in config.yaml")
    model_runtime = resolve_model_runtime(
        root,
        mode="host" if host_mode else "docker",
        for_run=for_run,
    )
    errors.extend(model_runtime["errors"])
    warnings.extend(model_runtime["warnings"])

    context_budget = resolve_context_budget(root, problem)
    warnings.extend(context_budget["warnings"])
    if context_budget["errors"]:
        errors.extend(context_budget["errors"])
    if for_run and context_budget["status"] == "unresolved":
        errors.append(
            "model_context.minimum_context_window_tokens is required before running"
        )

    try:
        runtime_resources = resolve_runtime_resources(problem)
    except SkillError as exc:
        runtime_resources = None
        errors.append(str(exc))

    limits, budget_errors = budget_limits(problem)
    errors.extend(budget_errors)
    configured_max_iterations = limits.get("max_iterations")
    if (
        isinstance(iterations, int)
        and isinstance(configured_max_iterations, int)
        and iterations > configured_max_iterations
    ):
        errors.append("problem.yaml iterations exceeds budget.max_iterations")
    if (
        limits.get("max_estimated_cost_usd") is not None
        and limits.get("estimated_cost_per_iteration_usd") is None
    ):
        message = (
            "Set budget.estimated_cost_per_iteration_usd; use 0 for a reviewed local-model setup"
        )
        if for_run:
            errors.append(message)
        else:
            warnings.append(message)
    try:
        resolved_budget_status = budget_status(root, limits)
    except SkillError as exc:
        resolved_budget_status = {"status": "invalid"}
        errors.append(str(exc))

    contract = nested_mapping(problem, "program_contract")
    enforce_contract = contract.get("enforce_public_signatures", True)
    if not isinstance(enforce_contract, bool):
        errors.append("program_contract.enforce_public_signatures must be true or false")
    elif enforce_contract:
        if "validate_public_contract" not in evaluator_text:
            errors.append(
                "evaluator.py must call validate_public_contract when public signature enforcement is enabled"
            )
    else:
        warnings.append(
            "Public signature enforcement is disabled; generated candidates may change interfaces"
        )

    secret_paths = [
        root / "problem.yaml",
        initial_path,
        evaluator_path,
        root / "config.yaml",
        root / "research" / "prior_art.md",
        root / "research" / "novelty_audit.md",
        root / "research" / "limitations.md",
    ]
    errors.extend(scan_secrets(secret_paths))
    errors.extend(validate_requirements(root / "requirements-evaluator.txt"))

    if for_run:
        prior_text = (root / "research" / "prior_art.md").read_text(encoding="utf-8")
        if "TODO" in prior_text or len(re.findall(r"https?://", prior_text)) < 3:
            errors.append("prior_art.md needs a dated search and at least three linked sources")
        limitations_text = (root / "research" / "limitations.md").read_text(
            encoding="utf-8"
        )
        if "TODO" in limitations_text:
            warnings.append(
                "limitations.md still has task-specific TODOs; final claims will remain provisional"
            )

        baseline_runs, baseline_errors = read_baseline_runs(root / "results" / "baseline_runs.jsonl")
        errors.extend(baseline_errors)
        if len(baseline_runs) < 3:
            errors.append("Record at least three baseline runs before evolution")
        else:
            expected_hash = sha256_file(initial_path)
            seen_seeds = {run.get("seed") for run in baseline_runs}
            if len(seen_seeds) < 3:
                errors.append("Baseline runs must cover at least three unique seeds")
            mismatched = [
                run for run in baseline_runs if run.get("program_sha256") != expected_hash
            ]
            if mismatched:
                errors.append("Every baseline program_sha256 must match initial_program.py")

    risk_findings = {
        "initial_program.py": scan_risky_python(initial_path),
        "evaluator.py": scan_risky_python(evaluator_path),
    }
    if any(risk_findings.values()):
        warnings.append("Python risk scan found operations requiring review")
    if host_mode:
        warnings.append(
            "Host mode cannot sandbox future generated candidates or enforce Docker CPU/memory limits; explicit confirmation is mandatory"
        )

    return {
        "valid": not errors,
        "validation_level": validation_level,
        "errors": errors,
        "warnings": warnings,
        "risk_findings": risk_findings,
        "context_budget": context_budget,
        "resolved_code_limit": context_budget.get("resolved_code_limit"),
        "runtime_resources": runtime_resources,
        "model_runtime": model_runtime,
        "budget_status": resolved_budget_status,
        "problem": {
            "name": problem.get("name"),
            "primary_metric": problem.get("primary_metric"),
            "iterations": problem.get("iterations"),
            "seeds": problem.get("seeds"),
        },
    }


def command_validate(args: argparse.Namespace) -> int:
    root = resolve_experiment(args.experiment)
    result = validate_experiment(root, for_run=args.for_run, host_mode=args.mode == "host")
    emit({"command": "validate", "experiment": str(root), **result})
    return 0 if result["valid"] else 2


def image_tag(root: Path) -> str:
    digest = hashlib.sha256()
    for relative in ("Dockerfile", "requirements-evaluator.txt"):
        digest.update((root / relative).read_bytes())
    return f"openevolve-skill:{PINNED_OPENEVOLVE_VERSION}-{digest.hexdigest()[:12]}"


def effective_config_text(root: Path, resolved_code_limit: int) -> str:
    source = (root / "config.yaml").read_text(encoding="utf-8")
    source = re.sub(
        r"(?m)^max_code_length\s*:\s*[^\r\n]*(?:\r?\n)?",
        "",
        source,
    ).rstrip()
    return (
        source
        + "\n\n# Resolved by openevolve-innovator after context preflight.\n"
        + f"max_code_length: {resolved_code_limit}\n"
    )


def write_effective_config(root: Path, resolved_code_limit: int) -> Path:
    path = root / "results" / "effective_config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        effective_config_text(root, resolved_code_limit),
        encoding="utf-8",
    )
    return path


def common_evolution_args(
    root: Path,
    docker: bool,
    iterations: int,
    target_score: float | None,
    checkpoint: Path | None,
    effective_config: Path | None = None,
) -> list[str]:
    effective_config = effective_config or (root / "results" / "effective_config.yaml")
    if docker:
        base = "/experiment"
        relative_config = effective_config.resolve().relative_to(root.resolve()).as_posix()
        values = [
            f"{base}/initial_program.py",
            f"{base}/evaluator.py",
            "--config",
            f"{base}/{relative_config}",
            "--output",
            f"{base}/openevolve_output",
            "--iterations",
            str(iterations),
        ]
        if checkpoint:
            relative = checkpoint.resolve().relative_to(root.resolve()).as_posix()
            values.extend(["--checkpoint", f"{base}/{relative}"])
    else:
        values = [
            str(root / "initial_program.py"),
            str(root / "evaluator.py"),
            "--config",
            str(effective_config),
            "--output",
            str(root / "openevolve_output"),
            "--iterations",
            str(iterations),
        ]
        if checkpoint:
            values.extend(["--checkpoint", str(checkpoint)])
    if target_score is not None:
        values.extend(["--target-score", str(target_score)])
    return values


def docker_commands(
    root: Path,
    iterations: int,
    target_score: float | None,
    checkpoint: Path | None,
    resources: dict[str, Any] | None = None,
    container_name: str | None = None,
    effective_config: Path | None = None,
    enforce_contract: bool = True,
) -> tuple[list[str], list[str], str]:
    if resources is None:
        resources = resolve_runtime_resources(load_yaml(root / "problem.yaml"))
    tag = image_tag(root)
    build = [
        "docker",
        "build",
        "--build-arg",
        f"OPENEVOLVE_VERSION={PINNED_OPENEVOLVE_VERSION}",
        "--tag",
        tag,
        ".",
    ]
    run = [
        "docker",
        "run",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--memory",
        str(resources["memory"]),
        "--cpus",
        str(resources["cpus"]),
        "--pids-limit",
        str(resources["pids_limit"]),
        "--tmpfs",
        f"/tmp:rw,noexec,nosuid,size={resources['tmpfs_size']}",
        "--mount",
        f"type=bind,source={root},target=/experiment",
        "--workdir",
        "/experiment",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--env",
        f"OPENEVO_GUARD_ENFORCE_SIGNATURES={'1' if enforce_contract else '0'}",
    ]
    if container_name:
        run[2:2] = ["--name", container_name]
    model_runtime = resolve_model_runtime(root, mode="docker")
    if model_runtime.get("requires_host_gateway"):
        run.extend(["--add-host", "host.docker.internal:host-gateway"])
    for credential_name in required_credential_envs(root):
        if os.environ.get(credential_name):
            run.extend(["--env", credential_name])
    run.append(tag)
    run.extend(
        common_evolution_args(
            root,
            True,
            iterations,
            target_score,
            checkpoint,
            effective_config,
        )
    )
    return build, run, tag


def host_command(
    root: Path,
    iterations: int,
    target_score: float | None,
    checkpoint: Path | None,
    effective_config: Path | None = None,
) -> list[str]:
    return [
        sys.executable,
        "-m",
        HOST_ENTRYPOINT_MODULE,
        *common_evolution_args(
            root,
            False,
            iterations,
            target_score,
            checkpoint,
            effective_config,
        ),
    ]


def command_for_display(command: Sequence[str]) -> list[str]:
    """Return argv as structured data; secrets are never argv values."""
    return list(command)


def trace_iterations(root: Path) -> set[int]:
    path = root / "openevolve_output" / "evolution_trace.jsonl"
    values: set[int] = set()
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        iteration = entry.get("iteration") if isinstance(entry, dict) else None
        if isinstance(iteration, int) and not isinstance(iteration, bool):
            values.add(iteration)
    return values


def terminate_process_group(process: subprocess.Popen[Any]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
        return
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    try:
        process.terminate()
        process.wait(timeout=5)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        if os.name == "nt":
            process.kill()
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def run_timed_process(
    command: Sequence[str],
    cwd: Path,
    timeout_seconds: float,
    env: dict[str, str] | None = None,
    stop_callback: Any = None,
) -> dict[str, Any]:
    popen_kwargs: dict[str, Any] = {"cwd": cwd, "env": env}
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    started = time.monotonic()
    process = subprocess.Popen(list(command), **popen_kwargs)
    timed_out = False
    interrupted = False
    try:
        returncode = process.wait(timeout=max(timeout_seconds, 0.1))
    except subprocess.TimeoutExpired:
        timed_out = True
        if stop_callback is not None:
            stop_callback()
        terminate_process_group(process)
        returncode = process.returncode if process.returncode is not None else 124
    except KeyboardInterrupt:
        interrupted = True
        if stop_callback is not None:
            stop_callback()
        terminate_process_group(process)
        returncode = process.returncode if process.returncode is not None else 130
    return {
        "returncode": int(returncode),
        "elapsed_seconds": time.monotonic() - started,
        "timed_out": timed_out,
        "interrupted": interrupted,
    }


def docker_state(container_name: str, root: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{json .State}}", container_name],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    try:
        value = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def classify_stop_reason(
    returncode: int,
    *,
    timed_out: bool = False,
    interrupted: bool = False,
    docker_inspect_state: dict[str, Any] | None = None,
) -> str:
    if timed_out:
        return "wall_time_exceeded"
    if interrupted:
        return "user_interrupted"
    if docker_inspect_state and docker_inspect_state.get("OOMKilled") is True:
        return "oom_killed"
    if returncode == 0:
        return "completed"
    if returncode in {137, -9}:
        return "resource_exhausted_or_sigkill"
    return "execution_failed"


def write_run_manifest(
    root: Path,
    mode: str,
    command: Sequence[str],
    iterations: int,
    checkpoint: Path | None,
    status: str,
    started_at: str,
    *,
    context_budget: dict[str, Any],
    runtime_resources: dict[str, Any] | None,
    budget: dict[str, Any],
    stop_reason: str | None = None,
    elapsed_seconds: float | None = None,
    effective_config: Path | None = None,
    container_name: str | None = None,
    returncode: int | None = None,
) -> None:
    manifest = {
        "schema_version": 2,
        "mode": mode,
        "command_argv": command_for_display(command),
        "iterations": iterations,
        "checkpoint": str(checkpoint) if checkpoint else None,
        "started_at": started_at,
        "finished_at": utc_now() if returncode is not None else None,
        "status": status,
        "stop_reason": stop_reason,
        "returncode": returncode,
        "elapsed_seconds": elapsed_seconds,
        "python_version": platform.python_version(),
        "openevolve_version": PINNED_OPENEVOLVE_VERSION,
        "context_budget": context_budget,
        "runtime_resources": runtime_resources,
        "model_runtime": resolve_model_runtime(root, mode=mode),
        "budget": budget,
        "effective_config": (
            effective_config.relative_to(root).as_posix()
            if effective_config and path_within(effective_config, root)
            else None
        ),
        "effective_config_sha256": (
            sha256_file(effective_config)
            if effective_config and effective_config.is_file()
            else None
        ),
        "container_name": container_name,
        "provider_environment_names": [
            name for name in required_credential_envs(root) if os.environ.get(name)
        ],
    }
    results_dir = root / "results"
    results_dir.mkdir(exist_ok=True)
    (results_dir / "run_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def resolve_checkpoint(root: Path, raw_checkpoint: str | None) -> Path | None:
    if not raw_checkpoint:
        return None
    checkpoint = Path(raw_checkpoint).expanduser()
    if not checkpoint.is_absolute():
        checkpoint = root / checkpoint
    checkpoint = checkpoint.resolve()
    if not path_within(checkpoint, root):
        raise SkillError("Checkpoint must be inside the experiment directory")
    if not checkpoint.is_dir():
        raise SkillError(f"Checkpoint directory not found: {checkpoint}")
    return checkpoint


def run_evolution(args: argparse.Namespace, resume: bool) -> int:
    root = resolve_experiment(args.experiment)
    checkpoint = resolve_checkpoint(root, args.checkpoint if resume else None)
    if resume and checkpoint is None:
        raise SkillError("resume requires --checkpoint")

    validation = validate_experiment(root, for_run=True, host_mode=args.mode == "host")
    if not validation["valid"]:
        emit({"command": "resume" if resume else "run", "status": "blocked", **validation})
        return 2

    problem = load_yaml(root / "problem.yaml")
    iterations = (
        int(problem["iterations"])
        if args.iterations is None
        else int(args.iterations)
    )
    target_score = args.target_score
    started_at = utc_now()
    context_budget = validation["context_budget"]
    resolved_code_limit = context_budget.get("resolved_code_limit")
    if not isinstance(resolved_code_limit, int):
        raise SkillError("Context preflight did not resolve a safe max_code_length")
    limits, budget_errors = budget_limits(problem)
    if budget_errors:
        raise SkillError("; ".join(budget_errors))
    current_budget = budget_status(root, limits)
    validate_budget_request(current_budget, limits, iterations)
    resources = resolve_runtime_resources(
        problem,
        getattr(args, "docker_profile", None),
        getattr(args, "docker_memory", None),
        getattr(args, "docker_cpus", None),
    )
    contract = nested_mapping(problem, "program_contract")
    enforce_contract = bool(contract.get("enforce_public_signatures", True))
    effective_config = root / "results" / "effective_config.yaml"
    required_envs = required_credential_envs(root)
    missing_envs = [name for name in required_envs if not os.environ.get(name)]
    model_runtime = validation["model_runtime"]
    if missing_envs and not args.dry_run:
        raise SkillError(
            "Configuration requires environment variables "
            + ", ".join(missing_envs)
            + "; their values must not be written to files"
        )
    if "claude-code" in model_runtime.get("backend_types", []) and not args.dry_run:
        claude = claude_cli_status()
        if not claude.get("installed"):
            raise SkillError("claude-code backend requires the claude CLI on the host")
        if not claude.get("authenticated"):
            raise SkillError("claude-code backend requires an authenticated claude CLI session")

    if args.mode == "host":
        if not args.acknowledge_host_risk:
            raise SkillError(
                "Host mode requires --acknowledge-host-risk after explicit user confirmation"
            )
        version = installed_openevolve_version()
        if version != PINNED_OPENEVOLVE_VERSION:
            raise SkillError(
                f"Host mode requires openevolve=={PINNED_OPENEVOLVE_VERSION}; found {version or 'not installed'}"
            )
        if not args.dry_run:
            entrypoints = openevolve_entrypoint_status()
            if not entrypoints["host_module"]["help_probe"].get("ok"):
                raise SkillError(
                    f"Host entrypoint {HOST_ENTRYPOINT_MODULE} failed its --help probe"
                )
        command = host_command(
            root, iterations, target_score, checkpoint, effective_config
        )
        if args.dry_run:
            emit(
                {
                    "command": "resume" if resume else "run",
                    "mode": "host",
                    "dry_run": True,
                    "argv": command_for_display(command),
                    "warnings": validation["warnings"],
                    "context_budget": context_budget,
                    "budget_status": current_budget,
                    "runtime_resources": None,
                    "model_runtime": model_runtime,
                }
            )
            return 0

    container_name = (
        f"openevolve-guard-{hashlib.sha256(str(root).encode()).hexdigest()[:8]}-{uuid.uuid4().hex[:8]}"
        if args.mode == "docker"
        else None
    )
    build: list[str] | None = None
    tag: str | None = None
    if args.mode == "docker":
        build, command, tag = docker_commands(
            root,
            iterations,
            target_score,
            checkpoint,
            resources,
            container_name,
            effective_config,
            enforce_contract,
        )
        if args.dry_run:
            emit(
                {
                    "command": "resume" if resume else "run",
                    "mode": "docker",
                    "dry_run": True,
                    "image": tag,
                    "build_argv": command_for_display(build),
                    "run_argv": command_for_display(command),
                    "context_budget": context_budget,
                    "budget_status": current_budget,
                    "runtime_resources": resources,
                    "model_runtime": model_runtime,
                }
            )
            return 0
        docker = docker_status()
        if not docker["server_available"]:
            raise SkillError("Docker server is unavailable; do not fall back to host mode silently")

    write_effective_config(root, resolved_code_limit)
    reservation_id, remaining_wall, reserved_budget = reserve_budget(
        root, limits, iterations
    )
    traces_before = trace_iterations(root)
    segment_started = time.monotonic()
    result: dict[str, Any] = {
        "returncode": 1,
        "elapsed_seconds": 0.0,
        "timed_out": False,
        "interrupted": False,
    }
    state: dict[str, Any] = {}
    evolution_started = False
    stop_reason = "execution_failed"
    finalized_budget = reserved_budget

    write_run_manifest(
        root,
        args.mode,
        command,
        iterations,
        checkpoint,
        "running",
        started_at,
        context_budget=context_budget,
        runtime_resources=resources if args.mode == "docker" else None,
        budget=reserved_budget,
        effective_config=effective_config,
        container_name=container_name,
    )

    try:
        if args.mode == "host":
            host_environment = os.environ.copy()
            for name in PROVIDER_ENV_NAMES:
                if name not in required_envs:
                    host_environment.pop(name, None)
            host_environment["OPENEVO_GUARD_ENFORCE_SIGNATURES"] = (
                "1" if enforce_contract else "0"
            )
            evolution_started = True
            result = run_timed_process(
                command,
                root,
                remaining_wall,
                env=host_environment,
            )
        else:
            assert build is not None and tag is not None and container_name is not None
            inspect_result = subprocess.run(
                ["docker", "image", "inspect", tag],
                cwd=root,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            docker_ready = inspect_result.returncode == 0
            if inspect_result.returncode != 0:
                build_result = run_timed_process(build, root, remaining_wall)
                if build_result["returncode"] != 0 or build_result["timed_out"]:
                    result = build_result
                    stop_reason = classify_stop_reason(
                        build_result["returncode"],
                        timed_out=build_result["timed_out"],
                        interrupted=build_result["interrupted"],
                    )
                else:
                    docker_ready = True
                    remaining_wall -= float(build_result["elapsed_seconds"])
            if docker_ready:
                def stop_container() -> None:
                    try:
                        stopped = subprocess.run(
                            ["docker", "stop", "--time", "10", container_name],
                            cwd=root,
                            check=False,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            timeout=15,
                        )
                    except (OSError, subprocess.TimeoutExpired):
                        stopped = None
                    if stopped is None or stopped.returncode != 0:
                        try:
                            subprocess.run(
                                ["docker", "kill", container_name],
                                cwd=root,
                                check=False,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                timeout=10,
                            )
                        except (OSError, subprocess.TimeoutExpired):
                            pass

                evolution_started = True
                result = run_timed_process(
                    command,
                    root,
                    max(remaining_wall, 0.1),
                    stop_callback=stop_container,
                )
                state = docker_state(container_name, root)
                stop_reason = classify_stop_reason(
                    result["returncode"],
                    timed_out=result["timed_out"],
                    interrupted=result["interrupted"],
                    docker_inspect_state=state,
                )
            elif stop_reason == "execution_failed":
                stop_reason = classify_stop_reason(
                    result["returncode"],
                    timed_out=result["timed_out"],
                    interrupted=result["interrupted"],
                )
    finally:
        if args.mode == "docker" and container_name:
            try:
                subprocess.run(
                    ["docker", "rm", "--force", container_name],
                    cwd=root,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        if args.mode == "host":
            stop_reason = classify_stop_reason(
                result["returncode"],
                timed_out=result["timed_out"],
                interrupted=result["interrupted"],
            )
        elapsed = time.monotonic() - segment_started
        traces_after = trace_iterations(root)
        observed_count = len(traces_after - traces_before)
        observed_iterations: int | None = observed_count
        if evolution_started and observed_count == 0 and iterations > 0:
            observed_iterations = None
        finalized_budget = finalize_budget(
            root,
            limits,
            reservation_id,
            elapsed,
            observed_iterations,
            stop_reason,
            args.mode,
            int(result["returncode"]),
        )

    public_returncode = int(result["returncode"])
    if result["timed_out"]:
        public_returncode = 124
    elif result["interrupted"]:
        public_returncode = 130
    elif stop_reason == "oom_killed":
        public_returncode = 137
    status_name = "completed" if stop_reason == "completed" else "failed"
    if stop_reason in {"wall_time_exceeded", "user_interrupted"}:
        status_name = "interrupted"
    write_run_manifest(
        root,
        args.mode,
        command,
        iterations,
        checkpoint,
        status_name,
        started_at,
        context_budget=context_budget,
        runtime_resources=resources if args.mode == "docker" else None,
        budget=finalized_budget,
        stop_reason=stop_reason,
        elapsed_seconds=time.monotonic() - segment_started,
        effective_config=effective_config,
        container_name=container_name,
        returncode=public_returncode,
    )
    return public_returncode


def locate_best_program(root: Path) -> tuple[Path, Path]:
    best_dir = root / "openevolve_output" / "best"
    info_path = best_dir / "best_program_info.json"
    programs = sorted(best_dir.glob("best_program.*")) if best_dir.is_dir() else []
    if info_path.is_file() and programs:
        return programs[0], info_path

    checkpoint_root = root / "openevolve_output" / "checkpoints"
    checkpoints: list[tuple[int, Path]] = []
    if checkpoint_root.is_dir():
        for child in checkpoint_root.iterdir():
            match = re.fullmatch(r"checkpoint_(\d+)", child.name)
            if child.is_dir() and match:
                checkpoints.append((int(match.group(1)), child))
    for _, checkpoint in sorted(checkpoints, reverse=True):
        info = checkpoint / "best_program_info.json"
        candidates = sorted(checkpoint.glob("best_program.*"))
        if info.is_file() and candidates:
            return candidates[0], info
    raise SkillError("No OpenEvolve best program was found")


def csv_data_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def configured_method_names(problem: dict[str, Any], key: str) -> list[str]:
    value = problem.get(key, [])
    if not isinstance(value, list):
        return []
    return [
        item.strip()
        for item in value
        if isinstance(item, str) and item.strip() and "TODO" not in item
    ]


def assess_task_validation(
    problem: dict[str, Any],
    comparison_rows: list[dict[str, str]],
    ablation_rows: list[dict[str, str]],
    *,
    search_improvement: bool,
    limitations_complete: bool,
) -> dict[str, Any]:
    minimum_seeds = 3
    reasons: list[str] = []
    grouped: dict[str, dict[str, float]] = {}
    display_names: dict[str, str] = {}
    for row in comparison_rows:
        method = str(row.get("method", "")).strip()
        split = str(row.get("split", "")).strip().casefold()
        seed = str(row.get("seed", "")).strip()
        score = finite_float(row.get("combined_score"))
        if not method or split != "holdout" or not seed or score is None:
            continue
        key = method.casefold()
        display_names.setdefault(key, method)
        grouped.setdefault(key, {})[seed] = score

    candidate_scores = grouped.get("candidate", {})
    if len(candidate_scores) < minimum_seeds:
        reasons.append(
            "baseline_comparison.csv needs method=candidate on holdout for at least three seeds"
        )

    def compare_category(config_key: str, label: str) -> dict[str, Any]:
        names = configured_method_names(problem, config_key)
        comparisons: list[dict[str, Any]] = []
        for name in names:
            baseline_scores = grouped.get(name.casefold(), {})
            common_seeds = sorted(set(candidate_scores) & set(baseline_scores))
            if len(common_seeds) < minimum_seeds:
                continue
            candidate_mean = statistics.fmean(
                candidate_scores[seed] for seed in common_seeds
            )
            baseline_mean = statistics.fmean(
                baseline_scores[seed] for seed in common_seeds
            )
            comparisons.append(
                {
                    "method": display_names.get(name.casefold(), name),
                    "common_seeds": common_seeds,
                    "candidate_mean_combined_score": candidate_mean,
                    "baseline_mean_combined_score": baseline_mean,
                    "candidate_wins": candidate_mean > baseline_mean,
                }
            )
        if not comparisons:
            reasons.append(
                f"No configured {label} has at least three common holdout seeds with method=candidate"
            )
            return {"status": "incomplete", "comparisons": []}
        strongest = max(
            comparisons,
            key=lambda item: item["baseline_mean_combined_score"],
        )
        if not strongest["candidate_wins"]:
            reasons.append(
                f"Candidate does not beat the strongest measured {label} on common holdout seeds"
            )
        return {
            "status": "passed" if strongest["candidate_wins"] else "failed",
            "strongest_measured": strongest,
            "comparisons": comparisons,
        }

    weak_comparison = compare_category("weak_baselines", "weak baseline")
    strong_comparison = compare_category("strong_baselines", "strong baseline")

    ablation_seeds: dict[str, set[str]] = {}
    for row in ablation_rows:
        component = str(row.get("removed_component", "")).strip()
        split = str(row.get("split", "")).strip().casefold()
        seed = str(row.get("seed", "")).strip()
        score = finite_float(row.get("combined_score"))
        delta = finite_float(row.get("delta_vs_full"))
        if (
            component
            and split == "holdout"
            and seed
            and score is not None
            and delta is not None
        ):
            ablation_seeds.setdefault(component, set()).add(seed)
    replicated_ablations = {
        component: sorted(seeds)
        for component, seeds in ablation_seeds.items()
        if len(seeds) >= minimum_seeds
    }
    if not replicated_ablations:
        reasons.append(
            "ablation_results.csv needs one component ablation on holdout for at least three seeds with numeric deltas"
        )
    if not search_improvement:
        reasons.append(
            "Best search score does not exceed the unchanged baseline mean across at least three runs"
        )
    if not limitations_complete:
        reasons.append("Task-specific limitations audit is incomplete")

    return {
        "status": "complete" if not reasons else "incomplete",
        "minimum_common_seeds": minimum_seeds,
        "search_improvement_observed": search_improvement,
        "candidate_holdout_seeds": sorted(candidate_scores),
        "weak_baseline_comparison": weak_comparison,
        "strong_baseline_comparison": strong_comparison,
        "replicated_holdout_ablations": replicated_ablations,
        "reasons": reasons,
        "measurement_source": (
            "results/baseline_runs.jsonl, results/baseline_comparison.csv, "
            "and results/ablation_results.csv"
        ),
    }


def count_jsonl(path: Path) -> int:
    if not path.is_file():
        return 0
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            json.loads(line)
            count += 1
        except json.JSONDecodeError:
            continue
    return count


def build_report(summary: dict[str, Any], language: str) -> str:
    best = summary["best_candidate"]
    baseline = summary["baseline"]
    novelty = summary["novelty"]
    limitations = summary["limitations"]
    limitations_audit = summary["limitations_audit"]
    budget = summary["budget"]
    runtime = summary["runtime"]
    model_runtime = summary["model_runtime"]
    task_validation = summary["task_validation"]
    validation_reasons = task_validation.get("reasons", [])
    if language.lower().startswith("zh"):
        return f"""# OpenEvolve Innovator 研究报告

## 结论

当前结论：**{novelty['claim_status']}**。最佳候选的 `combined_score` 为 {best['metrics'].get('combined_score', '未测量')}；基线均值为 {baseline.get('mean_combined_score', '未测量')}。

## 实验

- 实验：{summary['experiment']['name']}
- 目标：{summary['experiment']['objective']}
- OpenEvolve：{summary['reproducibility']['openevolve_version']}
- 模型后端：{', '.join(model_runtime.get('backend_types', [])) or '未记录'}
- 基线运行数：{baseline['run_count']}
- 消融记录数：{summary['ablation_count']}
- 失败案例数：{summary['failure_case_count']}
- 停止原因：{runtime.get('stop_reason', '未测量')}

## 最佳候选

- 文件：`{best['file']}`
- SHA-256：`{best['sha256']}`
- 指标：`{json.dumps(best['metrics'], ensure_ascii=False)}`

## 预算

- 累计迭代：{budget.get('consumed_iterations', 0)}
- 累计墙钟时间（秒）：{budget.get('consumed_wall_time_seconds', 0)}
- 估算成本（USD）：{budget.get('estimated_cost_usd', 0)}
- 计量来源：{budget.get('measurement_source', '未测量')}

## 新颖性

- 任务验证状态：{task_validation['status']}
- 审计状态：{novelty['audit_status']}
- 可支持的表述：{novelty['claim_status']}
{chr(10).join(f'- 未满足：{item}' for item in validation_reasons)}

## 局限性与有效性威胁

- 局限性审计：{limitations_audit['audit_status']}
{chr(10).join(f'- {item}' for item in limitations)}

### 实验特定说明

{limitations_audit['task_specific_text']}
"""
    return f"""# OpenEvolve Innovator Research Report

## Conclusion

Current claim: **{novelty['claim_status']}**. The best candidate has `combined_score` {best['metrics'].get('combined_score', 'unmeasured')}; the baseline mean is {baseline.get('mean_combined_score', 'unmeasured')}.

## Experiment

- Name: {summary['experiment']['name']}
- Objective: {summary['experiment']['objective']}
- OpenEvolve: {summary['reproducibility']['openevolve_version']}
- Model backends: {', '.join(model_runtime.get('backend_types', [])) or 'unrecorded'}
- Baseline runs: {baseline['run_count']}
- Ablation records: {summary['ablation_count']}
- Failure cases: {summary['failure_case_count']}
- Stop reason: {runtime.get('stop_reason', 'unmeasured')}

## Best candidate

- File: `{best['file']}`
- SHA-256: `{best['sha256']}`
- Metrics: `{json.dumps(best['metrics'], ensure_ascii=False)}`

## Budget

- Consumed iterations: {budget.get('consumed_iterations', 0)}
- Wall time seconds: {budget.get('consumed_wall_time_seconds', 0)}
- Estimated cost USD: {budget.get('estimated_cost_usd', 0)}
- Measurement source: {budget.get('measurement_source', 'unmeasured')}

## Novelty

- Task-validation status: {task_validation['status']}
- Audit status: {novelty['audit_status']}
- Supported wording: {novelty['claim_status']}
{chr(10).join(f'- Unmet gate: {item}' for item in validation_reasons)}

## Limitations and validity threats

- Audit status: {limitations_audit['audit_status']}
{chr(10).join(f'- {item}' for item in limitations)}

### Experiment-specific notes

{limitations_audit['task_specific_text']}
"""


def command_summarize(args: argparse.Namespace) -> int:
    root = resolve_experiment(args.experiment)
    program_path, info_path = locate_best_program(root)
    info = json.loads(info_path.read_text(encoding="utf-8"))
    problem = load_yaml(root / "problem.yaml")
    baseline_runs, baseline_errors = read_baseline_runs(root / "results" / "baseline_runs.jsonl")
    scores = [
        float(run["combined_score"])
        for run in baseline_runs
        if isinstance(run.get("combined_score"), (int, float))
    ]

    results_dir = root / "results"
    results_dir.mkdir(exist_ok=True)
    delivered_program = results_dir / f"best_program{program_path.suffix}"
    shutil.copy2(program_path, delivered_program)

    prior_text = (root / "research" / "prior_art.md").read_text(encoding="utf-8")
    novelty_text = (root / "research" / "novelty_audit.md").read_text(encoding="utf-8")
    limitations_text = (root / "research" / "limitations.md").read_text(
        encoding="utf-8"
    )
    novelty_complete = (
        re.search(r"(?im)^STATUS:\s*complete\s*$", novelty_text) is not None
        and "TODO" not in novelty_text
        and "TODO" not in prior_text
        and len(re.findall(r"https?://", prior_text)) >= 3
    )
    limitations_complete = (
        re.search(r"(?im)^STATUS:\s*complete\s*$", limitations_text) is not None
        and "TODO" not in limitations_text
    )

    run_manifest_path = results_dir / "run_manifest.json"
    run_manifest = (
        json.loads(run_manifest_path.read_text(encoding="utf-8"))
        if run_manifest_path.is_file()
        else None
    )
    comparisons = csv_data_rows(results_dir / "baseline_comparison.csv")
    ablations = csv_data_rows(results_dir / "ablation_results.csv")
    failures = count_jsonl(results_dir / "failure_cases.jsonl")
    metrics = info.get("metrics", {}) if isinstance(info, dict) else {}
    best_search_score = finite_float(metrics.get("combined_score"))
    baseline_mean = statistics.fmean(scores) if len(scores) >= 3 else None
    search_improvement = bool(
        best_search_score is not None
        and baseline_mean is not None
        and best_search_score > baseline_mean
    )
    task_validation = assess_task_validation(
        problem,
        comparisons,
        ablations,
        search_improvement=search_improvement,
        limitations_complete=limitations_complete,
    )
    if not limitations_complete:
        claim_status = "candidate result; limitations audit incomplete"
    elif not search_improvement:
        claim_status = "candidate result"
    elif task_validation["status"] != "complete":
        claim_status = "candidate improvement"
    elif novelty_complete:
        claim_status = "research novelty audited"
    else:
        claim_status = "validated task-specific improvement"

    limits, budget_errors = budget_limits(problem)
    try:
        resolved_budget = budget_status(root, limits)
    except SkillError as exc:
        resolved_budget = {**limits, "status": "invalid", "error": str(exc)}
    context_budget = resolve_context_budget(root, problem)
    runtime_summary = {
        "mode": run_manifest.get("mode") if isinstance(run_manifest, dict) else None,
        "resources": (
            run_manifest.get("runtime_resources")
            if isinstance(run_manifest, dict)
            else None
        ),
        "elapsed_seconds": (
            run_manifest.get("elapsed_seconds")
            if isinstance(run_manifest, dict)
            else None
        ),
        "stop_reason": (
            run_manifest.get("stop_reason")
            if isinstance(run_manifest, dict)
            else None
        ),
    }
    model_runtime = (
        run_manifest.get("model_runtime")
        if isinstance(run_manifest, dict)
        and isinstance(run_manifest.get("model_runtime"), dict)
        else resolve_model_runtime(root)
    )

    limitations: list[str] = []
    if not novelty_complete:
        limitations.append("Novelty audit is incomplete; do not claim a new algorithm.")
    if len(scores) < 3:
        limitations.append("Fewer than three valid baseline runs were found.")
    if not ablations:
        limitations.append("No component ablations were recorded.")
    if task_validation["status"] != "complete":
        limitations.append(
            "Task-specific validation is incomplete: "
            + "; ".join(task_validation["reasons"])
        )
    if run_manifest is None:
        limitations.append("No run manifest was found; runtime and execution mode are unmeasured.")
    if budget_errors:
        limitations.append("Budget configuration is invalid: " + "; ".join(budget_errors))
    if not resolved_budget.get("ledger_present"):
        limitations.append("No budget ledger was found; cumulative spend is unmeasured.")
    else:
        limitations.append(
            "USD cost is a configured per-iteration estimate, not a provider billing measurement."
        )
    if not limitations_complete:
        limitations.append(
            "Task-specific limitations audit is incomplete; do not present the result as a validated innovation."
        )
    documented_limitations = [
        line[2:].strip()
        for line in limitations_text.splitlines()
        if line.strip().startswith("- ")
        and "TODO" not in line
        and line[2:].strip()
    ]
    for item in documented_limitations:
        if item not in limitations:
            limitations.append(item)

    summary: dict[str, Any] = {
        "schema_version": 2,
        "generated_at": utc_now(),
        "experiment": {
            "name": problem.get("name"),
            "objective": problem.get("objective"),
            "primary_metric": problem.get("primary_metric"),
            "search_dataset": problem.get("search_dataset"),
            "holdout_dataset": problem.get("holdout_dataset"),
        },
        "best_candidate": {
            "file": delivered_program.relative_to(root).as_posix(),
            "sha256": sha256_file(delivered_program),
            "metrics": metrics,
            "source_info": info,
        },
        "baseline": {
            "run_count": len(scores),
            "mean_combined_score": statistics.fmean(scores) if scores else None,
            "stdev_combined_score": statistics.stdev(scores) if len(scores) > 1 else None,
            "errors": baseline_errors,
        },
        "task_validation": task_validation,
        "ablation_count": len(ablations),
        "failure_case_count": failures,
        "context_budget": context_budget,
        "runtime": runtime_summary,
        "model_runtime": model_runtime,
        "budget": resolved_budget,
        "novelty": {
            "audit_status": "complete" if novelty_complete else "incomplete",
            "claim_status": claim_status,
        },
        "reproducibility": {
            "openevolve_version": PINNED_OPENEVOLVE_VERSION,
            "python_version": platform.python_version(),
            "initial_program_sha256": sha256_file(root / "initial_program.py"),
            "evaluator_sha256": sha256_file(root / "evaluator.py"),
            "config_sha256": sha256_file(root / "config.yaml"),
            "effective_config_sha256": (
                sha256_file(root / "results" / "effective_config.yaml")
                if (root / "results" / "effective_config.yaml").is_file()
                else None
            ),
            "run_manifest": run_manifest,
        },
        "limitations": limitations,
        "limitations_audit": {
            "audit_status": "complete" if limitations_complete else "incomplete",
            "source": "research/limitations.md",
            "task_specific_text": limitations_text,
        },
    }
    (results_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report = build_report(summary, str(problem.get("report_language", "en")))
    (root / "research_report.md").write_text(report, encoding="utf-8")
    emit(
        {
            "command": "summarize",
            "status": "created",
            "summary": str(results_dir / "summary.json"),
            "report": str(root / "research_report.md"),
            "claim_status": claim_status,
            "limitations_audit_status": (
                "complete" if limitations_complete else "incomplete"
            ),
            "task_validation_status": task_validation["status"],
            "limitations": limitations,
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Research-grade OpenEvolve candidate algorithm innovator"
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {PINNED_OPENEVOLVE_VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check runtime and provider readiness")
    doctor.add_argument("experiment", nargs="?")
    doctor.add_argument(
        "--docker-smoke",
        action="store_true",
        help="Build the pinned image and run its console entrypoint --help",
    )
    doctor.set_defaults(func=command_doctor)

    init = subparsers.add_parser("init", help="Initialize an experiment directory")
    init.add_argument("experiment")
    init.add_argument("--name", required=True)
    init.add_argument(
        "--backend",
        choices=BACKEND_CHOICES,
        default="openai-compatible",
        help="Model transport profile",
    )
    init.add_argument("--model", help="Initial model name for the selected backend")
    init.add_argument(
        "--api-base",
        help="OpenAI-compatible API base URL",
    )
    init.add_argument(
        "--credential-env",
        help="Environment-variable name containing the OpenAI-compatible credential",
    )
    init.set_defaults(func=command_init)

    validate = subparsers.add_parser(
        "validate",
        help="Run basic validation; add --for-run for all execution and evidence gates",
    )
    validate.add_argument("experiment")
    validate.add_argument(
        "--for-run",
        action="store_true",
        help="Enforce data, evaluator, evidence, baseline, budget, and execution gates",
    )
    validate.add_argument("--mode", choices=("docker", "host"), default="docker")
    validate.set_defaults(func=command_validate)

    def add_run_arguments(subparser: argparse.ArgumentParser, resume: bool) -> None:
        subparser.add_argument("experiment")
        subparser.add_argument("--mode", choices=("docker", "host"), default="docker")
        subparser.add_argument("--iterations", type=int)
        subparser.add_argument("--target-score", type=float)
        subparser.add_argument(
            "--docker-profile",
            choices=tuple(RESOURCE_PROFILES),
            help="Override the experiment Docker resource profile",
        )
        subparser.add_argument(
            "--docker-memory",
            help="Override Docker memory with a validated size such as 8g",
        )
        subparser.add_argument(
            "--docker-cpus",
            type=float,
            help="Override Docker CPU quota",
        )
        subparser.add_argument("--acknowledge-host-risk", action="store_true")
        subparser.add_argument("--dry-run", action="store_true")
        if resume:
            subparser.add_argument("--checkpoint", required=True)

    run = subparsers.add_parser("run", help="Run a validated evolution experiment")
    add_run_arguments(run, resume=False)
    run.set_defaults(func=lambda args: run_evolution(args, resume=False))

    resume = subparsers.add_parser("resume", help="Resume from a checkpoint")
    add_run_arguments(resume, resume=True)
    resume.set_defaults(func=lambda args: run_evolution(args, resume=True))

    summarize = subparsers.add_parser("summarize", help="Create standardized result artifacts")
    summarize.add_argument("experiment")
    summarize.set_defaults(func=command_summarize)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except SkillError as exc:
        emit({"status": "error", "error": str(exc)})
        return 2
    except KeyboardInterrupt:
        emit({"status": "interrupted"})
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
