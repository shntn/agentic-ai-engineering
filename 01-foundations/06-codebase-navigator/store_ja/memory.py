"""
メモリストア

セッションをまたいで事実・洞察・好みを保存する、永続的なJSONベースのメモリ。
これは拡張LLM（Augmented LLM）パターンにおける「メモリ」拡張である。
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common.logging_config import setup_logging

logger = setup_logging(__name__)

# デフォルトのメモリファイルの場所
DEFAULT_MEMORY_PATH = Path(__file__).parent.parent / "memory.json"


class MemoryStore:
    """JSONファイルを裏付けとする永続的なメモリストア。"""

    def __init__(self, path: Path = DEFAULT_MEMORY_PATH) -> None:
        self.path = path
        self.data: dict[str, list[dict[str, Any]]] = {
            "facts": [],
            "insights": [],
            "preferences": [],
        }
        self._load()

    def _load(self) -> None:
        """ディスクからメモリを読み込む。"""
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
                total = sum(len(v) for v in self.data.values())
                logger.info("Loaded %d memories from %s", total, self.path)
            except (json.JSONDecodeError, KeyError) as e:
                logger.error("Failed to load memory file: %s", e)
                self.data = {"facts": [], "insights": [], "preferences": []}

    def _save(self) -> None:
        """メモリをディスクに永続化する。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")

    def save(self, category: str, content: str, repos: list[str] | None = None) -> str:
        """メモリエントリを保存する。"""
        if category not in self.data:
            return f"Invalid category: {category}. Use: fact, insight, preference"

        entry: dict[str, Any] = {
            "content": content,
            "created": datetime.now(timezone.utc).isoformat(),
        }
        if repos:
            entry["repos"] = repos

        self.data[category].append(entry)
        self._save()
        logger.info("Saved %s: %s", category, content[:80])
        return f"保存しました（{category}）: {content}"

    def recall(self, query: str | None = None) -> dict[str, list[dict[str, Any]]]:
        """メモリを呼び出す（キーワードで絞り込み可能）。"""
        if not query:
            return self.data

        query_lower = query.lower()
        filtered: dict[str, list[dict[str, Any]]] = {}
        for category, entries in self.data.items():
            matches = [e for e in entries if query_lower in e["content"].lower()]
            if matches:
                filtered[category] = matches
        return filtered

    def summary(self) -> str:
        """システムプロンプトに含めるための簡潔な要約を返す。"""
        parts = []
        for category, entries in self.data.items():
            if entries:
                parts.append(
                    f"{category} ({len(entries)}): " + "; ".join(e["content"] for e in entries[-3:])
                )
        return "\n".join(parts) if parts else "まだメモリは保存されていません。"
