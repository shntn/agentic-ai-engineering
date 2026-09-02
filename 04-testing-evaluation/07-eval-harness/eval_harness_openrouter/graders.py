"""エージェント応答を評価するグレーダー実装（OpenRouter）。"""

import logging
import re

from eval_harness_openrouter.models import EvalTask, EvalTrial, GraderScore

logger = logging.getLogger(__name__)


class KeywordGrader:
    """期待されるキーワードが回答に含まれているかを採点する。"""

    def grade(self, answer: str, expected_keywords: list[str]) -> GraderScore:
        """回答に期待されるキーワードが含まれているかをチェックする。"""
        if not expected_keywords:
            return GraderScore(
                grader_name="keyword",
                passed=True,
                score=1.0,
                reason="期待されるキーワードなし",
            )

        answer_lower = answer.lower()
        found = [kw for kw in expected_keywords if kw.lower() in answer_lower]
        missing = [kw for kw in expected_keywords if kw.lower() not in answer_lower]
        score = len(found) / len(expected_keywords)
        passed = score >= 0.5

        reason = f"{len(found)}/{len(expected_keywords)} 件のキーワードが一致"
        if missing:
            reason += f"（不足: {', '.join(missing)}）"

        logger.debug("KeywordGrader: score=%.2f, found=%s", score, found)
        return GraderScore(grader_name="keyword", passed=passed, score=score, reason=reason)


class SourceCitationGrader:
    """回答が期待される出典を引用しているかを採点する。"""

    def grade(self, answer: str, expected_source_ids: list[str]) -> GraderScore:
        """期待されるドキュメントIDが回答内で引用されているかをチェックする。"""
        if not expected_source_ids:
            # スコープ外タスク: エージェントは関連情報がない旨を示すべき
            has_refusal = bool(
                re.search(
                    r"見つかりませんでした|含まれていません|情報がありません|情報は見つかりません",
                    answer,
                )
            )
            return GraderScore(
                grader_name="citation",
                passed=has_refusal,
                score=1.0 if has_refusal else 0.0,
                reason="スコープ外: " + ("正しく拒否した" if has_refusal else "拒否すべきだった"),
            )

        cited = [sid for sid in expected_source_ids if sid in answer]
        missing = [sid for sid in expected_source_ids if sid not in answer]
        score = len(cited) / len(expected_source_ids)
        passed = score >= 0.5

        reason = f"{len(cited)}/{len(expected_source_ids)} 件の出典を引用"
        if missing:
            reason += f"（不足: {', '.join(missing)}）"

        logger.debug("SourceCitationGrader: score=%.2f, cited=%s", score, cited)
        return GraderScore(grader_name="citation", passed=passed, score=score, reason=reason)


class CompositeGrader:
    """複数のグレーダーを設定可能な重みで組み合わせる。"""

    def __init__(
        self,
        keyword_weight: float = 0.5,
        citation_weight: float = 0.5,
    ) -> None:
        self.keyword_grader = KeywordGrader()
        self.citation_grader = SourceCitationGrader()
        self.keyword_weight = keyword_weight
        self.citation_weight = citation_weight

    def grade(self, trial: EvalTrial, task: EvalTask) -> list[GraderScore]:
        """すべてのグレーダーを実行し、スコアのリストを返す。"""
        scores: list[GraderScore] = []

        # キーワード採点
        keyword_score = self.keyword_grader.grade(trial.answer, task.expected_keywords)
        scores.append(keyword_score)

        # 出典引用の採点
        citation_score = self.citation_grader.grade(trial.answer, task.expected_source_ids)
        scores.append(citation_score)

        # ツール呼び出しの採点 — エージェントが検索ツールを使ったかを確認する
        tool_names = [tc.get("name", "") for tc in trial.tool_calls]
        tool_called = "search_knowledge_base" in tool_names
        tool_score = GraderScore(
            grader_name="tool_call",
            passed=tool_called,
            score=1.0 if tool_called else 0.0,
            reason=(
                f"search_knowledge_baseを呼び出した（計{len(trial.tool_calls)}回）"
                if tool_called
                else "search_knowledge_baseを呼び出していない"
            ),
        )
        scores.append(tool_score)

        # 複合スコア — キーワードと出典引用の加重平均
        composite_val = (
            keyword_score.score * self.keyword_weight + citation_score.score * self.citation_weight
        )
        composite_passed = composite_val >= 0.5
        scores.append(
            GraderScore(
                grader_name="composite",
                passed=composite_passed,
                score=round(composite_val, 3),
                reason=(
                    f"加重平均: keyword({self.keyword_weight}) + "
                    f"citation({self.citation_weight}) = {composite_val:.3f}"
                ),
            )
        )

        logger.debug(
            "CompositeGrader for %s: composite=%.3f, passed=%s",
            task.id,
            composite_val,
            composite_passed,
        )
        return scores
