"""
Eval Harness — 集大成 (OpenRouter)

ユニットテストのパターン、evals、トレーシング、レッドチーミング、ベンチマークを
組み合わせ、リサーチアシスタントエージェント向けの統一されたハーネスにまとめた
完全な評価パイプライン。

この集大成はModule 05の5つの技法をすべて統合する:
1. 依存性注入によるテスト可能なエージェント設計
2. コードベースおよび複合採点によるゴールデンデータセット
3. 評価結果に紐づく実行トレーシング
4. 敵対的な安全性テストスイート
5. パレート分析によるモデルベンチマーク

2つのモードに対応する:
- シミュレーション（デフォルト）: 事前定義済みの応答、API呼び出しなし、即座に結果が出る
- ライブ: OpenRouter経由の実際のAPI呼び出しとツール使用エージェントループ
"""

import json
import os
from pathlib import Path
from typing import Any

from common import OpenRouterTokenTracker, interactive_menu, setup_logging
from dotenv import find_dotenv, load_dotenv
from rich.console import Console
from rich.panel import Panel

from eval_harness_openrouter import (
    BenchmarkRunner,
    CompositeGrader,
    EvalReport,
    EvalResult,
    EvalTask,
    EvalTrial,
    ResearchAgent,
    SafetyTester,
    SimulatedResearchAgent,
)
from eval_harness_openrouter.red_team import load_adversarial_tasks
from eval_harness_openrouter.reporter import EvalReporter
from eval_harness_openrouter.tracer import SimpleTracer

load_dotenv(find_dotenv())

logger = setup_logging(__name__)


MODE_OPTIONS = [
    "シミュレーション — 事前定義済みの応答、API呼び出しなし",
    "ライブ — OpenRouter経由の実際のAPI呼び出し",
]

AVAILABLE_MODELS = [
    "deepseek/deepseek-v4-flash-0731",
    "deepseek/deepseek-v4-pro-0813",
    "z-ai/glm-5.3-flash",
]


def select_mode_and_create_agent(console: Console, header: Panel) -> Any:
    """インタラクティブなモード・モデル選択を行い、設定済みのエージェントを返す。"""
    mode = interactive_menu(console, MODE_OPTIONS, title="実行モードを選択", header=header)
    if mode is None:
        raise SystemExit(0)

    if mode.startswith("ライブ"):
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            console.print("[red bold]ライブモードにはOPENROUTER_API_KEYの設定が必要です[/red bold]")
            raise SystemExit(1)

        model = interactive_menu(console, AVAILABLE_MODELS, title="モデルを選択", header=header)
        if model is None:
            raise SystemExit(0)

        from openrouter import OpenRouter

        client = OpenRouter(api_key=api_key)
        console.print(
            f"\n[green bold]LIVEモードで実行中[/green bold] — {model}への実際のAPI呼び出し\n"
            "[dim]評価トライアルと安全性テストはライブエージェントを使用します。"
            "ベンチマークはシミュレーションのまま（複数モデル比較）です。[/dim]\n"
        )
        return ResearchAgent(client=client, model=model)

    console.print("\n[dim]SIMULATEDモードで実行中 — 事前定義済みの応答、API呼び出しなし。[/dim]\n")
    return SimulatedResearchAgent()


def load_golden_tasks(path: Path) -> list[EvalTask]:
    """ゴールデンデータセットのJSONファイルから評価タスクを読み込む。"""
    with Path.open(path, encoding="utf-8") as f:
        data = json.load(f)
    tasks = [EvalTask(**t) for t in data["tasks"]]
    logger.info("Loaded %d golden tasks (v%s)", len(tasks), data["version"])
    return tasks


def run_eval_trials(
    agent: ResearchAgent | SimulatedResearchAgent,
    tasks: list[EvalTask],
    tracer: SimpleTracer,
) -> list[EvalTrial]:
    """各タスクでエージェントを実行し、トレーシング付きでトライアルを収集する。"""
    trials: list[EvalTrial] = []

    for task in tasks:
        question_preview = task.question[:50] + "…" if len(task.question) > 50 else task.question
        logger.info("Evaluating task %s: %s", task.id, question_preview)

        # 評価の実行をトレースする
        span = tracer.start_span(f"eval_{task.id}", "eval_trial")

        response = agent.answer(task.question, task_id=task.id)

        tracer.end_span(span)

        trial = EvalTrial(
            task_id=task.id,
            answer=response["answer"],
            tool_calls=response.get("tool_calls", []),
            trace=tracer.get_spans()[-1:],
            latency_ms=response.get("latency_ms", 0.0),
            input_tokens=response.get("input_tokens", 0),
            output_tokens=response.get("output_tokens", 0),
        )
        trials.append(trial)

    return trials


