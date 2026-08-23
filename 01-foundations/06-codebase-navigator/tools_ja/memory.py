"""
メモリツール

セッションをまたいで永続的なメモリを保存・呼び出すためのツール。
"""

from typing import Any

from store_ja.memory import MemoryStore

from common.logging_config import setup_logging

logger = setup_logging(__name__)

# Anthropic API向けのツール定義
MEMORY_TOOLS = [
    {
        "name": "save_memory",
        "description": (
            "今後のセッションのために、永続的なメモリに情報を保存します。"
            "アーキテクチャ上の洞察、ユーザーの好み、重要な事実を記憶するために使用してください。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["facts", "insights", "preferences"],
                    "description": "カテゴリ: facts（事実）、insights（洞察）、preferences（好み）のいずれか",
                },
                "content": {
                    "type": "string",
                    "description": "保存する情報",
                },
            },
            "required": ["category", "content"],
        },
    },
    {
        "name": "recall_memory",
        "description": "保存済みのメモリを取得します。キーワードクエリで絞り込むこともできます。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "メモリを絞り込むためのキーワード（任意）",
                },
            },
        },
    },
]


def execute_save_memory(memory: MemoryStore, tool_input: dict[str, Any]) -> str:
    """save_memory ツールを実行する。"""
    return memory.save(
        category=tool_input["category"],
        content=tool_input["content"],
    )


def execute_recall_memory(memory: MemoryStore, tool_input: dict[str, Any]) -> str:
    """recall_memory ツールを実行する。"""
    query = tool_input.get("query")
    memories = memory.recall(query)
    if not memories:
        return "メモリが見つかりませんでした。" + (f"（絞り込み: '{query}'）" if query else "")

    parts = []
    for category, entries in memories.items():
        parts.append(f"\n## {category.title()}")
        for entry in entries:
            parts.append(f"- {entry['content']} ({entry['created'][:10]})")
    return "\n".join(parts)
