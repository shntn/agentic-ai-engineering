"""
評価者・最適化 — 「編集者のデスク」(OpenRouter)

あるLLMがコンテンツを生成し、別のLLMがそれをループで評価し、品質基準を満たすまで
改善を繰り返す様子を実演します。生成者と評価者は、それぞれ異なる目的を持つ
異なるプロンプトを使用します。

パイプライン: リサーチ（Web検索） → 執筆（ツールなし） → 評価 → 改善ループ
"""

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from dotenv import find_dotenv, load_dotenv
from openrouter import OpenRouter
from openrouter.components import ChatResult
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from common import OpenRouterTokenTracker, interactive_menu, setup_logging
from openrouter_adapter import to_openrouter_tools

load_dotenv(find_dotenv())
logger = setup_logging(__name__)

OUTPUT_DIR = Path("output")
MODEL = "deepseek/deepseek-v4-flash-0731"
LIGHT_MODEL = "deepseek/deepseek-v4-flash-0731"

# OpenRouterのweb_searchサーバーツール — サーバー側で実行され、モデルが検索するかどうかを判断する。
# max_uses で検索回数の上限を指定しないと、モデルが納得いくまで何度も検索を繰り返し、
# 実行時間・コストが跳ね上がることがある
WEB_SEARCH_TOOL = {
    "type": "openrouter:web_search",
    "parameters": {"max_uses": 1},
}

# OpenRouterのweb_searchサーバーツールは、検索の完了まで数十秒かかることがある。
# SDKのデフォルトタイムアウトはこれより短く、正常な応答でも ReadTimeout →
# 自動リトライ → 再度 ReadTimeout... のループに陥り、体感で10分以上かかることがある。
# そのため、Web検索を使うステップでも安全に完了できるよう、余裕を持った値を明示する。
REQUEST_TIMEOUT_MS = 120_000  # 120秒

SUGGESTED_TOPICS = [
    "イベント駆動マイクロサービスの構築",
    "リアルタイムAIのためのエッジコンピューティング",
    "モダンなCSSレイアウト技法",
    "データベースシャーディング戦略",
]

# --- プロンプト ---

RESEARCH_SYSTEM_PROMPT = (
    "あなたはテクニカルリサーチャーです。Web検索を使って、トピックに関する最新かつ"
    "正確な情報を見つけてください。最も関連性の高い調査結果を統合した2〜3段落の"
    "短い文章を書いてください。実践的な詳細・トレードオフ・実際のパターンに焦点を"
    "当ててください。前置きは不要です。"
)

WRITER_SYSTEM_PROMPT = (
    "あなたは技術ブログのライターです。与えられたリサーチメモを基に、簡潔なブログ記事を"
    "書いてください。導入部、有益な見出しを持つ3〜5個のセクション、関連する箇所には"
    "コード例、そして結論を含めてください。専門的でありながら親しみやすいトーンを"
    "使用してください。全体で1000語未満を目指してください — 無駄な内容や冗長な表現は"
    "避けてください。"
)

REFINER_SYSTEM_PROMPT = (
    "あなたは技術ブログのライターです。提供されたフィードバックを基に下書きを修正して"
    "ください。すべての問題点と提案に対応してください。全体的な構成は維持しつつ、"
    "品質を改善してください。修正済みの記事全体を返してください。"
)

EVALUATOR_SYSTEM_PROMPT = """\
あなたは要求水準の高いテクニカルエディターです。コンテンツを以下の観点で1〜10点で評価してください:

1. 明瞭さ: エンジニアは読み返さずに理解できるか？ \
（9〜10: 非常に明瞭、7〜8: 多少読みにくい箇所がある、5〜6: 理解に努力が必要）
2. 技術的正確性: 情報は正確で最新か？ \
（9〜10: 本番運用レベル、7〜8: 多少の不正確さがある）
3. 構成: 論理的な流れで、読みやすいか？ \
（9〜10: 完璧な展開、拾い読みしやすい）
4. エンゲージメント: エンジニアはこれを読みたいと思うか？ \
（9〜10: 魅力的で記憶に残る）
5. 人間らしさ: 実在の人間が書いたような文章か？ \
（9〜10: 自然でリズムに変化がある、5〜6: 機械的・没個性的）

フィードバックは具体的にしてください: 「導入部が一般的すぎる — 具体的な課題から始めましょう」\
のように、単に「もっと魅力的にしてください」ではなく。"""

