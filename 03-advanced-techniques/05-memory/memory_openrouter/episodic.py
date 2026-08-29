"""Episodic memory — JSONファイルに永続化される、タイムスタンプ付きのイベント。"""

import json
from pathlib import Path

from common.logging_config import setup_logging

from openrouter_adapter import tokenize_japanese

from .models import MemoryEntry, MemoryType

logger = setup_logging(__name__)


class EpisodicMemory:
    """JSONファイルを裏側に持つ長期的なイベントメモリ。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path("data/episodic.json")
        self._entries: list[MemoryEntry] = []
        self._load()

    def _load(self) -> None:
        """ディスクからメモリを読み込む。"""
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self._entries = [MemoryEntry.from_dict(d) for d in raw]
                logger.info("Loaded %d episodic memories from %s", len(self._entries), self.path)
            except (json.JSONDecodeError, KeyError) as e:
                logger.error("Failed to load episodic memory: %s", e)
                self._entries = []
        else:
            logger.info("No existing episodic memory at %s", self.path)

    def _save(self) -> None:
        """メモリをディスクに永続化する。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [e.to_dict() for e in self._entries]
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def save(self, entry: MemoryEntry) -> None:
        """メモリエントリをepisodicストアに保存する。"""
        entry.memory_type = MemoryType.EPISODIC
        self._entries.append(entry)
        self._save()
        logger.info("Saved episodic memory: %s", entry.content[:60])

    def search(self, query: str, limit: int = 5) -> list[MemoryEntry]:
        """キーワードマッチングでメモリを検索する。

        `content_lower.split()`（スペース区切り）は単語間にスペースを入れない
        日本語ではほぼ機能しないため、janomeによる分かち書き（`tokenize_japanese`）
        を使う。
        """
        query_words = [w.lower() for w in tokenize_japanese(query)]

        scored: list[tuple[MemoryEntry, int]] = []
        for entry in self._entries:
            content_lower = entry.content.lower()
            # クエリの単語がいくつ含まれるかでスコアリングする
            score = sum(1 for word in query_words if word in content_lower)
            if score > 0:
                scored.append((entry, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [entry for entry, _ in scored[:limit]]

    def get_recent(self, n: int = 10) -> list[MemoryEntry]:
        """直近N件のepisodicメモリを返す。"""
        return sorted(self._entries, key=lambda e: e.timestamp, reverse=True)[:n]

    def delete(self, memory_id: str) -> bool:
        """IDを指定してメモリを削除する。"""
        for i, entry in enumerate(self._entries):
            if entry.id == memory_id:
                self._entries.pop(i)
                self._save()
                logger.info("Deleted episodic memory: %s", memory_id)
                return True
        return False

    def list_all(self) -> list[MemoryEntry]:
        """タイムスタンプ順に並べた全episodicメモリを返す。"""
        return sorted(self._entries, key=lambda e: e.timestamp)

    def clear(self) -> None:
        """すべてのepisodicメモリをクリアする。"""
        count = len(self._entries)
        self._entries.clear()
        self._save()
        logger.info("Cleared %d episodic memories", count)

    def stats(self) -> dict:
        """episodicメモリの統計情報を返す。"""
        return {
            "count": len(self._entries),
            "file": str(self.path),
            "oldest": self._entries[0].timestamp.isoformat() if self._entries else None,
            "newest": self._entries[-1].timestamp.isoformat() if self._entries else None,
        }
