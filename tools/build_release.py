#!/usr/bin/env python3
"""Validate and build the GitHub and Codex Skill release archives."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import zipfile
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "skill" / "openevolve-innovator"
SKILL_TOP_LEVEL = {"SKILL.md", "agents", "assets", "references", "scripts"}
EXCLUDED_NAMES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "outputs",
    "dist",
    "openevolve_output",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:ghp|github_pat)_[A-Za-z0-9_]{16,}\b"),
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
)
STALE_PATTERNS = (
    "openevolve-algorithm-discovery",
    "OpenEvolve Algorithm Discovery",
    "openevolve-research-guard",
)


def iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in EXCLUDED_NAMES for part in relative.parts):
            continue
        if path.is_file() and path.suffix.lower() not in EXCLUDED_SUFFIXES:
            yield path


def validate_frontmatter() -> None:
    skill_file = SKILL_ROOT / "SKILL.md"
    text = skill_file.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, flags=re.DOTALL)
    if not match:
        raise RuntimeError("SKILL.md has invalid YAML frontmatter")
    frontmatter = yaml.safe_load(match.group(1))
    if set(frontmatter) != {"name", "description"}:
        raise RuntimeError("SKILL.md frontmatter must contain only name and description")
    if frontmatter["name"] != "openevolve-innovator":
        raise RuntimeError("SKILL.md name does not match the Skill directory")


def validate_yaml() -> None:
    for path in iter_files(SKILL_ROOT):
        if path.suffix.lower() in {".yaml", ".yml"}:
            yaml.safe_load(path.read_text(encoding="utf-8"))


def validate_contents() -> None:
    if not SKILL_ROOT.is_dir():
        raise RuntimeError(f"Missing Skill directory: {SKILL_ROOT}")
    actual = {path.name for path in SKILL_ROOT.iterdir()}
    unexpected = actual - SKILL_TOP_LEVEL
    missing = {"SKILL.md", "agents", "assets", "references", "scripts"} - actual
    if unexpected or missing:
        raise RuntimeError(
            f"Unexpected Skill structure; missing={sorted(missing)}, "
            f"unexpected={sorted(unexpected)}"
        )
    for root in (REPO_ROOT, SKILL_ROOT):
        for path in iter_files(root):
            if path.stat().st_size > 5 * 1024 * 1024:
                raise RuntimeError(f"Unexpected large file: {path}")
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for pattern in SECRET_PATTERNS:
                if pattern.search(text):
                    raise RuntimeError(f"Secret-shaped value found in {path}")
            if path.resolve() != Path(__file__).resolve():
                for stale in STALE_PATTERNS:
                    if stale.lower() in text.lower():
                        raise RuntimeError(f"Stale name {stale!r} found in {path}")


def validate() -> None:
    validate_frontmatter()
    validate_yaml()
    validate_contents()


def write_zip(source: Path, destination: Path, archive_root: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in iter_files(source):
            relative = path.relative_to(source).as_posix()
            archive.write(path, f"{archive_root}/{relative}")


def write_skill_zip(destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in iter_files(SKILL_ROOT):
            relative = path.relative_to(SKILL_ROOT).as_posix()
            archive.write(path, f"openevolve-innovator/{relative}")
        for name in ("LICENSE", "NOTICE"):
            archive.write(REPO_ROOT / name, f"openevolve-innovator/{name}")


def validate_archive(path: Path, *, skill_only: bool) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    if not names or any(
        name.startswith(("/", "\\"))
        or ".." in Path(name.replace("\\", "/")).parts
        for name in names
    ):
        raise RuntimeError(f"Unsafe archive paths in {path}")
    roots = {name.replace("\\", "/").split("/", 1)[0] for name in names}
    if roots != {"openevolve-innovator"}:
        raise RuntimeError(f"Unexpected archive root in {path}: {sorted(roots)}")
    normalized = {name.replace("\\", "/") for name in names}
    if skill_only:
        if any("/README.md" in name or "/.github/" in name for name in normalized):
            raise RuntimeError("Skill archive contains repository-only files")
        for required in (
            "openevolve-innovator/SKILL.md",
            "openevolve-innovator/LICENSE",
            "openevolve-innovator/NOTICE",
        ):
            if required not in normalized:
                raise RuntimeError(f"Skill archive is missing {required}")
    else:
        for required in (
            "openevolve-innovator/README.md",
            "openevolve-innovator/LICENSE",
            "openevolve-innovator/.github/workflows/ci.yml",
            "openevolve-innovator/skill/openevolve-innovator/SKILL.md",
        ):
            if required not in normalized:
                raise RuntimeError(f"Repository archive is missing {required}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--output-dir", default="dist")
    args = parser.parse_args()

    validate()
    if args.check_only:
        print("Repository and Skill validation passed.")
        return 0

    output_dir = Path(args.output_dir).expanduser().resolve()
    skill_zip = output_dir / "openevolve-innovator.zip"
    repo_zip = output_dir / "openevolve-innovator-github.zip"
    write_skill_zip(skill_zip)
    write_zip(REPO_ROOT, repo_zip, "openevolve-innovator")
    validate_archive(skill_zip, skill_only=True)
    validate_archive(repo_zip, skill_only=False)
    for path in (skill_zip, repo_zip):
        print(f"{path}\t{path.stat().st_size}\t{sha256(path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
