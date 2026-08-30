"""
LLM-as-Judge評価 (OpenRouter)

構造化されたルーブリックを使ってエージェントの応答をLLMに評価させる方法を
実演する。ジャッジはtool_choiceで構造化出力を強制し、正確性・網羅性・
根拠性を1〜5のスケールで採点する。
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common import OpenRouterTokenTracker, setup_logging
from dotenv import find_dotenv, load_dotenv
from openrouter import OpenRouter
from openrouter.components import ChatResult
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from shared_openrouter.agent import ResearchAssistant
from shared_openrouter.knowledge_base import KNOWLEDGE_BASE

load_dotenv(find_dotenv())

logger = setup_logging(__name__)


# ---------------------------------------------------------------------------
# LLM-as-Judge — ルーブリックによる構造化評価
# ---------------------------------------------------------------------------

# ジャッジはtool_choiceで構造化出力を強制する
JUDGE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "submit_evaluation",
            "description": "エージェント応答の構造化された評価スコアを提出します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning": {
                        "type": "string",
                        "description": "応答品質についての思考過程（Chain-of-Thought）",
                    },
                    "accuracy_score": {
                        "type": "integer",
                        "description": "正確性のスコア（1〜5）",
                        "minimum": 1,
                        "maximum": 5,
                    },
                    "accuracy_reason": {"type": "string"},
                    "completeness_score": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 5,
                    },
                    "completeness_reason": {"type": "string"},
                    "grounding_score": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 5,
                    },
                    "grounding_reason": {"type": "string"},
                },
                "required": [
                    "reasoning",
                    "accuracy_score",
                    "accuracy_reason",
                    "completeness_score",
                    "completeness_reason",
                    "grounding_score",
                    "grounding_reason",
                ],
            },
        },
    },
]

# 評価を一貫させるためにジャッジへ渡すルーブリック
JUDGE_SYSTEM_PROMPT = """あなたはリサーチアシスタントエージェントの評価を専門とするエキスパートです。

以下のルーブリックを使ってエージェントの応答を評価してください:

**正確性 (1〜5)**
1: 重大な事実誤認またはでっち上げの情報
2: いくつかの不正確な点がある
3: おおむね正確だが軽微な誤りがある
4: 些細な問題を除いて正確
5: 完全に正確で、すべての事実が参照ドキュメントと一致している

**網羅性 (1〜5)**
1: 関連情報のほとんどを見落としている
2: 関連するポイントの半分未満しかカバーしていない
3: 主要なポイントはカバーしているが一部の詳細を見落としている
4: 軽微な省略を除いて網羅的
5: 包括的で、関連するすべての側面をカバーしている

**根拠性 (1〜5)**
1: 出典の引用が一切ない
2: 一部の主張に裏付けがない
3: ほとんどの主張は引用されているがいくつかの抜けがある
4: ほぼすべての主張が適切に引用されている
5: すべての主張が引用元に基づいている

