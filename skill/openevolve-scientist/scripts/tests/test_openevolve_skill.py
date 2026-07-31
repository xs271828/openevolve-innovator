from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "openevolve_skill.py"
SPEC = importlib.util.spec_from_file_location("openevolve_skill", SCRIPT_PATH)
assert SPEC and SPEC.loader
skill = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(skill)


class OpenEvolveSkillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "experiment"
        with contextlib.redirect_stdout(io.StringIO()):
            result = skill.command_init(
                Namespace(
                    experiment=str(self.root),
                    name="fixture",
                    backend="openai-compatible",
                    model=None,
                    api_base=None,
                    credential_env=None,
                )
            )
        self.assertEqual(result, 0)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def make_valid_for_run(self) -> None:
        problem = (self.root / "problem.yaml").read_text(encoding="utf-8")
        problem = problem.replace(
            'objective: "TODO: state one executable optimization objective"',
            'objective: "Maximize deterministic fixture quality"',
        )
        problem = problem.replace(
            '  - "TODO: define correctness and safety constraints"',
            '  - "All outputs must equal the fixture oracle"',
        )
        problem = problem.replace(
            '  - "TODO: name at least one credible external baseline"',
            '  - "reference_dynamic_programming"',
        )
        problem = problem.replace(
            "  minimum_context_window_tokens: null",
            "  minimum_context_window_tokens: 128000",
        )
        problem = problem.replace(
            "  estimated_cost_per_iteration_usd: null",
            "  estimated_cost_per_iteration_usd: 0.05",
        )
        (self.root / "problem.yaml").write_text(problem, encoding="utf-8")

        evaluator = (self.root / "evaluator.py").read_text(encoding="utf-8")
        evaluator = evaluator.replace(
            "    # TODO: execute module.solve on cases and calculate real metrics.",
            "    # Fixture evaluator executes deterministic cases.",
        )
        (self.root / "evaluator.py").write_text(evaluator, encoding="utf-8")

        config = (self.root / "config.yaml").read_text(encoding="utf-8")
        (self.root / "config.yaml").write_text(
            config.replace("REPLACE_WITH_MODEL", "fixture-model").replace(
                "REPLACE_WITH_OPENAI_COMPATIBLE_BASE_URL",
                "https://example.invalid/v1",
            ),
            encoding="utf-8",
        )
        (self.root / "data" / "search.jsonl").write_text('{"input": 1, "expected": 1}\n', encoding="utf-8")
        (self.root / "data" / "holdout.jsonl").write_text('{"input": 2, "expected": 2}\n', encoding="utf-8")
        (self.root / "research" / "prior_art.md").write_text(
            """# Prior art

Search date: 2026-07-28

- Scholar: https://example.org/paper
- arXiv: https://arxiv.org/abs/1234.5678
- Source: https://github.com/example/baseline
""",
            encoding="utf-8",
        )
        (self.root / "research" / "limitations.md").write_text(
            """# Limitations and validity threats

STATUS: complete

- The fixture does not establish external validity.
- Provider-side nondeterminism remains possible.
""",
            encoding="utf-8",
        )
        program_hash = skill.sha256_file(self.root / "initial_program.py")
        runs = [
            {
                "seed": seed,
                "combined_score": score,
                "program_sha256": program_hash,
                "dataset": "search",
            }
            for seed, score in ((11, 0.2), (23, 0.3), (47, 0.4))
        ]
        (self.root / "results" / "baseline_runs.jsonl").write_text(
            "".join(json.dumps(run) + "\n" for run in runs), encoding="utf-8"
        )

    def test_init_creates_standard_layout(self) -> None:
        for relative in skill.REQUIRED_FILES:
            self.assertTrue((self.root / relative).is_file(), relative)
        self.assertTrue((self.root / "data" / "search.jsonl").is_file())
        self.assertTrue((self.root / "results" / "baseline_runs.jsonl").is_file())

    def test_init_renders_all_backend_profiles(self) -> None:
        cases = {
            "codex-native": ("codex_native:", "codex-current-session"),
            "openai-compatible": ('provider: "openai"', "${CUSTOM_MODEL_KEY}"),
            "claude-code": ('provider: "claude_code"', "sonnet"),
            "manual": ("manual_mode: true", 'name: "manual"'),
        }
        for backend, expected in cases.items():
            target = Path(self.temp.name) / backend
            args = Namespace(
                experiment=str(target),
                name=backend,
                backend=backend,
                model=None,
                api_base=(
                    "https://gateway.example/v1"
                    if backend == "openai-compatible"
                    else None
                ),
                credential_env=(
                    "CUSTOM_MODEL_KEY"
                    if backend == "openai-compatible"
                    else None
                ),
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(skill.command_init(args), 0)
            text = (target / "config.yaml").read_text(encoding="utf-8")
            self.assertIn(expected[0], text)
            self.assertIn(expected[1], text)
            if backend == "manual":
                self.assertNotIn('provider: "openai"', text)

    def test_codex_native_needs_no_external_model_or_credential(self) -> None:
        target = Path(self.temp.name) / "native"
        args = Namespace(
            experiment=str(target),
            name="native",
            backend="codex-native",
            model=None,
            api_base=None,
            credential_env=None,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(skill.command_init(args), 0)
        runtime = skill.resolve_model_runtime(target, mode="host", for_run=True)
        self.assertEqual(runtime["status"], "ready")
        self.assertEqual(runtime["backend_types"], ["codex-native"])
        self.assertTrue(runtime["native_mode"])
        self.assertEqual(runtime["credential_environment_names"], [])

    def test_native_run_returns_agent_guidance_without_starting_openevolve(self) -> None:
        target = Path(self.temp.name) / "native-run"
        args = Namespace(
            experiment=str(target),
            name="native-run",
            backend="codex-native",
            model=None,
            api_base=None,
            credential_env=None,
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(skill.command_init(args), 0)
        original_root = self.root
        self.root = target
        self.make_valid_for_run()
        self.root = original_root
        payload = io.StringIO()
        with contextlib.redirect_stdout(payload):
            result = skill.run_evolution(
                Namespace(
                    experiment=str(target),
                    mode="host",
                    iterations=None,
                    target_score=None,
                    docker_profile=None,
                    docker_memory=None,
                    docker_cpus=None,
                    acknowledge_host_risk=True,
                    dry_run=False,
                    checkpoint=None,
                ),
                resume=False,
            )
        self.assertEqual(result, 0)
        self.assertIn("native_agent_required", payload.getvalue())

    def test_fallback_yaml_parser_preserves_top_level_lists(self) -> None:
        problem = skill.load_simple_problem_yaml(self.root / "problem.yaml")
        self.assertEqual(len(problem["seeds"]), 3)
        self.assertIn("TODO", problem["hard_constraints"][0])
        self.assertIn("TODO", problem["strong_baselines"][0])
        self.assertEqual(problem["runtime"]["docker"]["profile"], "standard")
        self.assertEqual(problem["code_budget"]["hard_max_chars"], 20000)

    def test_doctor_returns_nonzero_when_no_execution_mode_is_ready(self) -> None:
        with mock.patch.object(
            skill,
            "doctor_data",
            return_value={"ready_for_docker": False, "ready_for_host": False},
        ):
            with contextlib.redirect_stdout(io.StringIO()):
                result = skill.command_doctor(Namespace(experiment=None))
        self.assertEqual(result, 2)

    def test_doctor_without_experiment_is_provider_neutral(self) -> None:
        with mock.patch.object(
            skill,
            "installed_openevolve_version",
            return_value=skill.PINNED_OPENEVOLVE_VERSION,
        ):
            with mock.patch.object(
                skill,
                "docker_status",
                return_value={"installed": False, "server_available": False},
            ):
                with mock.patch.object(
                    skill,
                    "openevolve_entrypoint_status",
                    return_value={"ready": True},
                ):
                    with mock.patch.object(
                        skill,
                        "host_resource_snapshot",
                        return_value={},
                    ):
                        data = skill.doctor_data()
        self.assertEqual(data["required_credential_environments"], [])
        self.assertIsNone(data["required_credential_environment"])
        self.assertTrue(data["ready_for_host"])

    def test_entrypoint_registration_mismatch_is_not_ready(self) -> None:
        entries = SimpleNamespace(
            select=lambda **_: [
                SimpleNamespace(
                    name=skill.CONSOLE_ENTRYPOINT_NAME,
                    value="wrong.module:main",
                )
            ]
        )
        fake_module = SimpleNamespace(main=lambda: 0)
        with mock.patch.object(skill.importlib.util, "find_spec", return_value=object()):
            with mock.patch("builtins.__import__", return_value=fake_module):
                with mock.patch.object(
                    skill.importlib.metadata,
                    "entry_points",
                    return_value=entries,
                ):
                    with mock.patch.object(
                        skill.shutil,
                        "which",
                        return_value="openevolve-run",
                    ):
                        with mock.patch.object(
                            skill,
                            "run_help_probe",
                            return_value={"ok": True, "returncode": 0},
                        ):
                            status = skill.openevolve_entrypoint_status()
        self.assertFalse(status["ready"])
        self.assertFalse(status["console_script"]["target_matches"])

    @unittest.skipUnless(
        importlib.util.find_spec("openevolve") is not None,
        "openevolve is only installed in the compatibility test environment",
    )
    def test_pinned_package_real_entrypoints_execute(self) -> None:
        status = skill.openevolve_entrypoint_status()
        self.assertTrue(status["ready"], status)
        self.assertEqual(
            status["console_script"]["registered_target"],
            skill.CONSOLE_ENTRYPOINT_TARGET,
        )
        self.assertTrue(status["host_module"]["help_probe"]["ok"])
        self.assertTrue(status["console_script"]["help_probe"]["ok"])

    def test_help_probe_reports_timeout(self) -> None:
        result = skill.run_help_probe(
            [sys.executable, "-c", "import time; time.sleep(2)"],
            timeout=0.1,
        )
        self.assertFalse(result["ok"])
        self.assertIsNone(result["returncode"])

    def test_template_evaluator_executes_without_external_api(self) -> None:
        (self.root / "data" / "search.jsonl").write_text(
            '{"input": [1, 2], "expected": [1, 2]}\n', encoding="utf-8"
        )
        evaluator_path = self.root / "evaluator.py"
        evaluator_spec = importlib.util.spec_from_file_location("fixture_evaluator", evaluator_path)
        assert evaluator_spec and evaluator_spec.loader
        evaluator_module = importlib.util.module_from_spec(evaluator_spec)
        evaluator_spec.loader.exec_module(evaluator_module)
        metrics = evaluator_module.evaluate(str(self.root / "initial_program.py"))
        self.assertIn("combined_score", metrics)
        self.assertEqual(metrics["case_count"], 1.0)

    def test_pre_run_validation_accepts_completed_fixture(self) -> None:
        self.make_valid_for_run()
        result = skill.validate_experiment(self.root, for_run=True)
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["validation_level"], "run")
        self.assertGreater(result["resolved_code_limit"], 0)
        self.assertEqual(result["runtime_resources"]["memory"], "4g")

    def test_basic_validation_identifies_its_reduced_scope(self) -> None:
        result = skill.validate_experiment(self.root)
        self.assertEqual(result["validation_level"], "basic")
        self.assertTrue(
            any("use --for-run" in warning for warning in result["warnings"])
        )

    def test_invalid_evaluator_is_rejected(self) -> None:
        evaluator_path = self.root / "evaluator.py"
        evaluator_path.write_text("def other(path):\n    return {'score': 1.0}\n", encoding="utf-8")
        result = skill.validate_experiment(self.root)
        self.assertFalse(result["valid"])
        self.assertTrue(any("evaluate(program_path)" in item for item in result["errors"]))
        self.assertTrue(any("combined_score" in item for item in result["errors"]))

    def test_embedded_secret_is_rejected(self) -> None:
        config_path = self.root / "config.yaml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8") + "\napi_key: " + ("a" * 24) + "\n",
            encoding="utf-8",
        )
        result = skill.validate_experiment(self.root)
        self.assertFalse(result["valid"])
        self.assertTrue(any("embedded secret" in item for item in result["errors"]))

    def test_docker_command_passes_only_environment_name(self) -> None:
        self.make_valid_for_run()
        secret = "provider-value-not-present-in-command"
        with mock.patch.dict(
            os.environ,
            {"MODEL_API_KEY": secret, "GEMINI_API_KEY": "unrelated-provider-value"},
            clear=False,
        ):
            _, command, _ = skill.docker_commands(self.root, 5, None, None)
        joined = " ".join(command)
        self.assertIn("MODEL_API_KEY", joined)
        self.assertNotIn("GEMINI_API_KEY", joined)
        self.assertNotIn(secret, joined)
        self.assertIn("no-new-privileges", joined)
        self.assertIn(str(self.root), joined)
        self.assertNotIn("--rm", command)

    def test_multiple_model_credentials_are_passed_by_name(self) -> None:
        self.make_valid_for_run()
        config_path = self.root / "config.yaml"
        text = config_path.read_text(encoding="utf-8").replace(
            '  api_key: "${MODEL_API_KEY}"\n',
            "",
        ).replace(
            '    - name: "fixture-model"\n      weight: 1.0',
            """    - name: "fixture-model"
      api_key: "${FIRST_MODEL_KEY}"
      weight: 0.5
    - name: "fixture-model-2"
      api_key: "${SECOND_MODEL_KEY}"
      weight: 0.5""",
        )
        config_path.write_text(text, encoding="utf-8")
        values = {
            "FIRST_MODEL_KEY": "first-secret-value",
            "SECOND_MODEL_KEY": "second-secret-value",
        }
        with mock.patch.dict(os.environ, values, clear=False):
            runtime = skill.resolve_model_runtime(self.root, mode="docker")
            _, command, _ = skill.docker_commands(self.root, 1, None, None)
        self.assertEqual(
            runtime["credential_environment_names"],
            ["FIRST_MODEL_KEY", "SECOND_MODEL_KEY"],
        )
        joined = " ".join(command)
        self.assertIn("FIRST_MODEL_KEY", joined)
        self.assertIn("SECOND_MODEL_KEY", joined)
        self.assertNotIn(values["FIRST_MODEL_KEY"], joined)
        self.assertNotIn(values["SECOND_MODEL_KEY"], joined)
        if importlib.util.find_spec("openevolve") is not None:
            from openevolve.config import Config

            with mock.patch.dict(os.environ, values, clear=False):
                loaded = Config.from_yaml(config_path)
            self.assertEqual(len(loaded.llm.models), 2)

    def test_top_level_credential_is_required_even_with_model_overrides(self) -> None:
        self.make_valid_for_run()
        config_path = self.root / "config.yaml"
        text = config_path.read_text(encoding="utf-8").replace(
            '    - name: "fixture-model"\n      weight: 1.0',
            """    - name: "fixture-model"
      api_key: "${PER_MODEL_KEY}"
      weight: 1.0""",
        )
        config_path.write_text(text, encoding="utf-8")
        runtime = skill.resolve_model_runtime(self.root)
        self.assertEqual(
            runtime["credential_environment_names"],
            ["MODEL_API_KEY", "PER_MODEL_KEY"],
        )

    def test_unknown_provider_is_rejected(self) -> None:
        self.make_valid_for_run()
        config_path = self.root / "config.yaml"
        config_path.write_text(
            config_path.read_text(encoding="utf-8").replace(
                'provider: "openai"',
                'provider: "mystery-provider"',
            ),
            encoding="utf-8",
        )
        result = skill.validate_experiment(self.root)
        self.assertFalse(result["valid"])
        self.assertTrue(
            any("Unsupported llm provider" in item for item in result["errors"])
        )

    def test_api_base_rejects_embedded_credentials_and_sensitive_query(self) -> None:
        _, userinfo_errors = skill.redact_api_base(
            "https://user:password@example.invalid/v1"
        )
        _, query_errors = skill.redact_api_base(
            "https://example.invalid/v1?api_key=secret"
        )
        _, port_errors = skill.redact_api_base(
            "https://example.invalid:not-a-port/v1"
        )
        self.assertTrue(any("username or password" in item for item in userinfo_errors))
        self.assertTrue(any("sensitive query" in item for item in query_errors))
        self.assertTrue(any("invalid host or port" in item for item in port_errors))

    def test_docker_local_model_gateway_mapping_and_localhost_rejection(self) -> None:
        self.make_valid_for_run()
        config_path = self.root / "config.yaml"
        text = config_path.read_text(encoding="utf-8")
        config_path.write_text(
            text.replace(
                "https://example.invalid/v1",
                "http://host.docker.internal:11434/v1",
            ),
            encoding="utf-8",
        )
        runtime = skill.resolve_model_runtime(self.root, mode="docker")
        self.assertTrue(runtime["requires_host_gateway"])
        _, command, _ = skill.docker_commands(self.root, 1, None, None)
        self.assertIn("host.docker.internal:host-gateway", command)

        config_path.write_text(
            config_path.read_text(encoding="utf-8").replace(
                "host.docker.internal",
                "localhost",
            ),
            encoding="utf-8",
        )
        result = skill.validate_experiment(self.root, for_run=True)
        self.assertFalse(result["valid"])
        self.assertTrue(
            any("Docker cannot reach" in item for item in result["errors"])
        )

    def test_claude_code_is_host_only_and_manual_needs_no_credential(self) -> None:
        config_path = self.root / "config.yaml"
        skill.configure_initialized_backend(
            config_path,
            "claude-code",
            "sonnet",
            None,
            None,
        )
        host = skill.resolve_model_runtime(self.root, mode="host")
        docker = skill.resolve_model_runtime(self.root, mode="docker")
        self.assertEqual(host["backend_types"], ["claude-code"])
        self.assertEqual(host["credential_environment_names"], [])
        self.assertTrue(any("host-only" in item for item in docker["errors"]))

        skill.configure_initialized_backend(
            config_path,
            "manual",
            "external-agent",
            None,
            None,
        )
        manual = skill.resolve_model_runtime(self.root, mode="docker")
        self.assertEqual(manual["backend_types"], ["manual"])
        self.assertEqual(manual["credential_environment_names"], [])
        self.assertEqual(manual["status"], "ready")

    def test_unpinned_evaluator_requirement_is_rejected(self) -> None:
        (self.root / "requirements-evaluator.txt").write_text("pandas>=2\n", encoding="utf-8")
        result = skill.validate_experiment(self.root)
        self.assertFalse(result["valid"])
        self.assertTrue(any("exact == version" in item for item in result["errors"]))

    def test_docker_dry_run_does_not_require_docker_or_credentials(self) -> None:
        self.make_valid_for_run()
        args = Namespace(
            experiment=str(self.root),
            checkpoint=None,
            mode="docker",
            acknowledge_host_risk=False,
            iterations=1,
            target_score=None,
            dry_run=True,
        )
        with contextlib.redirect_stdout(io.StringIO()) as output:
            result = skill.run_evolution(args, resume=False)
        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["mode"], "docker")
        self.assertTrue(payload["dry_run"])

    def test_host_dry_run_uses_pinned_package(self) -> None:
        self.make_valid_for_run()
        args = Namespace(
            experiment=str(self.root),
            checkpoint=None,
            mode="host",
            acknowledge_host_risk=True,
            iterations=1,
            target_score=None,
            dry_run=True,
        )
        with mock.patch.object(
            skill, "installed_openevolve_version", return_value=skill.PINNED_OPENEVOLVE_VERSION
        ):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                result = skill.run_evolution(args, resume=False)
        self.assertEqual(result, 0)
        payload = json.loads(output.getvalue())
        self.assertIn("openevolve.cli", payload["argv"])

    def test_host_mode_requires_acknowledgement(self) -> None:
        self.make_valid_for_run()
        args = Namespace(
            experiment=str(self.root),
            checkpoint=None,
            mode="host",
            acknowledge_host_risk=False,
            iterations=1,
            target_score=None,
            dry_run=True,
        )
        with self.assertRaises(skill.SkillError):
            skill.run_evolution(args, resume=False)

    def test_mocked_host_run_and_resume_share_budget_ledger(self) -> None:
        self.make_valid_for_run()
        base_args = {
            "experiment": str(self.root),
            "mode": "host",
            "acknowledge_host_risk": True,
            "iterations": 1,
            "target_score": None,
            "dry_run": False,
            "docker_profile": None,
            "docker_memory": None,
            "docker_cpus": None,
        }
        fake_result = {
            "returncode": 0,
            "elapsed_seconds": 0.1,
            "timed_out": False,
            "interrupted": False,
        }
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": "fixture-only"}, clear=False):
            with mock.patch.object(
                skill,
                "installed_openevolve_version",
                return_value=skill.PINNED_OPENEVOLVE_VERSION,
            ):
                with mock.patch.object(
                    skill,
                    "openevolve_entrypoint_status",
                    return_value={"host_module": {"help_probe": {"ok": True}}},
                ):
                    with mock.patch.object(
                        skill, "run_timed_process", return_value=fake_result
                    ):
                        first = skill.run_evolution(
                            Namespace(**base_args, checkpoint=None), resume=False
                        )
                        checkpoint = (
                            self.root
                            / "openevolve_output"
                            / "checkpoints"
                            / "checkpoint_0"
                        )
                        checkpoint.mkdir(parents=True)
                        second = skill.run_evolution(
                            Namespace(**base_args, checkpoint=str(checkpoint)),
                            resume=True,
                        )
        self.assertEqual(first, 0)
        self.assertEqual(second, 0)
        ledger = json.loads(
            (self.root / "results" / "budget_ledger.json").read_text(encoding="utf-8")
        )
        self.assertEqual(ledger["consumed_iterations"], 2)
        manifest = json.loads(
            (self.root / "results" / "run_manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["schema_version"], 2)
        self.assertEqual(manifest["stop_reason"], "completed")
        self.assertTrue((self.root / "results" / "effective_config.yaml").is_file())

    def test_resume_rejects_checkpoint_outside_experiment(self) -> None:
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        with self.assertRaises(skill.SkillError):
            skill.resolve_checkpoint(self.root, str(outside))

    def test_context_budget_resolves_dynamic_limit(self) -> None:
        self.make_valid_for_run()
        problem = skill.load_yaml(self.root / "problem.yaml")
        resolved = skill.resolve_context_budget(self.root, problem)
        self.assertEqual(resolved["status"], "valid")
        self.assertLessEqual(resolved["resolved_code_limit"], 20000)
        self.assertGreaterEqual(
            resolved["resolved_code_limit"], resolved["initial_program_chars"]
        )

    def test_explicit_legacy_code_limit_is_a_hard_cap(self) -> None:
        self.make_valid_for_run()
        config = self.root / "config.yaml"
        config.write_text(
            config.read_text(encoding="utf-8") + "\nmax_code_length: 1200\n",
            encoding="utf-8",
        )
        resolved = skill.resolve_context_budget(
            self.root, skill.load_yaml(self.root / "problem.yaml")
        )
        self.assertEqual(resolved["explicit_config_limit"], 1200)
        self.assertEqual(resolved["resolved_code_limit"], 1200)

    def test_full_rewrite_is_limited_by_generation_budget(self) -> None:
        self.make_valid_for_run()
        config = self.root / "config.yaml"
        config.write_text(
            config.read_text(encoding="utf-8").replace(
                "diff_based_evolution: true", "diff_based_evolution: false"
            ),
            encoding="utf-8",
        )
        resolved = skill.resolve_context_budget(
            self.root, skill.load_yaml(self.root / "problem.yaml")
        )
        self.assertFalse(resolved["diff_based_evolution"])
        self.assertLessEqual(
            resolved["resolved_code_limit"], resolved["generation_code_cap"]
        )

    def test_oversized_initial_program_is_rejected(self) -> None:
        self.make_valid_for_run()
        initial = self.root / "initial_program.py"
        text = initial.read_text(encoding="utf-8")
        text = text.replace(
            "# EVOLVE-BLOCK-END",
            "# " + ("x" * 21000) + "\n# EVOLVE-BLOCK-END",
        )
        initial.write_text(text, encoding="utf-8")
        result = skill.validate_experiment(self.root, for_run=True)
        self.assertFalse(result["valid"])
        self.assertTrue(any("cannot fit" in item for item in result["errors"]))

    def test_effective_config_contains_one_resolved_limit(self) -> None:
        self.make_valid_for_run()
        path = skill.write_effective_config(self.root, 4321)
        text = path.read_text(encoding="utf-8")
        self.assertEqual(text.count("max_code_length:"), 1)
        self.assertIn("max_code_length: 4321", text)
        self.assertNotIn("REPLACE_WITH_MODEL", text)

    @unittest.skipUnless(
        importlib.util.find_spec("openevolve") is not None,
        "openevolve is only installed in the compatibility test environment",
    )
    def test_effective_config_loads_in_pinned_openevolve(self) -> None:
        from openevolve.config import Config

        self.make_valid_for_run()
        path = skill.write_effective_config(self.root, 4321)
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": "fixture-only"}, clear=False):
            loaded = Config.from_yaml(path)
        self.assertEqual(loaded.max_code_length, 4321)

    @unittest.skipUnless(
        importlib.util.find_spec("openevolve") is not None,
        "openevolve is only installed in the compatibility test environment",
    )
    def test_all_backend_profiles_load_in_pinned_openevolve(self) -> None:
        from openevolve.config import Config

        profiles = (
            ("openai-compatible", "fixture-model", "https://example.invalid/v1", "MODEL_API_KEY"),
            ("claude-code", "sonnet", None, None),
            ("manual", "external-agent", None, None),
        )
        with mock.patch.dict(os.environ, {"MODEL_API_KEY": "fixture-only"}, clear=False):
            for backend, model, api_base, credential_env in profiles:
                target = Path(self.temp.name) / f"compat-{backend}"
                with contextlib.redirect_stdout(io.StringIO()):
                    skill.command_init(
                        Namespace(
                            experiment=str(target),
                            name=backend,
                            backend=backend,
                            model=model,
                            api_base=api_base,
                            credential_env=credential_env,
                        )
                    )
                loaded = Config.from_yaml(target / "config.yaml")
                self.assertEqual(loaded.llm.models[0].name, model)
                if backend == "manual":
                    self.assertTrue(loaded.llm.manual_mode)
                elif backend == "claude-code":
                    self.assertEqual(loaded.llm.models[0].provider, "claude_code")

    @unittest.skipUnless(
        importlib.util.find_spec("openevolve") is not None,
        "openevolve is only installed in the compatibility test environment",
    )
    def test_manual_queue_round_trip_without_external_api(self) -> None:
        from openevolve.config import Config
        from openevolve.llm.openai import OpenAILLM

        skill.configure_initialized_backend(
            self.root / "config.yaml",
            "manual",
            "external-agent",
            None,
            None,
        )
        loaded = Config.from_yaml(self.root / "config.yaml")
        queue = self.root / "manual-queue"
        model_cfg = loaded.llm.models[0]
        model_cfg._manual_queue_dir = str(queue)
        client = OpenAILLM(model_cfg)

        async def round_trip() -> str:
            pending = asyncio.create_task(client.generate("Return fixture answer"))
            task_file = None
            for _ in range(40):
                candidates = list(queue.glob("*.json"))
                candidates = [
                    path for path in candidates if not path.name.endswith(".answer.json")
                ]
                if candidates:
                    task_file = candidates[0]
                    break
                await asyncio.sleep(0.05)
            self.assertIsNotNone(task_file)
            assert task_file is not None
            answer = task_file.with_name(f"{task_file.stem}.answer.json")
            answer.write_text(
                json.dumps({"answer": "fixture response"}),
                encoding="utf-8",
            )
            return await asyncio.wait_for(pending, timeout=3)

        self.assertEqual(asyncio.run(round_trip()), "fixture response")

    def test_public_contract_rejects_signature_change(self) -> None:
        evaluator_path = self.root / "evaluator.py"
        evaluator_spec = importlib.util.spec_from_file_location(
            "contract_evaluator", evaluator_path
        )
        assert evaluator_spec and evaluator_spec.loader
        evaluator_module = importlib.util.module_from_spec(evaluator_spec)
        evaluator_spec.loader.exec_module(evaluator_module)
        candidate = self.root / "candidate.py"
        candidate.write_text("def solve(items, extra=None):\n    return list(items)\n", encoding="utf-8")
        violations = evaluator_module.validate_public_contract(str(candidate))
        self.assertTrue(any("changed signature" in item for item in violations))
        metrics = evaluator_module.evaluate(str(candidate))
        self.assertEqual(metrics["combined_score"], 0.0)
        self.assertEqual(metrics["contract_valid"], 0.0)

    def test_resource_profiles_and_cli_overrides(self) -> None:
        problem = skill.load_yaml(self.root / "problem.yaml")
        large = skill.resolve_runtime_resources(problem, profile_override="large")
        self.assertEqual(large["memory"], "16g")
        custom = skill.resolve_runtime_resources(
            problem,
            profile_override="standard",
            memory_override="8g",
            cpus_override=6.0,
        )
        self.assertEqual(custom["memory"], "8g")
        self.assertEqual(custom["cpus"], 6.0)
        with self.assertRaises(skill.SkillError):
            skill.resolve_runtime_resources(
                problem, memory_override="4g --privileged"
            )

    def test_docker_oom_state_is_classified(self) -> None:
        reason = skill.classify_stop_reason(
            137, docker_inspect_state={"OOMKilled": True}
        )
        self.assertEqual(reason, "oom_killed")
        self.assertEqual(
            skill.classify_stop_reason(137), "resource_exhausted_or_sigkill"
        )

    def test_wall_timeout_terminates_process_group(self) -> None:
        result = skill.run_timed_process(
            [sys.executable, "-c", "import time; time.sleep(2)"],
            self.root,
            0.1,
        )
        self.assertTrue(result["timed_out"])

    def test_budget_reservation_is_cumulative_and_fail_closed(self) -> None:
        self.make_valid_for_run()
        problem = skill.load_yaml(self.root / "problem.yaml")
        limits, errors = skill.budget_limits(problem)
        self.assertEqual(errors, [])
        reservation, _, _ = skill.reserve_budget(self.root, limits, 5)
        first = skill.finalize_budget(
            self.root,
            limits,
            reservation,
            12.0,
            3,
            "completed",
            "docker",
            0,
        )
        self.assertEqual(first["consumed_iterations"], 3)
        second_reservation, _, _ = skill.reserve_budget(self.root, limits, 4)
        second = skill.finalize_budget(
            self.root,
            limits,
            second_reservation,
            5.0,
            None,
            "execution_failed",
            "docker",
            1,
        )
        self.assertEqual(second["consumed_iterations"], 7)
        self.assertAlmostEqual(second["estimated_cost_usd"], 0.35)
        self.assertAlmostEqual(second["consumed_wall_time_seconds"], 17.0)

    def test_budget_rejects_iteration_and_estimated_cost_overruns(self) -> None:
        self.make_valid_for_run()
        limits, _ = skill.budget_limits(skill.load_yaml(self.root / "problem.yaml"))
        status = skill.budget_status(self.root, limits)
        with self.assertRaises(skill.SkillError):
            skill.validate_budget_request(status, limits, 51)
        expensive = dict(limits)
        expensive["estimated_cost_per_iteration_usd"] = 2.0
        with self.assertRaises(skill.SkillError):
            skill.validate_budget_request(status, expensive, 6)

    def test_summarize_creates_standard_outputs(self) -> None:
        self.make_valid_for_run()
        best_dir = self.root / "openevolve_output" / "best"
        best_dir.mkdir(parents=True)
        (best_dir / "best_program.py").write_text("def solve(x): return x\n", encoding="utf-8")
        (best_dir / "best_program_info.json").write_text(
            json.dumps(
                {
                    "id": "candidate-1",
                    "generation": 3,
                    "iteration": 8,
                    "metrics": {"combined_score": 0.9, "correctness": 1.0},
                    "language": "python",
                }
            ),
            encoding="utf-8",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            result = skill.command_summarize(Namespace(experiment=str(self.root)))
        self.assertEqual(result, 0)
        summary = json.loads((self.root / "results" / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["schema_version"], 2)
        self.assertEqual(summary["novelty"]["claim_status"], "candidate improvement")
        self.assertEqual(summary["task_validation"]["status"], "incomplete")
        self.assertEqual(summary["baseline"]["run_count"], 3)
        self.assertIn("budget", summary)
        self.assertIn("context_budget", summary)
        self.assertEqual(summary["limitations_audit"]["audit_status"], "complete")
        self.assertEqual(
            summary["model_runtime"]["backend_types"],
            ["openai-compatible"],
        )
        self.assertTrue((self.root / "results" / "best_program.py").is_file())
        self.assertTrue((self.root / "research_report.md").is_file())
        report = (self.root / "research_report.md").read_text(encoding="utf-8")
        self.assertIn("局限性与有效性威胁", report)

    def test_incomplete_limitations_keep_claim_provisional(self) -> None:
        self.make_valid_for_run()
        (self.root / "research" / "limitations.md").write_text(
            "STATUS: incomplete\n\n- TODO: test external validity.\n",
            encoding="utf-8",
        )
        (self.root / "research" / "novelty_audit.md").write_text(
            "STATUS: complete\n\nClosest methods and differences were audited.\n",
            encoding="utf-8",
        )
        best_dir = self.root / "openevolve_output" / "best"
        best_dir.mkdir(parents=True)
        (best_dir / "best_program.py").write_text(
            "def solve(x): return x\n",
            encoding="utf-8",
        )
        (best_dir / "best_program_info.json").write_text(
            json.dumps({"metrics": {"combined_score": 0.9}}),
            encoding="utf-8",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                skill.command_summarize(Namespace(experiment=str(self.root))),
                0,
            )
        summary = json.loads(
            (self.root / "results" / "summary.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            summary["novelty"]["claim_status"],
            "candidate result; limitations audit incomplete",
        )
        self.assertEqual(summary["limitations_audit"]["audit_status"], "incomplete")

    def test_claim_upgrades_only_after_replicated_holdout_evidence(self) -> None:
        self.make_valid_for_run()
        best_dir = self.root / "openevolve_output" / "best"
        best_dir.mkdir(parents=True)
        (best_dir / "best_program.py").write_text(
            "def solve(x): return x\n",
            encoding="utf-8",
        )
        (best_dir / "best_program_info.json").write_text(
            json.dumps({"metrics": {"combined_score": 0.9}}),
            encoding="utf-8",
        )
        comparison_rows = [
            "method,split,seed,combined_score,correctness,runtime_seconds,cost_usd,notes",
            "candidate,holdout,11,0.90,1.0,1.0,0.0,",
            "candidate,holdout,23,0.91,1.0,1.0,0.0,",
            "candidate,holdout,47,0.92,1.0,1.0,0.0,",
            "initial_program,holdout,11,0.30,1.0,1.0,0.0,",
            "initial_program,holdout,23,0.31,1.0,1.0,0.0,",
            "initial_program,holdout,47,0.32,1.0,1.0,0.0,",
            "reference_dynamic_programming,holdout,11,0.50,1.0,1.0,0.0,",
            "reference_dynamic_programming,holdout,23,0.51,1.0,1.0,0.0,",
            "reference_dynamic_programming,holdout,47,0.52,1.0,1.0,0.0,",
        ]
        (self.root / "results" / "baseline_comparison.csv").write_text(
            "\n".join(comparison_rows) + "\n",
            encoding="utf-8",
        )
        ablation_rows = [
            "candidate_id,removed_component,split,seed,combined_score,delta_vs_full,notes",
            "candidate-1,memoization,holdout,11,0.80,-0.10,",
            "candidate-1,memoization,holdout,23,0.81,-0.10,",
            "candidate-1,memoization,holdout,47,0.82,-0.10,",
        ]
        (self.root / "results" / "ablation_results.csv").write_text(
            "\n".join(ablation_rows) + "\n",
            encoding="utf-8",
        )

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                skill.command_summarize(Namespace(experiment=str(self.root))),
                0,
            )
        summary_path = self.root / "results" / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertEqual(summary["task_validation"]["status"], "complete")
        self.assertEqual(
            summary["novelty"]["claim_status"],
            "validated task-specific improvement",
        )

        (self.root / "research" / "novelty_audit.md").write_text(
            """# Novelty audit

STATUS: complete

The closest methods, code overlaps, and remaining differences were audited.
""",
            encoding="utf-8",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                skill.command_summarize(Namespace(experiment=str(self.root))),
                0,
            )
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertEqual(
            summary["novelty"]["claim_status"],
            "research novelty audited",
        )


if __name__ == "__main__":
    unittest.main()
