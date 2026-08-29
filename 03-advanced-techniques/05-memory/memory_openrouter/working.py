"""Working memory — セッション内バッファ、重要度ベースの追い出しを行う。"""

from common.logging_config import setup_logging

from .models import MemoryEntry, MemoryType

logger = setup_logging(__name__)


class WorkingMemory:
    """満杯になると最も重要度の低いエントリを追い出す、セッション単位のバッファ。"""

    def __init__(self, max_items: int = 50) -> None:
        self.max_items = max_items
        self._entries: list[MemoryEntry] = []

    def add(
        self,
        content: str,
        importance: float = 0.5,
        metadata: dict | None = None,
    ) -> MemoryEntry:
        """メモリを追加する。容量に達している場合は最も重要度の低いエントリを追い出す。"""
        entry = MemoryEntry(
            content=content,
            memory_type=MemoryType.WORKING,
            importance=importance,
            metadata=metadata or {},
        )

        if len(self._entries) >= self.max_items:
            # 最も重要度の低いものを追い出す
            self._entries.sort(key=lambda e: e.importance)
            evicted = self._entries.pop(0)
            logger.info("Evicted working memory: %s", evicted.content[:60])

        self._entries.append(entry)
        return entry

    def get_recent(self, n: int = 10) -> list[MemoryEntry]:
        """直近N件のエントリを返す。"""
        return sorted(self._entries, key=lambda e: e.timestamp, reverse=True)[:n]

    def get_important(self, threshold: float = 0.7) -> list[MemoryEntry]:
        """重要度が閾値以上のエントリを返す。"""
        return [e for e in self._entries if e.importance >= threshold]

    def get_all(self) -> list[MemoryEntry]:
        """タイムスタンプ順に並べた全エントリを返す。"""
        return sorted(self._entries, key=lambda e: e.timestamp)

    def clear(self) -> None:
        """すべてのworking memoryをクリアする。"""
        count = len(self._entries)
        self._entries.clear()
        logger.info("Cleared %d working memory entries", count)

    def stats(self) -> dict:
        """working memoryの統計情報を返す。"""
        return {
            "count": len(self._entries),
            "max_items": self.max_items,
            "avg_importance": (
                sum(e.importance for e in self._entries) / len(self._entries)
                if self._entries
                else 0.0
            ),
        }
