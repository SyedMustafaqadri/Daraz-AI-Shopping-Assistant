#!/usr/bin/env python3
"""Generate a markdown snapshot of the repository tree and text file contents.

This script is intended for AI agents and humans who need a single, readable
copy of the codebase structure plus the contents of each text file.

`uv run python scripts/generate_codebase_markdown.py --folder src/daraz_ai_shopping_assistant --output AGENT_CODEBASE_CONTEXT.md`
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from pathlib import Path

DEFAULT_IGNORED_DIRECTORIES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "__init__.py",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".idea",
    ".vscode",
    "node_modules",
    "dist",
    "build",
    "site-packages",
    ".agents",
    "AGENT_CODEBASE_CONTEXT.md",
}

BINARY_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".ico",
    ".webp",
    ".pdf",
    ".zip",
    ".gz",
    ".tar",
    ".rar",
    ".7z",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".mp3",
    ".mp4",
    ".mov",
    ".avi",
    ".svg",
}


def is_ignored_directory(path: Path) -> bool:
    return path.name in DEFAULT_IGNORED_DIRECTORIES


def is_binary_file(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in BINARY_EXTENSIONS:
        return True

    try:
        with path.open("rb") as handle:
            chunk = handle.read(4096)
    except OSError:
        return True

    return b"\0" in chunk


def collect_files(root: Path, output_path: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix().lower()):
        if path.is_dir():
            if is_ignored_directory(path):
                for child in path.rglob("*"):
                    if child.is_dir() and child.name in DEFAULT_IGNORED_DIRECTORIES:
                        continue
                continue
            continue

        if not path.is_file():
            continue

        if path == output_path:
            continue

        if path.name.startswith(".") and path.name not in {".gitignore", ".env.example", ".python-version"}:
            continue

        if is_binary_file(path):
            continue

        files.append(path)
    return files


def render_tree(root: Path, files: Iterable[Path]) -> str:
    rel_paths = [path.relative_to(root).as_posix() for path in files]
    lines = ["# Repository Structure", "", "```text", "."]

    if not rel_paths:
        lines.append("(no files found)")
        lines.append("```")
        return "\n".join(lines) + "\n"

    # Build tree using simple indentation. This lists the file paths and directories
    # without duplicating empty folder nodes, keeping the output readable.
    dirs: set[str] = set()
    for rel_path in rel_paths:
        parts = rel_path.split("/")
        for idx in range(1, len(parts)):
            dirs.add("/".join(parts[:idx]))

    tree_paths = sorted(set(rel_paths) | dirs)
    printed: set[str] = set()

    for rel_path in tree_paths:
        parts = rel_path.split("/")
        depth = len(parts)
        label = parts[-1]
        prefix = "    " * (depth - 1)
        connector = "├── " if rel_path != tree_paths[-1] else "└── "

        if rel_path in rel_paths:
            lines.append(f"{prefix}{connector}{label}")
            printed.add(rel_path)
        else:
            if rel_path not in printed:
                lines.append(f"{prefix}{'├── ' if rel_path != tree_paths[-1] else '└── '}{label}/")
                printed.add(rel_path)

    lines.append("```")
    return "\n".join(lines) + "\n"


def read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def render_file_contents(root: Path, files: Iterable[Path]) -> str:
    sections: list[str] = ["# File Contents", ""]

    for path in files:
        rel_path = path.relative_to(root).as_posix()
        sections.append(f"## {rel_path}")
        sections.append("")
        sections.append(f"```{path.suffix.lstrip('.').lower() or 'text'}")
        sections.append(read_text_file(path))
        if not read_text_file(path).endswith("\n"):
            sections.append("")
        sections.append("```")
        sections.append("")

    return "\n".join(sections) + "\n"


def build_markdown(root: Path, output_path: Path, scope: str | None = None) -> str:
    files = collect_files(root, output_path)
    structure = render_tree(root, files)
    contents = render_file_contents(root, files)
    generated_at = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")

    scope_note = f"Scope: {scope}\n\n" if scope else ""
    return (
        "# Codebase Snapshot\n\n"
        f"Generated: {generated_at}\n\n"
        f"{scope_note}"
        "This file contains the repository structure and the contents of every readable text file.\n\n"
        f"{structure}\n\n{contents}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a markdown file containing the repository tree and file contents."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root to scan. Defaults to the parent of this script.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "AGENT_CODEBASE_CONTEXT.md",
        help="Markdown file to write. Defaults to AGENT_CODEBASE_CONTEXT.md in the repo root.",
    )
    parser.add_argument(
        "--folder",
        type=str,
        default=None,
        help="Optional subfolder relative to --root to include in the markdown snapshot. Example: src/daraz_ai_shopping_assistant",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.root.resolve()
    output = args.output.resolve()

    target_root = root
    scope = None
    if args.folder:
        target_root = (root / args.folder).resolve()
        if not target_root.exists() or not target_root.is_dir():
            raise SystemExit(f"Folder does not exist: {target_root}")
        scope = args.folder.replace("\\", "/")

    markdown = build_markdown(target_root, output, scope=scope)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown, encoding="utf-8")
    print(f"Codebase snapshot generated: {output}")
    if scope:
        print(f"Scope: {scope}")


if __name__ == "__main__":
    main()
