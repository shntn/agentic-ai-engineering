"""
決定的なエージェント評価のためのコードベースのグレーダー。

キーワードマッチング・正規表現パターンマッチング・出典引用の検証・
ツール呼び出しのチェックを提供する。各グレーダーはpass/fail・スコア（0-1）・
人間が読める理由を含むGraderResultを返す。
"""

import re
from dataclasses import dataclass
from typing import Any

from common import setup_logging

logger = setup_logging(__name__)


@dataclass
class GraderResult:
    """グレーダー評価の結果。"""

    passed: bool
    score: float  # 0.0〜1.0
    reason: str


class KeywordGrader:
    """回答に含まれるべきキーワードに基づいて採点する。"""

    def grade(self, answer: str, expected_keywords: list[str]) -> GraderResult:
        """回答が期待されるキーワードを含んでいるかを確認する。"""
        answer_lower = answer.lower()
        found = [kw for kw in expected_keywords if kw.lower() in answer_lower]
        missing = [kw for kw in expected_keywords if kw.lower() not in answer_lower]

        score = len(found) / len(expected_keywords) if expected_keywords else 1.0
        passed = score >= 0.5

        reason = f"Found {len(found)}/{len(expected_keywords)} keywords"
        if missing:
            reason += f" (missing: {', '.join(missing)})"

        logger.debug("KeywordGrader: score=%.2f, found=%s", score, found)
        return GraderResult(passed=passed, score=score, reason=reason)


class RegexGrader:
    """正規表現パターンマッチングに基づいて採点する。"""

    def grade(self, answer: str, pattern: str) -> GraderResult:
        """回答が正規表現パターンにマッチするかを確認する。"""
        match = re.search(pattern, answer, re.IGNORECASE)
        passed = match is not None
        score = 1.0 if passed else 0.0
        reason = f"Pattern '{pattern}' {'matched' if passed else 'not found'}"

        logger.debug("RegexGrader: pattern=%s, passed=%s", pattern, passed)
        return GraderResult(passed=passed, score=score, reason=reason)


class SourceCitationGrader:
    """回答が出典を引用しているかを採点する。"""

    def grade(self, answer: str, expected_source_ids: list[str]) -> GraderResult:
        """期待されるドキュメントIDが回答内で引用されているかを確認する。"""
        if not expected_source_ids:
            # 対応範囲外のタスク: エージェントが情報を持っていないと述べているか確認する
            has_refusal = bool(
                re.search(
                    r"関連する情報|見つかりません|情報がありません|持っていません",
                    answer,
                )
            )
            return GraderResult(
                passed=has_refusal,
                score=1.0 if has_refusal else 0.0,
                reason="Out-of-scope: " + ("correctly refused" if has_refusal else "should refuse"),
            )

        cited = [sid for sid in expected_source_ids if sid in answer]
        missing = [sid for sid in expected_source_ids if sid not in answer]

        score = len(cited) / len(expected_source_ids)
        passed = score >= 0.5
        reason = f"Cited {len(cited)}/{len(expected_source_ids)} sources"
        if missing:
            reason += f" (missing: {', '.join(missing)})"

        logger.debug("SourceCitationGrader: score=%.2f, cited=%s", score, cited)
        return GraderResult(passed=passed, score=score, reason=reason)


class ToolCallGrader:
    """エージェントが期待されるツール呼び出しを行ったかを採点する。"""

    def grade(
        self, tool_calls: list[dict[str, Any]], expected_tool: str = "search_knowledge_base"
    ) -> GraderResult:
        """エージェントが期待されるツールを少なくとも1回呼び出したことを確認する。"""
        tool_names = [tc.get("name", "") for tc in tool_calls]
        called = expected_tool in tool_names
        score = 1.0 if called else 0.0

        reason = (
            f"Tool '{expected_tool}' was called ({len(tool_calls)} total calls)"
            if called
            else f"Tool '{expected_tool}' was NOT called"
        )

        logger.debug("ToolCallGrader: tool=%s, called=%s", expected_tool, called)
        return GraderResult(passed=called, score=score, reason=reason)
