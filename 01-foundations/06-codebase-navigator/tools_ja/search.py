"""
検索ツール

インデックス化されたコードベース全体に対するセマンティック検索と正規表現grep。
"""

import re
from pathlib import Path
from typing import Any

from indexer_ja.chunker import INDEXABLE_EXTENSIONS
from indexer_ja.embedder import Embedder
from store_ja.vector import VectorStore

from common.logging_config import setup_logging

logger = setup_logging(__name__)

# クローンされたリポジトリの保存先
REPOS_DIR = Path(__file__).parent.parent / "repos"

SEARCH_TOOLS = [
    {
        "name": "search_code",
        "description": (
            "インデックス化されたコードベース全体をセマンティック検索します。"
            "「ルーティングはどう動く？」「認証はどこで処理されている？」のような"
            "概念的な質問に使ってください。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "自然言語の検索クエリ",
                },
                "repo": {
                    "type": "string",
                    "description": "検索対象を特定のリポジトリに限定する（任意）",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "grep",
        "description": (
            "リポジトリのファイルを対象に正規表現パターンで完全一致検索します。"
            "特定の識別子やTODO、完全な文字列を探すのに使ってください。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "検索する正規表現パターン",
                },
                "repo": {
                    "type": "string",
                    "description": "検索対象を特定のリポジトリに限定する（任意）",
                },
            },
            "required": ["pattern"],
        },
    },
]


def execute_search_code(
    vector_store: VectorStore, embedder: Embedder, tool_input: dict[str, Any]
) -> str:
    """インデックス化されたコードベース全体をセマンティック検索する。"""
    query = tool_input["query"]
    repo = tool_input.get("repo")

    query_embedding = embedder.embed_query(query)
    results = vector_store.search(
        query_embedding=query_embedding,
        collection_name=repo,
        n_results=5,
    )

    if not results:
        return f"'{query}' に一致する結果は見つかりませんでした"

    parts = [f"'{query}' の検索結果\n"]
    for i, r in enumerate(results, 1):
        meta = r["metadata"]
        score = 1 - r["distance"]  # distanceをsimilarityに変換する
        parts.append(
            f"### 結果 {i}（関連度: {score:.2f}）\n"
            f"**{meta['filepath']}** {meta['start_line']}〜{meta['end_line']}行目 "
            f"[{r['collection']}]\n"
            f"```\n{r['content'][:500]}\n```\n"
        )

    return "\n".join(parts)


def execute_grep(vector_store: VectorStore, _embedder: Embedder, tool_input: dict[str, Any]) -> str:
    """リポジトリ内のファイルを正規表現で検索する。"""
    pattern = tool_input["pattern"]
    repo = tool_input.get("repo")

    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"Invalid regex pattern: {e}"

    # 検索対象のリポジトリを決定する
    if repo:
        search_dirs = [REPOS_DIR / repo]
    else:
        search_dirs = [d for d in REPOS_DIR.iterdir() if d.is_dir()] if REPOS_DIR.exists() else []

    if not search_dirs:
        return "利用可能なリポジトリがありません。まず clone_and_index を実行してください。"

    matches: list[str] = []
    context_lines = 2

    for repo_dir in search_dirs:
        if not repo_dir.exists():
            continue
        for filepath in repo_dir.rglob("*"):
            if not filepath.is_file() or filepath.suffix not in INDEXABLE_EXTENSIONS:
                continue

            try:
                lines = filepath.read_text(encoding="utf-8", errors="ignore").split("\n")
            except Exception:
                continue

            for i, line in enumerate(lines):
                if regex.search(line):
                    rel_path = filepath.relative_to(repo_dir)
                    start = max(0, i - context_lines)
                    end = min(len(lines), i + context_lines + 1)
                    context = "\n".join(
                        f"{'>' if j == i else ' '} {j + 1:4d} | {lines[j]}"
                        for j in range(start, end)
                    )
                    matches.append(f"**{rel_path}:{i + 1}**\n```\n{context}\n```")

                    if len(matches) >= 20:
                        break
            if len(matches) >= 20:
                break
        if len(matches) >= 20:
            break

    if not matches:
        return f"パターン `{pattern}` に一致する結果はありませんでした"

    header = f"パターン `{pattern}` に {len(matches)} 件一致しました"
    if len(matches) >= 20:
        header += "（最初の20件を表示）"
    return header + "\n\n" + "\n\n".join(matches)
