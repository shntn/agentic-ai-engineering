"""
リポジトリツール

GitHubリポジトリのクローンと、インデックス化されたコードベースの管理を行うツール。
"""

import subprocess
from pathlib import Path
from typing import Any

from indexer_ja.chunker import chunk_repository, collect_files
from indexer_ja.embedder import Embedder, index_chunks
from store_ja.vector import VectorStore

from common.logging_config import setup_logging

logger = setup_logging(__name__)

# クローンされたリポジトリの保存先
REPOS_DIR = Path(__file__).parent.parent / "repos"

REPO_TOOLS = [
    {
        "name": "clone_and_index",
        "description": (
            "GitHubリポジトリをクローンし、セマンティック検索用にインデックス化します。"
            "'pallets/flask' のようなGitHubリポジトリ、またはローカルパスを指定してください。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "GitHubリポジトリ（owner/repo）またはローカルパス",
                },
            },
            "required": ["repo"],
        },
    },
    {
        "name": "list_repos",
        "description": "インデックス化済みのすべてのリポジトリを、チャンク数とともに一覧表示します。",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
]


def _normalize_collection_name(repo: str) -> str:
    """リポジトリ識別子を有効なChromaDBコレクション名に変換する。"""
    # ChromaDBの制約: 3〜63文字、先頭と末尾は英数字、使えるのは英数字/アンダースコア/ハイフンのみ
    name = repo.replace("/", "-").replace(".", "-").replace(" ", "-")
    # 先頭が英数字になるようにする
    if name and not name[0].isalnum():
        name = "r-" + name
    # 63文字に切り詰める
    return name[:63]


def _resolve_repo_path(repo: str) -> tuple[Path, str, bool]:
    """リポジトリをローカルパスに解決する。(path, collection_name, needs_clone) を返す。"""
    local_path = Path(repo).expanduser()
    if local_path.is_dir():
        name = "local-" + local_path.name
        return local_path, _normalize_collection_name(name), False

    # GitHubリポジトリとして扱う
    name = _normalize_collection_name(repo)
    clone_dir = REPOS_DIR / name
    return clone_dir, name, not clone_dir.exists()


def execute_clone_and_index(
    vector_store: VectorStore, embedder: Embedder, tool_input: dict[str, Any]
) -> str:
    """リポジトリをクローンし、セマンティック検索用にインデックス化する。"""
    repo = tool_input["repo"]
    repo_path, collection_name, needs_clone = _resolve_repo_path(repo)

    # すでにインデックス化済みか確認する
    if vector_store.collection_exists(collection_name):
        collections = vector_store.list_collections()
        for c in collections:
            if c["name"] == collection_name:
                return f"リポジトリ '{repo}' はすでにインデックス化済みです（{c['chunks']}チャンク）。検索できます！"

    # 必要ならクローンする
    if needs_clone:
        REPOS_DIR.mkdir(parents=True, exist_ok=True)
        url = f"https://github.com/{repo}.git"
        logger.info("Cloning %s to %s", url, repo_path)
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", url, str(repo_path)],
                capture_output=True,
                text=True,
                timeout=120,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            return f"Failed to clone '{repo}': {e.stderr.strip()}"
        except subprocess.TimeoutExpired:
            return f"Cloning '{repo}' timed out."

    # ファイル数を数え、チャンク化する
    files = collect_files(repo_path)
    chunks = chunk_repository(repo_path, collection_name)

    if not chunks:
        return f"'{repo}' にインデックス化可能なファイルが見つかりませんでした。"

    # 埋め込みを行い保存する
    count = index_chunks(embedder, vector_store, collection_name, chunks)

    return (
        f"'{repo}' をインデックス化しました: {len(files)}ファイル、{count}チャンク。"
        f"検索できます！コードベースについて質問してみてください。"
    )


def execute_list_repos(vector_store: VectorStore, _tool_input: dict[str, Any]) -> str:
    """インデックス化済みのすべてのリポジトリを一覧表示する。"""
    collections = vector_store.list_collections()
    if not collections:
        return "まだインデックス化されたリポジトリはありません。clone_and_index を使って追加してください。"

    lines = ["インデックス化済みのリポジトリ:"]
    for c in collections:
        lines.append(f"  - {c['name']}: {c['chunks']} chunks")
    return "\n".join(lines)
