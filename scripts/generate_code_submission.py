#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import subprocess
from datetime import datetime, timezone
from pathlib import Path


INCLUDE_DIRS = [
    "backend/src/icici_breeze_backend",
    "backend/tests",
    "frontend/src",
    "frontend/public",
    "deploy",
    ".github/workflows",
]

INCLUDE_FILES = [
    "README.md",
    "Dockerfile",
    "docker-compose.yml",
    "dev.sh",
    "nginx.conf",
]

EXCLUDE_GLOBS = [
    "legacy/**",
    "**/node_modules/**",
    "**/.venv/**",
    "**/.next/**",
    "**/__pycache__/**",
    "**/.pytest_cache/**",
    "**/logs/**",
    "**/*.sqlite3",
    ".env*",
    "backend/static/Sample Data Files/creds.json",
]


def is_excluded(rel_path: str) -> bool:
    for pattern in EXCLUDE_GLOBS:
        if fnmatch.fnmatch(rel_path, pattern):
            return True
    return False


def is_probably_text(path: Path) -> bool:
    try:
        raw = path.read_bytes()
    except OSError:
        return False
    if b"\x00" in raw:
        return False
    try:
        raw.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def fence_lang(rel_path: str) -> str:
    suffix = Path(rel_path).suffix.lower()
    return {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".js": "javascript",
        ".jsx": "jsx",
        ".json": "json",
        ".md": "markdown",
        ".yml": "yaml",
        ".yaml": "yaml",
        ".sh": "bash",
        ".css": "css",
        ".html": "html",
        ".sql": "sql",
        ".txt": "text",
        ".conf": "nginx",
    }.get(suffix, "text")


def git_commit_hash(repo_root: Path) -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return out
    except Exception:
        return "unknown"


def collect_files(repo_root: Path) -> list[str]:
    files: list[str] = []

    for rel_dir in INCLUDE_DIRS:
        abs_dir = repo_root / rel_dir
        if not abs_dir.exists():
            continue
        for p in sorted(abs_dir.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(repo_root).as_posix()
            if is_excluded(rel):
                continue
            if not is_probably_text(p):
                continue
            files.append(rel)

    for rel_file in INCLUDE_FILES:
        p = repo_root / rel_file
        if p.exists() and p.is_file():
            rel = p.relative_to(repo_root).as_posix()
            if not is_excluded(rel) and is_probably_text(p):
                files.append(rel)

    return sorted(set(files))


def build_document(repo_root: Path, files: list[str]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    commit = git_commit_hash(repo_root)
    lines: list[str] = []

    lines.append("# Breeze Core Engine - Code Submission")
    lines.append("")
    lines.append("## Metadata")
    lines.append("")
    lines.append(f"- Generated at (UTC): {now}")
    lines.append(f"- Git commit: `{commit}`")
    lines.append("- Scope: first-party code only")
    lines.append("")
    lines.append("## Inclusion policy")
    lines.append("")
    lines.append("- Included: backend source/tests, frontend source/public text assets, deploy/workflow files, selected root authored files.")
    lines.append("- Excluded: third-party dependencies, generated/build outputs, runtime logs, `.env*`, sqlite databases, credentials, `legacy/`.")
    lines.append("")
    lines.append("## Table of contents")
    lines.append("")
    for rel in files:
        anchor = rel.replace("/", "").replace(".", "").replace("_", "").lower()
        lines.append(f"- [{rel}](#{anchor})")
    lines.append("")
    lines.append("---")
    lines.append("")

    for rel in files:
        abs_path = repo_root / rel
        try:
            content = abs_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
        lines.append(f"## {rel}")
        lines.append("")
        lines.append(f"```{fence_lang(rel)}")
        lines.append(content.rstrip("\n"))
        lines.append("```")
        lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate consolidated code submission markdown.")
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Path to repository root (default: current directory).",
    )
    parser.add_argument(
        "--output",
        default="docs/code-submission.md",
        help="Output markdown file path.",
    )
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    output_path = (repo_root / args.output).resolve()
    files = collect_files(repo_root)
    doc = build_document(repo_root, files)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(doc, encoding="utf-8")

    print(f"Generated {output_path} with {len(files)} files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
