"""MemoryManager — 3階層すべてのメモリを統括する（OpenRouter対応版）。"""

import json

from common.logging_config import setup_logging
from memory.episodic import EpisodicMemory
from memory.models import MemoryEntry, MemoryType
from memory.semantic import SemanticMemory
from memory.working import WorkingMemory
from openrouter import OpenRouter
from openrouter.components import ChatResult
from openrouter.errors import OpenRouterError

logger = setup_logging(__name__)

# 会話から重要な情報を抽出するためのプロンプト
CONSOLIDATION_PROMPT = """\
この会話を分析し、長期的に記憶する価値がある重要な情報を抽出してください。
以下のフィールドを持つオブジェクトのJSON配列を返してください:
- "content": 記憶すべき事実または出来事（簡潔な1文）
- "importance": 0.0〜1.0の浮動小数点数（記憶する重要度）
- "type": "episodic"（出来事・やり取り・起こったこと）または"semantic"（事実・好み・知識）
  のいずれか

本当に重要な情報だけを抽出してください。保存する価値がなければ空配列 [] を返して
ください。

会話:
{conversation}

JSON配列のみを返してください。それ以外のテキストは不要です。"""


class MemoryManager:
    """working・episodic・semanticの3階層のメモリを統括する。"""

    def __init__(self) -> None:
        self.working = WorkingMemory()
        self.episodic = EpisodicMemory()
        self.semantic = SemanticMemory()

    def remember(
        self,
        content: str,
        memory_type: str = "working",
        importance: float = 0.5,
        metadata: dict | None = None,
    ) -> str:
        """指定した階層にメモリを保存する。"""
        entry = MemoryEntry(
            content=content,
            memory_type=MemoryType(memory_type),
            importance=importance,
            metadata=metadata or {},
        )

        if memory_type == "working":
            self.working.add(content, importance, metadata)
        elif memory_type == "episodic":
            self.episodic.save(entry)
        elif memory_type == "semantic":
            self.semantic.save(entry)
        else:
            return f"Unknown memory type: {memory_type}"

        return f"Remembered in {memory_type}: {content}"

    def recall(self, query: str, limit: int = 5) -> str:
        """階層横断検索——episodicのキーワード検索とsemanticの類似度検索の結果を統合する。"""
        results: list[tuple[str, str, float]] = []  # (source, content, score)

        # episodicのキーワード検索
        episodic_matches = self.episodic.search(query, limit=limit)
        for entry in episodic_matches:
            results.append(("episodic", entry.content, entry.importance))

        # semanticの類似度検索
        semantic_matches = self.semantic.search(query, limit=limit)
        for entry, similarity in semantic_matches:
            # 類似度 × 重要度でランク付け
            score = similarity * entry.importance
            results.append(("semantic", entry.content, score))

        # スコアの降順でソート
        results.sort(key=lambda x: x[2], reverse=True)
        results = results[:limit]

        if not results:
            return "No relevant memories found."

        lines = []
        for source, content, score in results:
            lines.append(f"[{source}] (score: {score:.2f}) {content}")
        return "\n".join(lines)

    def forget(self, memory_id: str, memory_type: str) -> str:
        """指定した階層から特定のメモリを削除する。"""
        if memory_type == "episodic":
            success = self.episodic.delete(memory_id)
        elif memory_type == "semantic":
            success = self.semantic.delete(memory_id)
        elif memory_type == "working":
            return "Working memory clears automatically at session end."
        else:
            return f"Unknown memory type: {memory_type}"

        return f"{'Deleted' if success else 'Not found'}: {memory_id} from {memory_type}"

    def build_memory_context(self) -> str:
        """システムプロンプトに注入するメモリコンテキスト文字列を構築する。"""
        sections: list[str] = []

        # 直近のepisodicメモリ
        recent = self.episodic.get_recent(5)
        if recent:
            episodic_lines = [f"- {e.content}" for e in recent]
            sections.append("## Recent Events\n" + "\n".join(episodic_lines))

        # 上位のsemanticメモリ（関連性の高い一般知識）
        semantic_all = self.semantic.list_all()
        if semantic_all:
            # 重要度でソートし、上位のみ取得
            top = sorted(semantic_all, key=lambda e: e.importance, reverse=True)[:5]
            semantic_lines = [f"- {e.content}" for e in top]
            sections.append("## Known Facts\n" + "\n".join(semantic_lines))

        if not sections:
            return ""

        return "# Recalled Memories\n\n" + "\n\n".join(sections)

    def consolidate(
        self,
        conversation_messages: list[dict],
        client: OpenRouter,
        model: str,
    ) -> list[str]:
        """LLMを使い、会話から重要な項目を抽出して永続メモリに保存する。"""
        # メッセージから会話テキストを組み立てる
        # OpenAI/OpenRouter形式ではcontentは常にstr/Noneのフラットな値のため、
        # Anthropic版のような content ブロックのリスト判定は不要
        parts: list[str] = []
        for msg in conversation_messages:
            role = msg["role"]
            if role == "tool":
                # ツール結果はノイズになりやすいため要約対象から除外する
                continue
            content = msg.get("content")
            if content:
                label = "User" if role == "user" else "Assistant"
                parts.append(f"{label}: {content}")

        conversation_text = "\n".join(parts)
        if not conversation_text.strip():
            return []

        prompt = CONSOLIDATION_PROMPT.format(conversation=conversation_text)

        try:
            response: ChatResult = client.chat.send(  # type: ignore[call-overload]
                model=model,
                max_tokens=1024,
                reasoning={"effort": "none"},
                messages=[{"role": "user", "content": prompt}],
            )
            raw = str(response.choices[0].message.content or "").strip()

            # markdownのコードフェンスが付いていれば取り除く
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                if raw.endswith("```"):
                    raw = raw[:-3].strip()

            # 応答からJSON配列をパースする
            items = json.loads(raw)
            if not isinstance(items, list):
                return []

        except (json.JSONDecodeError, OpenRouterError) as e:
            logger.error("Consolidation failed: %s", e)
            return []

        saved: list[str] = []
        for item in items:
            content = item.get("content", "")
            importance = float(item.get("importance", 0.5))
            mem_type = item.get("type", "episodic")

            if mem_type not in ("episodic", "semantic"):
                mem_type = "episodic"

            self.remember(content, memory_type=mem_type, importance=importance)
            saved.append(f"[{mem_type}] {content}")

        logger.info("Consolidated %d memories from conversation", len(saved))
        return saved

    def get_stats(self) -> dict:
        """全メモリ階層の統計を集計する。"""
        return {
            "working": self.working.stats(),
            "episodic": self.episodic.stats(),
            "semantic": self.semantic.stats(),
        }