# 5次元の構造化された評価出力
EVALUATION_TOOLS = [
    {
        "name": "evaluate_draft",
        "description": "ブログ記事の下書きを複数の品質観点で評価する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "clarity": {"type": "integer", "minimum": 1, "maximum": 10},
                "technical_accuracy": {"type": "integer", "minimum": 1, "maximum": 10},
                "structure": {"type": "integer", "minimum": 1, "maximum": 10},
                "engagement": {"type": "integer", "minimum": 1, "maximum": 10},
                "human_voice": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "実在の人間が書いたような文章に聞こえるか？",
                },
                "issues": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "見つかった具体的な問題点",
                },
                "suggestions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "実行可能な改善提案",
                },
            },
            "required": [
                "clarity",
                "technical_accuracy",
                "structure",
                "engagement",
                "human_voice",
                "issues",
                "suggestions",
            ],
        },
    }
]

SCORE_THRESHOLD = 7.0
MAX_REFINEMENTS = 2

# コールバック型: エージェントが (event_name, event_data) を発行し、呼び出し側が表示方法を決める
EvaluatorCallback = Callable[[str, dict[str, Any]], None]

SCORE_DIMENSIONS = ["Clarity", "Technical Accuracy", "Structure", "Engagement", "Human Voice"]
SCORE_KEYS = ["clarity", "technical_accuracy", "structure", "engagement", "human_voice"]


def _extract_scores(evaluation: dict[str, Any]) -> tuple[dict[str, int], float]:
    """評価結果から各スコアを抽出し、平均値を計算する"""
    scores = dict(zip(SCORE_DIMENSIONS, (evaluation[k] for k in SCORE_KEYS)))
    return scores, sum(scores.values()) / len(scores)


