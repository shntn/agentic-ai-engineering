"""
モデルルーティング (OpenRouter)

インテリジェントなモデルルーティングによるコスト最適化を実演します。安価な分類器
（deepseek-v4-flash）がタスクの難易度を評価し、deepseek-v4-flash（簡単）または
deepseek-v4-pro（難しい）にルーティングします。全タスクをdeepseek-v4-proで処理
した場合とのコスト削減効果を実際の数値で示します。

【注意】Total savingsは件数ベースではなく金額ベースの加重平均（合計節約額 /
合計baseline金額）で計算される。そのため、hardタスクの回答が長文になると
そのタスクのコストが総コストを支配してしまい、easyタスクの高い節約率
（Flash利用時は約90%超）が薄まって、全体の節約率が実際より低く見えることが
ある。逆にhardタスクの回答が短ければ、easyタスクの節約効果が全体にも反映され
やすくなる。

期待通りのコスト削減効果が得られるプロンプト例（Interactiveモードで試す場合）:
  easy (5問):
    1. 日本の首都はどこですか？
    2. 1マイルは何キロメートルですか？
    3. 12×8はいくつですか？
    4. 光の速さはおよそ秒速何キロメートルですか？
    5. HTTPのデフォルトポート番号は何番ですか？
  hard (3問。回答を短く指定することで「hardだが低コスト」なケースを再現できる):
    6. RESTとGraphQLの主な違いを1文で説明してください。
    7. 書き込みレイテンシを最小化しつつキャッシュの一貫性も保つには、Write-throughと
       Write-backのどちらが適切か、理由を一言で答えてください。
    8. マイクロサービスとモノリスのどちらを選ぶべきか、理由を一言で答えてください。
"""

import os
from dataclasses import dataclass, field
from typing import Any

from dotenv import find_dotenv, load_dotenv
from openrouter import OpenRouter
from openrouter.components import ChatResult
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from common import OpenRouterTokenTracker, setup_logging
from common.menu import interactive_menu

# ルートの.envファイルから環境変数を読み込む
load_dotenv(find_dotenv())

# ロギングを設定
logger = setup_logging(__name__)

# モデル設定
# deepseek-v4-pro は deepseek-v4-flash の約11倍の料金（input/output とも）
# なので、簡単なタスクをflashに逃がすことで実際にコストが変わる組み合わせにしている
MODEL_CLASSIFIER = "deepseek/deepseek-v4-flash"
MODEL_EASY = "deepseek/deepseek-v4-flash"
MODEL_HARD = "deepseek/deepseek-v4-pro"

# 料金（$ per トークン）。client.models.list() で取得した実測値（2026年8月時点）。
PRICING = {
    "easy_input": 0.00000007798,
    "easy_output": 0.00000015596,
    "hard_input": 0.00000087,
    "hard_output": 0.00000174,
}

# 簡単なタスクと難しいタスクを混在させたサンプル
SAMPLE_TASKS = [
    "フランスの首都はどこですか？",
    "華氏72度を摂氏に変換してください。",
    "10万人の同時接続ユーザーを50ミリ秒未満のレイテンシで処理する必要がある"
    "リアルタイムマルチプレイヤーゲーム向けのマイクロサービスアーキテクチャを設計してください。",
    "初代iPhoneが発売されたのは何年ですか？",
    "完全な監査証跡と規制コンプライアンスが求められる金融取引システムにおいて、"
    "イベントソーシングと従来のCRUDのトレードオフを分析してください。",
    "1キロメートルは何メートルですか？",
    "グローバルに分散したECプラットフォームでPostgreSQL・Cassandra・CockroachDBを"
    "選定する際のCAP定理の含意を比較・対比してください。",
    "金の元素記号は何ですか？",
]


@dataclass
class TaskResult:
    """ルーティングされたタスク実行の結果。"""

    task: str
    difficulty: str
    model_used: str
    response: str
    routed_cost: float
    baseline_cost: float  # deepseek-v4-proで処理した場合のコスト


