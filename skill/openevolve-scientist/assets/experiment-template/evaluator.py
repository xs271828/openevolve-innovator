"""Search-time evaluator template.

Replace evaluate_candidate with task-specific logic. Never read holdout data here.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import time
from pathlib import Path
from typing import Any


EXPERIMENT_DIR = Path(__file__).resolve().parent
SEARCH_DATA = EXPERIMENT_DIR / "data" / "search.jsonl"
INITIAL_PROGRAM = EXPERIMENT_DIR / "initial_program.py"


def argument_contract(arguments: ast.arguments) -> dict[str, Any]:
    return {
        "posonly": [item.arg for item in arguments.posonlyargs],
        "positional": [item.arg for item in arguments.args],
        "vararg": arguments.vararg.arg if arguments.vararg else None,
        "kwonly": [item.arg for item in arguments.kwonlyargs],
        "kwarg": arguments.kwarg.arg if arguments.kwarg else None,
        "defaults": [ast.dump(item, include_attributes=False) for item in arguments.defaults],
        "kw_defaults": [
            ast.dump(item, include_attributes=False) if item is not None else None
            for item in arguments.kw_defaults
        ],
    }


def public_contract(program_path: str | Path) -> dict[str, Any]:
    tree = ast.parse(Path(program_path).read_text(encoding="utf-8"))
    contract: dict[str, Any] = {"functions": {}, "classes": {}}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            contract["functions"][node.name] = argument_contract(node.args)
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            methods: dict[str, Any] = {}
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                    not child.name.startswith("_") or child.name == "__init__"
                ):
                    methods[child.name] = argument_contract(child.args)
            contract["classes"][node.name] = methods
    return contract


def validate_public_contract(program_path: str) -> list[str]:
    expected = public_contract(INITIAL_PROGRAM)
    candidate = public_contract(program_path)
    violations: list[str] = []
    for name, signature in expected["functions"].items():
        if name not in candidate["functions"]:
            violations.append(f"missing public function {name}")
        elif candidate["functions"][name] != signature:
            violations.append(f"changed signature for function {name}")
    for class_name, methods in expected["classes"].items():
        candidate_methods = candidate["classes"].get(class_name)
        if candidate_methods is None:
            violations.append(f"missing public class {class_name}")
            continue
        for method_name, signature in methods.items():
            if method_name not in candidate_methods:
                violations.append(f"missing method {class_name}.{method_name}")
            elif candidate_methods[method_name] != signature:
                violations.append(f"changed signature for {class_name}.{method_name}")
    return violations


def load_program(program_path: str):
    module_name = "candidate_" + hashlib.sha256(program_path.encode("utf-8")).hexdigest()[:12]
    spec = importlib.util.spec_from_file_location(module_name, program_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load candidate program")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_search_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with SEARCH_DATA.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                cases.append(json.loads(line))
    return cases


def evaluate_candidate(module: Any, cases: list[dict[str, Any]], seed: int) -> dict[str, float]:
    """Return task-specific raw metrics with higher values meaning better results."""
    if not hasattr(module, "solve"):
        return {"correctness": 0.0, "quality": 0.0, "runtime_seconds": 0.0}

    started = time.perf_counter()
    # TODO: execute module.solve on cases and calculate real metrics.
    _ = (cases, seed)
    elapsed = time.perf_counter() - started
    return {"correctness": 0.0, "quality": 0.0, "runtime_seconds": elapsed}


def evaluate(program_path: str) -> dict[str, Any]:
    try:
        enforce_contract = os.environ.get(
            "OPENEVO_GUARD_ENFORCE_SIGNATURES", "1"
        ) != "0"
        if enforce_contract:
            violations = validate_public_contract(program_path)
            if violations:
                return {
                    "combined_score": 0.0,
                    "correctness": 0.0,
                    "quality": 0.0,
                    "runtime_seconds": 0.0,
                    "contract_valid": 0.0,
                    "error": "; ".join(violations),
                }
        module = load_program(program_path)
        cases = load_search_cases()
        seed = int(os.environ.get("EXPERIMENT_SEED", "11"))
        metrics = evaluate_candidate(module, cases, seed)
        correctness = float(metrics.get("correctness", 0.0))
        quality = float(metrics.get("quality", 0.0))
        runtime = max(float(metrics.get("runtime_seconds", 0.0)), 0.0)

        # Correctness is a hard gate. Replace weights only after documenting them.
        combined_score = 0.0 if correctness < 1.0 else quality - min(runtime, 60.0) * 0.001
        return {
            "combined_score": float(combined_score),
            "correctness": correctness,
            "quality": quality,
            "runtime_seconds": runtime,
            "case_count": float(len(cases)),
            "contract_valid": 1.0,
        }
    except Exception as exc:
        return {
            "combined_score": 0.0,
            "correctness": 0.0,
            "quality": 0.0,
            "runtime_seconds": 0.0,
            "error": f"{type(exc).__name__}: {exc}",
        }