必ずsubmit_evaluationツールを使って構造化された評価を提出してください。"""


@dataclass
class JudgeResult:
    """LLMジャッジ評価の構造化された結果。"""

    reasoning: str
    accuracy_score: int
    accuracy_reason: str
    completeness_score: int
    completeness_reason: str
    grounding_score: int
    grounding_reason: str

    @property
    def avg_score(self) -> float:
        """全次元にわたる平均スコアを計算する。"""
        return (self.accuracy_score + self.completeness_score + self.grounding_score) / 3.0


class LLMJudge:
    """構造化されたルーブリックを使ってエージェントの応答をLLMで評価する。"""

    def __init__(
        self,
        client: OpenRouter,
        model: str = "deepseek/deepseek-v4-flash-0731",
    ) -> None:
        self.client = client
        self.model = model
        self.token_tracker = OpenRouterTokenTracker()

    def evaluate(
        self,
        question: str,
        answer: str,
        reference_docs: list[dict[str, Any]],
        expected_answer: str | None = None,
    ) -> JudgeResult:
        """Chain-of-Thoughtによるジャッジングでエージェントの応答を評価する。"""
        # ジャッジに必要なコンテキストをすべて含めて評価プロンプトを組み立てる
        ref_text = json.dumps(reference_docs, indent=2, ensure_ascii=False)
        prompt = (
            f"## 質問\n{question}\n\n"
            f"## エージェントの回答\n{answer}\n\n"
            f"## 参照ドキュメント（正解データ）\n{ref_text}"
        )
        if expected_answer:
            prompt += f"\n\n## 期待される回答の要約\n{expected_answer}"

        logger.info("LLM judge evaluating answer (question: %s...)", question[:50])

        # submit_evaluationツールを通じて構造化出力を強制するためtool_choiceを使う
        response: ChatResult = self.client.chat.send(  # type: ignore[call-overload]
            model=self.model,
            max_tokens=1024,
            reasoning={"effort": "none"},
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            tools=JUDGE_TOOLS,
            tool_choice={"type": "function", "function": {"name": "submit_evaluation"}},
        )
        assert response.usage is not None
        self.token_tracker.track(response.usage)

        # tool_callからの構造化された評価を抽出する
        tool_calls = response.choices[0].message.tool_calls or []
        for tool_call in tool_calls:
            if tool_call.function.name == "submit_evaluation":
                args = json.loads(tool_call.function.arguments)
                return JudgeResult(
                    reasoning=args["reasoning"],
                    accuracy_score=args["accuracy_score"],
                    accuracy_reason=args["accuracy_reason"],
                    completeness_score=args["completeness_score"],
                    completeness_reason=args["completeness_reason"],
                    grounding_score=args["grounding_score"],
                    grounding_reason=args["grounding_reason"],
                )

        # tool_callが見つからない場合のフォールバック（tool_choice使用時は発生しないはず）
        logger.warning("Judge did not return structured evaluation")
        return JudgeResult(
            reasoning="Failed to parse",
            accuracy_score=1,
            accuracy_reason="Parse error",
            completeness_score=1,
            completeness_reason="Parse error",
            grounding_score=1,
            grounding_reason="Parse error",
        )


# ---------------------------------------------------------------------------
# デモモード用のシミュレートされたジャッジ結果
# ---------------------------------------------------------------------------

SIMULATED_JUDGE_RESULTS: dict[str, JudgeResult] = {
    "task_001": JudgeResult(
        reasoning=(
            "回答はスケーラビリティ・障害分離・独立したデプロイを主な利点として"
            "正確に挙げており、doc_001と一致している。"
        ),
        accuracy_score=5,
        accuracy_reason="記載されている利点はすべて参照ドキュメントと完全に一致している。",
        completeness_score=4,
        completeness_reason="主な利点はカバーしているが、技術選定の柔軟性の詳細を省略している。",
        grounding_score=5,
        grounding_reason="doc_001を出典として適切に引用している。",
    ),
    "task_002": JudgeResult(
        reasoning=(
            "回答はdoc_002から、エンドポイントの名詞・HTTPメソッド・ステータスコード・"
            "バージョニング・ページネーションをカバーしている。"
        ),
        accuracy_score=5,
        accuracy_reason="すべての事実がdoc_002と一致している。",
        completeness_score=5,
        completeness_reason="REST API設計の主要な原則をすべてカバーしている。",
        grounding_score=4,
        grounding_reason="doc_002を引用しているが、一部の主張に明示的な出典表記がない。",
    ),
    "task_003": JudgeResult(
        reasoning=(
            "B-treeインデックス・検索構造・EXPLAINについて言及しており、いずれも"
            "doc_003に基づいている。複合インデックスの詳細を見落としている。"
        ),
        accuracy_score=5,
        accuracy_reason="記載されている事実はすべてdoc_003通りで正しい。",
        completeness_score=3,
        completeness_reason="複合インデックスと過剰なインデックスのトレードオフを省略している。",
        grounding_score=4,
        grounding_reason="doc_003を引用しているが、すべての主張に明示的な出典表記があるわけではない。",
    ),
}


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


def main() -> None:
    """リサーチアシスタントの応答に対してLLM-as-judge評価を実行する。"""
    console = Console()
    console.print(
        Panel(
            "[bold cyan]LLM-as-Judge評価[/bold cyan]\n\n"
            "LLMを使ってエージェントの応答を3つの観点で評価します:\n"
            "正確性・網羅性・根拠性（それぞれ1〜5のスケール）。\n"
            "構造化出力はtool_choiceで強制されます。",
            title="評価チュートリアル2",
        )
    )

    has_api_key = bool(os.environ.get("OPENROUTER_API_KEY"))
    if has_api_key:
        console.print("[green]APIキーが見つかりました — ライブ評価を実行します[/green]\n")
        client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        agent = ResearchAssistant(client, KNOWLEDGE_BASE)
        judge = LLMJudge(client)
    else:
        console.print(
            "[yellow]APIキーが見つかりません — デモ用にシミュレートされた結果を使用します[/yellow]\n"
        )
        agent = None
        judge = None

    # このデモ用にタスクのサブセットを読み込む
    dataset_path = Path(__file__).parent / "datasets_openrouter" / "golden_tasks.json"
    with dataset_path.open(encoding="utf-8") as f:
        data = json.load(f)
    tasks = data["tasks"]

    # デモ用に最初の数タスクのみを使用する（LLM-as-judgeはコストがかかる）
    eval_tasks = tasks[:3] if agent is None else tasks[:5]
    console.print(f"LLM-as-judgeで{len(eval_tasks)}件のタスクを評価します...\n")

    # 結果テーブル
    table = Table(title="LLM-as-Judge結果", show_lines=True)
    table.add_column("タスク", style="cyan", width=12)
    table.add_column("正確性", width=10, justify="center")
    table.add_column("網羅性", width=12, justify="center")
    table.add_column("根拠性", width=10, justify="center")
    table.add_column("平均", width=8, justify="center")
    table.add_column("理由", width=50)

    all_results: list[JudgeResult] = []

    for task in eval_tasks:
        task_id = task["id"]
        logger.info("Evaluating %s with LLM judge", task_id)

        if agent is not None and judge is not None:
            try:
                response = agent.answer(task["question"])
                # ジャッジ用の参照ドキュメントを集める
                ref_docs = [
                    doc for doc in KNOWLEDGE_BASE if doc["id"] in task["expected_source_ids"]
                ]
                result = judge.evaluate(
                    question=task["question"],
                    answer=response["answer"],
                    reference_docs=ref_docs if ref_docs else KNOWLEDGE_BASE[:2],
                )
            except Exception as e:
                logger.error("Error evaluating %s: %s", task_id, e)
                result = JudgeResult(
                    reasoning=f"Error: {e}",
                    accuracy_score=1,
                    accuracy_reason="Error",
                    completeness_score=1,
                    completeness_reason="Error",
                    grounding_score=1,
                    grounding_reason="Error",
                )
        else:
            result = SIMULATED_JUDGE_RESULTS.get(
                task_id,
                JudgeResult(
                    reasoning="No simulated result",
                    accuracy_score=3,
                    accuracy_reason="N/A",
                    completeness_score=3,
                    completeness_reason="N/A",
                    grounding_score=3,
                    grounding_reason="N/A",
                ),
            )

        all_results.append(result)

        # スコアを色分けする
        def score_color(s: int) -> str:
            if s >= 4:
                return f"[green]{s}/5[/green]"
            if s >= 3:
                return f"[yellow]{s}/5[/yellow]"
            return f"[red]{s}/5[/red]"

        # テーブル表示用に理由を切り詰める
        short_reasoning = (
            result.reasoning[:80] + "..." if len(result.reasoning) > 80 else result.reasoning
        )

        table.add_row(
            task_id,
            score_color(result.accuracy_score),
            score_color(result.completeness_score),
            score_color(result.grounding_score),
            f"{result.avg_score:.1f}",
            short_reasoning,
        )

    console.print(table)

    # 集計統計
    if all_results:
        avg_accuracy = sum(r.accuracy_score for r in all_results) / len(all_results)
        avg_completeness = sum(r.completeness_score for r in all_results) / len(all_results)
        avg_grounding = sum(r.grounding_score for r in all_results) / len(all_results)
        overall = sum(r.avg_score for r in all_results) / len(all_results)

        console.print("\n[bold]集計スコア[/bold]")
        console.print(f"  正確性:   {avg_accuracy:.2f}/5")
        console.print(f"  網羅性:   {avg_completeness:.2f}/5")
        console.print(f"  根拠性:   {avg_grounding:.2f}/5")
        console.print(f"  総合:     {overall:.2f}/5")

    # ---------------------------------------------------------------------------
    # グレーダーのキャリブレーション: LLMジャッジのスコアを人間のベースラインと比較する
    # ベストプラクティス: LLM-as-judgeのグレーダーは人間の専門家と密に校正する
    # ---------------------------------------------------------------------------
    console.print(
        "\n[bold]グレーダーのキャリブレーション（LLMジャッジ vs 人間のベースライン）[/bold]"
    )
    console.print(
        "[dim]最初の3タスクについてシミュレートした人間のスコア——実際には"
        "ドメインエキスパートから収集してください。[/dim]\n"
    )

    # シミュレートされた人間の専門家のスコア（本番ではラベリングセッションから取得する）
    human_baselines: list[dict[str, int]] = [
        {"accuracy": 5, "completeness": 4, "grounding": 5},
        {"accuracy": 5, "completeness": 5, "grounding": 5},
        {"accuracy": 4, "completeness": 3, "grounding": 4},
    ]

    cal_table = Table(title="キャリブレーション: LLMジャッジ vs 人間の専門家", show_lines=True)
    cal_table.add_column("タスク", style="cyan", width=12)
    cal_table.add_column("観点", width=14)
    cal_table.add_column("人間", width=8, justify="center")
    cal_table.add_column("LLMジャッジ", width=10, justify="center")
    cal_table.add_column("差分", width=8, justify="center")

    num_calibration = min(3, len(all_results))
    for i in range(num_calibration):
        task_id = eval_tasks[i]["id"] if isinstance(eval_tasks[i], dict) else eval_tasks[i]
        judge_r = all_results[i]
        human = human_baselines[i]

        for dim, human_score, judge_score in [
            ("正確性", human["accuracy"], judge_r.accuracy_score),
            ("網羅性", human["completeness"], judge_r.completeness_score),
            ("根拠性", human["grounding"], judge_r.grounding_score),
        ]:
            delta = judge_score - human_score
            delta_str = f"{delta:+d}"
            delta_color = "green" if delta == 0 else ("yellow" if abs(delta) == 1 else "red")
            cal_table.add_row(
                task_id if dim == "正確性" else "",
                dim,
                str(human_score),
                str(judge_score),
                f"[{delta_color}]{delta_str}[/{delta_color}]",
            )

    console.print(cal_table)

    # トークン使用量
    if agent is not None:
        console.print("\n[bold]トークン使用量[/bold]")
        console.print("[dim]Agent:[/dim]")
        agent.token_tracker.report()
    if judge is not None:
        console.print("[dim]Judge:[/dim]")
        judge.token_tracker.report()


if __name__ == "__main__":
    main()
