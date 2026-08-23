"""
コードチャンカー

ソースコードファイルを、埋め込みとセマンティック検索に適したチャンクに分割する。
シンプルなヒューリスティックを使用: Pythonファイルはclass/function定義で分割し、
それ以外のファイルはオーバーラップを持たせた行数で分割する。
"""

from pathlib import Path
from typing import Any

from common.logging_config import setup_logging

logger = setup_logging(__name__)

# インデックス化対象のファイル拡張子
INDEXABLE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".go",
    ".rs",
    ".java",
    ".rb",
    ".php",
    ".c",
    ".cpp",
    ".h",
    ".md",
    ".txt",
    ".yaml",
    ".yml",
    ".json",
}

# スキップするディレクトリ
SKIP_DIRS = {
    "node_modules",
    "venv",
    ".venv",
    ".git",
    "__pycache__",
    "dist",
    "build",
    ".next",
    "vendor",
    ".tox",
    ".mypy_cache",
    ".ruff_cache",
    "egg-info",
}

# Python以外のファイルにおけるチャンクあたりの最大行数
CHUNK_SIZE = 50
OVERLAP = 10


def collect_files(repo_path: Path) -> list[Path]:
    """リポジトリからインデックス化可能なすべてのファイルを収集する。"""
    files = []
    for path in repo_path.rglob("*"):
        if any(skip in path.parts for skip in SKIP_DIRS):
            continue
        if path.is_file() and path.suffix in INDEXABLE_EXTENSIONS:
            files.append(path)
    return sorted(files)


def chunk_python(content: str, filepath: str, repo: str) -> list[dict[str, Any]]:
    """トップレベルのclass/function定義で分割してPythonファイルをチャンク化する。"""
    lines = content.split("\n")
    chunks: list[dict[str, Any]] = []
    current_chunk_start = 0

    for i, line in enumerate(lines):
        # トップレベルの定義で分割する（先頭に空白がない行）
        if i > 0 and (line.startswith("class ") or line.startswith("def ")):
            chunk_content = "\n".join(lines[current_chunk_start:i]).strip()
            if chunk_content:
                chunks.append(
                    {
                        "content": chunk_content,
                        "filepath": filepath,
                        "start_line": current_chunk_start + 1,
                        "end_line": i,
                        "repo": repo,
                    }
                )
            current_chunk_start = i

    # 最後のチャンクを忘れずに追加する
    chunk_content = "\n".join(lines[current_chunk_start:]).strip()
    if chunk_content:
        chunks.append(
            {
                "content": chunk_content,
                "filepath": filepath,
                "start_line": current_chunk_start + 1,
                "end_line": len(lines),
                "repo": repo,
            }
        )

    return chunks


def chunk_generic(content: str, filepath: str, repo: str) -> list[dict[str, Any]]:
    """オーバーラップを持たせた固定行数でPython以外のファイルをチャンク化する。"""
    lines = content.split("\n")
    chunks: list[dict[str, Any]] = []

    i = 0
    while i < len(lines):
        end = min(i + CHUNK_SIZE, len(lines))
        chunk_content = "\n".join(lines[i:end]).strip()
        if chunk_content:
            chunks.append(
                {
                    "content": chunk_content,
                    "filepath": filepath,
                    "start_line": i + 1,
                    "end_line": end,
                    "repo": repo,
                }
            )
        i += CHUNK_SIZE - OVERLAP

    return chunks


def chunk_file(path: Path, repo_path: Path, repo_name: str) -> list[dict[str, Any]]:
    """適切な戦略を使って単一のファイルをチャンク化する。"""
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        logger.warning("Could not read %s: %s", path, e)
        return []

    if not content.strip():
        return []

    # 非常に大きなファイルは制限する
    if len(content) > 100_000:
        content = content[:100_000]

    filepath = str(path.relative_to(repo_path))

    if path.suffix == ".py":
        return chunk_python(content, filepath, repo_name)
    return chunk_generic(content, filepath, repo_name)


def chunk_repository(repo_path: Path, repo_name: str) -> list[dict[str, Any]]:
    """リポジトリ内のすべてのファイルをチャンク化する。"""
    files = collect_files(repo_path)
    logger.info("Found %d indexable files in %s", len(files), repo_path)

    all_chunks: list[dict[str, Any]] = []
    for path in files:
        all_chunks.extend(chunk_file(path, repo_path, repo_name))

    logger.info("Created %d chunks from %d files", len(all_chunks), len(files))
    return all_chunks