class ModelRouter:
    """タスクの複雑さに基づいて適切なモデルにルーティングする。"""

    def __init__(self, token_tracker: OpenRouterTokenTracker):
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.token_tracker = token_tracker
        self.results: list[TaskResult] = field(default_factory=list)
        self.results = []

    def _call_llm(self, model: str, messages: list[dict[str, Any]], max_tokens: int) -> ChatResult:
        """単一のLLM呼び出しを行い、トークンを追跡する"""
        response: ChatResult = self.client.chat.send(  # type: ignore[call-overload]
            model=model,
            max_tokens=max_tokens,
            reasoning={"effort": "none"},
            messages=messages,
        )
        self.token_tracker.track(response.usage)
        return response

    def classify(self, task: str) -> str:
        """分類器モデルを使い、タスクの難易度を'easy'または'hard'に分類する"""
        response = self._call_llm(
            MODEL_CLASSIFIER,
            [
                {
                    "role": "system",
                    "content": (
                        "次のタスクを'easy'または'hard'のどちらかに分類してください。\n"
                        "Easy: 単純な事実検索、単位変換、基本的な計算、定義。\n"
                        "Hard: 分析、アーキテクチャ設計、複数ステップの推論、比較、"
                        "創作、コードレビュー。\n"
                        "ちょうど1単語だけで答えてください: easy または hard。"
                    ),
                },
                {"role": "user", "content": task},
            ],
            max_tokens=10,
        )

        classification = str(response.choices[0].message.content or "").strip().lower()
        # 分類が不明瞭な場合はhardをデフォルトにする
        if classification not in ("easy", "hard"):
            logger.warning("Unclear classification '%s', defaulting to hard", classification)
            classification = "hard"

        logger.info("Classified as '%s': %s", classification, task[:60])
        return classification

    def execute(self, task: str, model: str) -> tuple[str, int, int]:
        """指定したモデルでタスクを実行し、(応答, input_tokens, output_tokens) を返す"""
        response = self._call_llm(model, [{"role": "user", "content": task}], max_tokens=1024)
        assert response.usage is not None

        return (
            str(response.choices[0].message.content or ""),
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
        )

    def _calculate_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """指定したモデルとトークン数に対するコストを計算する"""
        if model == MODEL_HARD:
            return input_tokens * PRICING["hard_input"] + output_tokens * PRICING["hard_output"]
        return input_tokens * PRICING["easy_input"] + output_tokens * PRICING["easy_output"]

    def route_and_execute(self, task: str) -> TaskResult:
        """分類・ルーティング・実行し、コストを記録する"""
        difficulty = self.classify(task)

        model = MODEL_EASY if difficulty == "easy" else MODEL_HARD
        response_text, input_tokens, output_tokens = self.execute(task, model)

        routed_cost = self._calculate_cost(model, input_tokens, output_tokens)
        baseline_cost = self._calculate_cost(MODEL_HARD, input_tokens, output_tokens)

        result = TaskResult(
            task=task,
            difficulty=difficulty,
            model_used=model,
            response=response_text,
            routed_cost=routed_cost,
            baseline_cost=baseline_cost,
        )
        self.results.append(result)

        return result

    def get_summary(self) -> dict:
        """全結果を通じたコスト比較を集計する"""
        total_routed = sum(r.routed_cost for r in self.results)
        total_baseline = sum(r.baseline_cost for r in self.results)
        savings = total_baseline - total_routed
        savings_pct = (savings / total_baseline * 100) if total_baseline > 0 else 0
        easy_count = sum(1 for r in self.results if r.difficulty == "easy")
        hard_count = sum(1 for r in self.results if r.difficulty == "hard")

        return {
            "total_tasks": len(self.results),
            "easy_count": easy_count,
            "hard_count": hard_count,
            "total_routed_cost": total_routed,
            "total_baseline_cost": total_baseline,
            "savings": savings,
            "savings_pct": savings_pct,
        }


def _render_task_result(console: Console, result: TaskResult, index: int) -> None:
    """1件のタスク結果をルーティング情報とともに描画する"""
    model_label = "Flash" if result.model_used == MODEL_EASY else "Pro"
    diff_color = "green" if result.difficulty == "easy" else "yellow"
    savings = result.baseline_cost - result.routed_cost
    savings_pct = (savings / result.baseline_cost * 100) if result.baseline_cost > 0 else 0

    console.print(
        Panel(
            f"[dim]Task:[/dim] {result.task}\n"
            f"[dim]Difficulty:[/dim] [{diff_color}]{result.difficulty}[/{diff_color}] → "
            f"[bold]{model_label}[/bold]\n"
            f"[dim]Routed cost:[/dim] [green]${result.routed_cost:.6f}[/green]  "
            f"[dim]Baseline (Pro):[/dim] [red]${result.baseline_cost:.6f}[/red]  "
            f"[dim]Saved:[/dim] [bold green]${savings:.6f} ({savings_pct:.0f}%)[/bold green]",
            title=f"Task {index}",
            border_style="dim",
            padding=(0, 1),
        )
    )