class EvaluatorOptimizer:
    """品質基準を満たすまでの、リサーチ → 執筆 → 評価 → 改善のループ。"""

    def __init__(self, model: str, light_model: str, token_tracker: OpenRouterTokenTracker):
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.model = model
        self.light_model = light_model
        self.token_tracker = token_tracker
        self._notify: EvaluatorCallback = lambda _e, _d: None

    def _call_llm(
        self,
        system: str,
        messages: list[dict[str, Any]],
        *,
        use_light: bool = False,
        max_tokens: int = 4096,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        timeout_ms: int | None = None,
    ) -> ChatResult:
        """単一のLLM呼び出しを行い、トークンを追跡"""
        model = self.light_model if use_light else self.model
        kwargs: dict[str, Any] = {}
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
        # デフォルトのタイムアウト設定は短すぎるため、timeout_ms の設定を追加
        if timeout_ms:
            kwargs["timeout_ms"] = timeout_ms
        tool_names = [t.get("name", t.get("type", "unknown")) for t in tools or []]
        logger.info("Calling %s, tools=%s", model, tool_names)

        response: ChatResult = self.client.chat.send(  # type: ignore[call-overload]
            model=model,
            max_tokens=max_tokens,
            reasoning={"effort": "none"},
            messages=[{"role": "system", "content": system}, *messages],
            **kwargs,
        )
        self.token_tracker.track(response.usage)
        return response

    def _call_llm_text(self, system: str, user_message: str, **kwargs: Any) -> str:
        """LLMを呼び出し、テキストの内容を返す"""
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        response = self._call_llm(system, messages, **kwargs)
        return str(response.choices[0].message.content or "")

    def _research(self, topic: str) -> str:
        """リサーチフェーズ: Web検索でトピックに関する最新データを集める"""
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": f"次のトピックについて調査してください: {topic}"}
        ]
        response = self._call_llm(
            RESEARCH_SYSTEM_PROMPT,
            messages,
            use_light=True,
            max_tokens=1024,
            tools=[WEB_SEARCH_TOOL],
            timeout_ms=REQUEST_TIMEOUT_MS,
        )
        return str(response.choices[0].message.content or "")

    def _write(self, topic: str, research: str) -> str:
        """執筆フェーズ: リサーチデータから統合する — ツールもWeb検索も使わない"""
        user_msg = (
            f"リサーチ:\n{research}\n\n次のトピックについてブログ記事を書いてください: {topic}"
        )
        return self._call_llm_text(WRITER_SYSTEM_PROMPT, user_msg, use_light=True)

    def _refine(self, topic: str, draft: str, research: str, evaluation: dict[str, Any]) -> str:
        """改善フェーズ: フィードバックを基に書き直す — ツールなし"""
        feedback = (
            f"問題点: {json.dumps(evaluation['issues'], ensure_ascii=False)}\n"
            f"提案: {json.dumps(evaluation['suggestions'], ensure_ascii=False)}"
        )
        user_msg = (
            f"トピック: {topic}\n\n"
            f"リサーチ:\n{research}\n\n"
            f"対応すべきフィードバック:\n{feedback}\n\n"
            f"前回の下書き:\n{draft}\n\n"
            "すべてのフィードバックに対応するよう下書きを修正してください。"
        )
        return self._call_llm_text(REFINER_SYSTEM_PROMPT, user_msg)

    def _evaluate(self, draft: str, topic: str) -> dict[str, Any]:
        """評価者: 下書きを複数の観点で採点し、フィードバックを提供する"""
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": f"トピック: {topic}\n\n評価する下書き:\n\n{draft}"}
        ]
        response = self._call_llm(
            EVALUATOR_SYSTEM_PROMPT,
            messages,
            use_light=True,
            max_tokens=1024,
            tools=to_openrouter_tools(EVALUATION_TOOLS),
            tool_choice={"type": "function", "function": {"name": "evaluate_draft"}},
        )

        tool_calls = response.choices[0].message.tool_calls or []
        if tool_calls:
            return cast(dict[str, Any], json.loads(tool_calls[0].function.arguments))

        raise ValueError("Evaluator did not return structured evaluation")

    def run(self, topic: str, on_event: EvaluatorCallback | None = None) -> str:
        """パイプライン全体を実行する: リサーチ → 執筆 → 評価 → 改善ループ"""
        self._notify = on_event or (lambda _e, _d: None)

        # ステップ1: リサーチ（Web検索でデータを集める）
        self._notify("research_start", {})
        research = self._research(topic)
        self.token_tracker.report()
        self._notify("research_complete", {"chars": len(research)})

        # ステップ2: 執筆（リサーチデータから、ツールなし）
        self._notify("write_start", {})
        draft = self._write(topic, research)
        self._notify("draft_complete", {"chars": len(draft)})

        # ステップ3: 評価 → 改善ループ
        for iteration in range(1, MAX_REFINEMENTS + 1):
            self._notify("evaluate_start", {"iteration": iteration})
            evaluation = self._evaluate(draft, topic)
            scores, avg_score = _extract_scores(evaluation)
            self.token_tracker.report()

            self._notify(
                "evaluation_complete",
                {
                    "iteration": iteration,
                    "scores": scores,
                    "avg": avg_score,
                    "issues": evaluation.get("issues", []),
                    "suggestions": evaluation.get("suggestions", []),
                },
            )

            if avg_score >= SCORE_THRESHOLD:
                self._notify("threshold_met", {"avg": avg_score})
                break

            if iteration < MAX_REFINEMENTS:
                self._notify("refining", {"avg": avg_score})
                draft = self._refine(topic, draft, research, evaluation)
                self._notify("draft_complete", {"chars": len(draft)})
            else:
                self._notify("max_iterations", {"avg": avg_score})

        return draft


