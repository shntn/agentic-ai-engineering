"""
ファイルツール

インデックス化されたリポジトリのファイルを読み込み、ディレクトリ構造を探索するためのツール。
"""

from pathlib import Path
from typing import Any

from common.logging_config import setup_logging

logger = setup_logging(__name__)

# クローンされたリポジトリの保存先
REPOS_DIR = Path(__file__).parent.parent / "repos"

FILE_TOOLS = [
    {
        "name": "read_file",
        "description": "インデックス化されたリポジトリから、行番号付きでファイルの全内容を読み込みます。",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "リポジトリ名（例: 'pallets-flask'）",
                },
                "filepath": {
                    "type": "string",
                    "description": "リポジトリ内のファイルパス（例: 'src/flask/app.py'）",
                },
            },
            "required": ["repo", "filepath"],
        },
    },
    {
        "name": "list_directory",
        "description": "インデックス化されたリポジトリ内のパスにあるファイルとディレクトリを一覧表示します。",
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "リポジトリ名（例: 'pallets-flask'）",
                },
                "path": {
                    "type": "string",
                    "description": "リポジトリ内のパス（デフォルトはルート）",
                    "default": "",
                },
            },
            "required": ["repo"],
        },
    },
]


def _find_repo_path(repo: str) -> Path | None:
    """リポジトリのローカルパスを探す。"""
    # repos ディレクトリを確認する
    repo_path = REPOS_DIR / repo
    if repo_path.is_dir():
        return repo_path

    # local- プレフィックス付きのコレクションかどうかを確認する
    if not repo.startswith("local-"):
        repo_path = REPOS_DIR / f"local-{repo}"
        if repo_path.is_dir():
            return repo_path

    return None


def execute_read_file(_vector_store: Any, tool_input: dict[str, Any]) -> str:
    """インデックス化されたリポジトリから、行番号付きでファイルを読み込む。"""
    repo = tool_input["repo"]
    filepath = tool_input["filepath"]

    repo_path = _find_repo_path(repo)
    if not repo_path:
        return f"リポジトリ '{repo}' がローカルに見つかりません。"

    file_path = repo_path / filepath
    if not file_path.is_file():
        return f"ファイルが見つかりません: {repo} 内の {filepath}"

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return f"Error reading file: {e}"

    # 行番号を追加し、コンテキストの肥大化を避けるため大きなファイルは切り詰める
    lines = content.split("\n")
    max_lines = 300
    truncated = len(lines) > max_lines
    display_lines = lines[:max_lines]
    numbered = [f"{i + 1:4d} | {line}" for i, line in enumerate(display_lines)]
    result = f"ファイル: {filepath}（{len(lines)}行）\n\n" + "\n".join(numbered)
    if truncated:
        result += f"\n\n... 省略されました（残り{len(lines) - max_lines}行）"
    return result


def execute_list_directory(_vector_store: Any, tool_input: dict[str, Any]) -> str:
    """インデックス化されたリポジトリのディレクトリ内容を一覧表示する。"""
    repo = tool_input["repo"]
    subpath = tool_input.get("path", "")

    repo_path = _find_repo_path(repo)
    if not repo_path:
        return f"リポジトリ '{repo}' がローカルに見つかりません。"

    target = repo_path / subpath
    if not target.is_dir():
        return f"ディレクトリが見つかりません: {repo} 内の {subpath or '/'}"

    entries = sorted(target.iterdir())
    lines = [f"ディレクトリ: {repo} 内の {subpath or '/'}\n"]

    for entry in entries:
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            lines.append(f"  📁 {entry.name}/")
        else:
            size = entry.stat().st_size
            if size < 1024:
                size_str = f"{size} B"
            elif size < 1024 * 1024:
                size_str = f"{size / 1024:.1f} KB"
            else:
                size_str = f"{size / (1024 * 1024):.1f} MB"
            lines.append(f"  📄 {entry.name} ({size_str})")

    return "\n".join(lines)