def grade_trials(
    trials: list[EvalTrial],
    tasks: list[EvalTask],
    grader: CompositeGrader,
) -> list[EvalResult]:
    """すべてのトライアルを採点し、評価結果を生成する。"""
    task_map = {t.id: t for t in tasks}
    results: list[EvalResult] = []

    for trial in trials:
        task = task_map[trial.task_id]
        scores = grader.grade(trial, task)

        # 複合スコアに基づく合格率
        composite = next((s for s in scores if s.grader_name == "composite"), None)
        pass_rate = 1.0 if (composite and composite.passed) else 0.0
        avg_score = composite.score if composite else 0.0

        result = EvalResult(
            task_id=trial.task_id,
            trials=[trial],
            grader_scores=scores,
            pass_rate=pass_rate,
            avg_score=avg_score,
        )
        results.append(result)

    return results


def main() -> None:
    """完全な評価パイプラインを実行する。"""
    console = Console()
    token_tracker = OpenRouterTokenTracker()

    header = Panel(
        "[bold cyan]Eval Harness — 集大成[/bold cyan]\n\n"
        "以下を組み合わせた完全な評価パイプライン:\n"
        "  1. テスト可能なエージェント設計（依存性注入）\n"
        "  2. ゴールデンデータセットのevals（キーワード + 出典採点）\n"
        "  3. 実行トレーシング（結果に紐づくスパン）\n"
        "  4. 敵対的な安全性テスト（レッドチームスイート）\n"
        "  5. モデルベンチマーク（パレート分析）",
        title="チュートリアル 06 (OpenRouter)",
    )

    # インタラクティブなモード・モデル選択
    agent = select_mode_and_create_agent(console, header)

    # ステップ1: データセットを読み込む
    base_dir = Path(__file__).parent
    tasks = load_golden_tasks(base_dir / "datasets_openrouter" / "golden_tasks.json")
    adversarial_attacks = load_adversarial_tasks(
        base_dir / "datasets_openrouter" / "adversarial_tasks.json"
    )
    console.print(
        f"[bold]ステップ1:[/bold] {len(tasks)}件のタスク、"
        f"{len(adversarial_attacks)}件の攻撃を読み込みました\n"
    )

    # ステップ2: コンポーネントを初期化する
    tracer = SimpleTracer()
    grader = CompositeGrader(keyword_weight=0.5, citation_weight=0.5)
    console.print("[bold]ステップ2:[/bold] トレーサーとグレーダーを初期化しました\n")

    # ステップ3: トレーシング付きで評価トライアルを実行する
    console.print("[bold]ステップ3:[/bold] 評価トライアルを実行中...\n")
    trials = run_eval_trials(agent, tasks, tracer)
    logger.info("Completed %d trials, %d spans collected", len(trials), tracer.get_span_count())

    # ステップ4: 複合グレーダーで採点する
    console.print("[bold]ステップ4:[/bold] 応答を採点中...\n")
    eval_results = grade_trials(trials, tasks, grader)

    # ステップ5: 安全性テストを実行する
    console.print("[bold]ステップ5:[/bold] 安全性テストを実行中...\n")
    safety_tester = SafetyTester()
    safety_results = safety_tester.run_safety_suite(agent, adversarial_attacks)

    # ステップ6: ベンチマークを実行する（シミュレーション）
    console.print("[bold]ステップ6:[/bold] ベンチマークを実行中...\n")
    benchmark_runner = BenchmarkRunner()
    # 出力を簡潔に保つため、ベンチマークにはタスクのサブセットを使う
    benchmark_tasks = tasks[:5]
    benchmark_entries = benchmark_runner.run_benchmark(benchmark_tasks)
    pareto_configs = benchmark_runner.find_pareto_optimal(benchmark_entries)

    # ステップ7: レポートを組み立てて表示する
    total_latency = sum(t.latency_ms for t in trials)
    total_cost = sum(e.cost_usd for e in benchmark_entries)
    passed_count = sum(1 for r in eval_results if r.pass_rate >= 0.5)
    blocked_count = sum(1 for r in safety_results if r.blocked)

    report = EvalReport(
        agent_name="Research Assistant",
        eval_results=eval_results,
        safety_results=safety_results,
        benchmark_entries=benchmark_entries,
        overall_pass_rate=passed_count / len(eval_results) if eval_results else 0.0,
        overall_safety_score=blocked_count / len(safety_results) if safety_results else 0.0,
        total_cost_usd=total_cost,
        total_latency_ms=total_latency,
    )

    console.print("[bold]ステップ7:[/bold] レポートを生成中...\n")
    reporter = EvalReporter(console)
    reporter.print_report(report)

    # パレート分析のサマリー
    console.print(
        Panel(
            f"[bold]パレート最適な設定:[/bold] {', '.join(pareto_configs)}\n\n"
            "これらの設定は、他のどの設定によっても\n"
            "（正確性・レイテンシ・コストの）いずれの軸でも劣後していません。",
            title="パレート分析",
        )
    )

    # トレースサマリー
    console.print(
        f"\n[dim]トレース: {tracer.get_span_count()}スパン、"
        f"合計{tracer.get_total_duration_ms():.0f}ms[/dim]"
    )

    token_tracker.report()


if __name__ == "__main__":
    main()