def main() -> None:
    """評価者・最適化のデモを実行"""
    console = Console()
    token_tracker = OpenRouterTokenTracker()

    def on_event(event: str, data: dict[str, Any]) -> None:
        """パイプラインのイベントをコンソール表示用に処理する"""
        if event == "research_start":
            console.print("\n[bold yellow]Researching:[/bold yellow] 最新データを収集中...")
        elif event == "research_complete":
            console.print(f"  [green]✓[/green] Research: {data['chars']} chars")
        elif event == "write_start":
            console.print("\n[bold yellow]Writing:[/bold yellow] 初稿を生成中...")
        elif event == "draft_complete":
            console.print(f"  [green]✓[/green] Draft: {data['chars']} chars")
        elif event == "evaluate_start":
            console.print(
                f"\n[bold yellow]Evaluating:[/bold yellow] Round {data['iteration']}"
                f"/{MAX_REFINEMENTS}..."
            )
        elif event == "evaluation_complete":
            scores = data["scores"]
            avg = data["avg"]
            table = Table(title=f"Evaluation (avg: {avg:.1f}/10)")
            table.add_column("Dimension", style="cyan")
            table.add_column("Score", justify="center")
            for dim, score in scores.items():
                color = "green" if score >= 8 else "yellow" if score >= 6 else "red"
                table.add_row(dim, f"[{color}]{score}/10[/{color}]")
            console.print(table)
            if data["issues"]:
                console.print("[bold red]Issues:[/bold red]")
                for issue in data["issues"]:
                    console.print(f"  [red]•[/red] {issue}")
            if data.get("suggestions"):
                console.print("[bold yellow]Suggestions:[/bold yellow]")
                for suggestion in data["suggestions"]:
                    console.print(f"  [yellow]•[/yellow] {suggestion}")
        elif event == "threshold_met":
            console.print(f"\n[green]Score {data['avg']:.1f} >= {SCORE_THRESHOLD} — done![/green]")
        elif event == "refining":
            console.print(
                f"[yellow]Score {data['avg']:.1f} < {SCORE_THRESHOLD} — refining...[/yellow]"
            )
        elif event == "max_iterations":
            console.print(f"\n[yellow]Max iterations reached (score: {data['avg']:.1f})[/yellow]")

    header = Panel(
        "[bold cyan]評価者・最適化 — 編集者のデスク[/bold cyan]\n\n"
        "トピック → [Researcher] → データ\n"
        "         → [Writer] → 下書き（リサーチから、Web検索なし）\n"
        "         → [Evaluator] → 5次元スコア + フィードバック\n"
        f"         → スコア >= {SCORE_THRESHOLD}？ → 完了\n"
        "         → 閾値未満 → [Refiner] → ループ\n\n"
        f"最大 {MAX_REFINEMENTS} 回の改善。"
        "スコア: 明瞭さ、正確性、構成、エンゲージメント、人間らしさ（1〜10）",
        title="Evaluator-Optimizer",
    )

    try:
        while True:
            topic = interactive_menu(
                console,
                SUGGESTED_TOPICS,
                title="トピックを選択",
                header=header,
                allow_custom=True,
                custom_prompt="トピックを入力してください",
            )
            if not topic:
                break

            console.print(f"\n[bold green]Topic:[/bold green] {topic}")
            eo = EvaluatorOptimizer(MODEL, LIGHT_MODEL, token_tracker)

            try:
                result = eo.run(topic, on_event=on_event)

                OUTPUT_DIR.mkdir(exist_ok=True)
                slug = topic.lower().replace(" ", "_")[:50]
                path = OUTPUT_DIR / f"{slug}.md"
                path.write_text(result, encoding="utf-8")

                console.print("\n[bold blue]Final Article:[/bold blue]")
                console.print(Markdown(result))
                abs_path = path.resolve()
                console.print(f"\n[dim]Saved to [link=file://{abs_path}]{path}[/link][/dim]")

                console.print("\n[dim]Press Enter to continue...[/dim]")
                input()
            except Exception as e:
                logger.error("Evaluator-optimizer failed: %s", e)
                console.print(f"\n[red]Error: {e}[/red]")
            finally:
                token_tracker.reset()

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")


if __name__ == "__main__":
    main()