def _render_summary(console: Console, summary: dict) -> None:
    """集計されたコストサマリーを描画する"""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Metric", style="dim", min_width=22)
    table.add_column("Value", justify="right")

    table.add_row("Tasks processed", f"[cyan]{summary['total_tasks']}[/cyan]")
    table.add_row(
        "Routing breakdown",
        f"[green]{summary['easy_count']} easy[/green] / "
        f"[yellow]{summary['hard_count']} hard[/yellow]",
    )
    table.add_row(
        "Cost (routed)",
        f"[green]${summary['total_routed_cost']:.6f}[/green]",
    )
    table.add_row(
        "Cost (all-Pro baseline)",
        f"[red]${summary['total_baseline_cost']:.6f}[/red]",
    )
    table.add_row(
        "Total savings",
        f"[bold green]${summary['savings']:.6f} ({summary['savings_pct']:.1f}%)[/bold green]",
    )

    console.print(
        Panel(
            table,
            title="Cost Summary — Routed vs All-Pro",
            border_style="green",
            padding=(0, 1),
        )
    )


def _run_demo(console: Console, router: ModelRouter) -> None:
    """全サンプルタスクを実行し、結果を表示する"""
    console.print(f"\n[bold]Running {len(SAMPLE_TASKS)} sample tasks...[/bold]\n")

    for i, task in enumerate(SAMPLE_TASKS, 1):
        console.print(f"[dim]Processing task {i}/{len(SAMPLE_TASKS)}...[/dim]")
        try:
            result = router.route_and_execute(task)
            _render_task_result(console, result, i)
            # 応答を切り詰めて表示
            preview = (
                result.response[:200] + "..." if len(result.response) > 200 else result.response
            )
            console.print(Markdown(preview))
            console.print()
        except Exception as e:
            logger.error("Error processing task %d: %s", i, e)
            console.print(f"[red]Error: {e}[/red]\n")

    _render_summary(console, router.get_summary())


def _run_interactive(console: Console, router: ModelRouter) -> None:
    """対話モード——ユーザーがタスクを入力し、リアルタイムでルーティング判断を確認する"""
    console.print(
        "\n[bold]Interactive mode[/bold] — タスクを入力してルーティング判断を確認します。\n"
        "[bold]'summary'[/bold] でコスト合計を表示、[bold]'quit'[/bold] で終了します。\n"
    )

    while True:
        console.print("[bold green]Task:[/bold green] ", end="")
        user_input = input().strip()

        if user_input.lower() in ["quit", "exit", ""]:
            break

        if user_input.lower() == "summary":
            if router.results:
                _render_summary(console, router.get_summary())
            else:
                console.print("[dim]No tasks processed yet.[/dim]")
            continue

        try:
            result = router.route_and_execute(user_input)
            _render_task_result(console, result, len(router.results))

            console.print("\n[bold blue]Response:[/bold blue]")
            console.print(Markdown(result.response))
            console.print()

        except Exception as e:
            logger.error("Error processing task: %s", e)
            console.print(f"\n[red]Error: {e}[/red]")

    if router.results:
        _render_summary(console, router.get_summary())


def main() -> None:
    """モデルルーティングのデモ用メインオーケストレーション関数。"""
    console = Console()
    token_tracker = OpenRouterTokenTracker()
    router = ModelRouter(token_tracker)

    header = Panel(
        "[bold cyan]Model Routing Demo[/bold cyan]\n\n"
        "安価な分類器（Flash）が各タスクの難易度を評価し、\n"
        "[green]Flash[/green]（簡単）または[yellow]Pro[/yellow]（難しい）にルーティングします。\n\n"
        "FlashはProよりinputで約91%安価です——単純なタスクを安価なモデルに\n"
        "逃がすことで、規模が大きくなるほど実際のコスト削減につながります。\n\n"
        "[bold]料金:[/bold]\n"
        f"  Flash: ${PRICING['easy_input'] * 1_000_000:.2f} input / "
        f"${PRICING['easy_output'] * 1_000_000:.2f} output  (per MTok)\n"
        f"  Pro:   ${PRICING['hard_input'] * 1_000_000:.2f} input / "
        f"${PRICING['hard_output'] * 1_000_000:.2f} output (per MTok)",
        title="Smart Model Routing",
    )

    mode = interactive_menu(
        console,
        items=[
            "Demo — サンプルタスクを自動ルーティングで実行",
            "Interactive — 自分でタスクを入力",
        ],
        title="Select Mode",
        header=header,
    )

    if mode is None:
        return

    if mode.startswith("Demo"):
        _run_demo(console, router)
    else:
        _run_interactive(console, router)

    # 最終的なトークンレポート
    console.print()
    token_tracker.report()


if __name__ == "__main__":
    main()
