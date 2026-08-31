"""
完全なベンチマークスイート (OpenRouter)

モデル比較とプロンプト比較を1つの設定マトリクスに統合する。すべての
モデル×プロンプトの組み合わせを実行し、集計統計を計算し、パレート分析
（非劣位な設定の特定）を行い、サマリーレポートを生成する。シミュレートされた
結果でも単体で動作する。
"""

import json
import os
import time
from typing import Any

from common import OpenRouterTokenTracker, setup_logging
from dotenv import find_dotenv, load_dotenv
from openrouter import OpenRouter
from openrouter.components import ChatResult
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from shared_openrouter.knowledge_base import (
    BENCHMARK_TASKS,
    TOOLS,
    score_answer,
    search_knowledge_base,
)
from shared_openrouter.models import MODEL_CONFIGS, BenchmarkConfig, BenchmarkResult, ModelConfig

load_dotenv(find_dotenv())

logger = setup_logging(__name__)

# ---------------------------------------------------------------------------
# プロンプト戦略
# ---------------------------------------------------------------------------

PROMPT_STRATEGIES = {
    "zero_shot": (
        "あなたはリサーチアシスタントです。ツール経由で提供された検索結果の"
        "情報のみを使って質問に答えてください。出典を引用してください。"
    ),
    "few_shot": (
        "あなたはリサーチアシスタントです。ツール経由で提供された検索結果の"
        "情報のみを使って質問に答えてください。出典を引用してください。\n\n"
        "例:\n"
        "質問: RESTとは何ですか？\n"
        "回答: REST（Representational State Transfer）はAPIのための"
        "アーキテクチャスタイルです（doc_002）。リソース指向の設計を採用し、"
        "アクションにHTTPメソッドを使用します。\n\n"
        "同じ形式でユーザーの質問に答えてください。"
    ),
    "chain_of_thought": (
        "あなたはリサーチアシスタントです。ツール経由で提供された検索結果の"
        "情報のみを使って質問に答えてください。出典を引用してください。\n\n"
        "段階的に考えてください:\n"
        "1. 関連するドキュメントを検索する\n"
        "2. 各ドキュメントから重要な事実を抽出する\n"
        "3. 包括的な回答を組み立てる\n"
        "4. 使用したすべての出典を引用する"
    ),
}

# ---------------------------------------------------------------------------
# デモモード用のシミュレートされた結果（3モデル×3プロンプト×5タスク=45件）
# ---------------------------------------------------------------------------


def _build_simulated_results() -> dict[str, list[BenchmarkResult]]:
    """設定マトリクス全体のシミュレートされた結果を構築する。"""
    # (config_name, task_id) -> (score, latency, in_tok, out_tok, cost)
    # コストは client.models.list() で取得した実測価格（2026年8月時点）から算出
    sim_data: dict[str, list[tuple[str, float, float, int, int, float]]] = {
        "DeepSeek V4 Pro + zero_shot": [
            ("bench_001", 0.8, 1100, 150, 180, 0.0004554),
            ("bench_002", 0.8, 1080, 145, 175, 0.0004422),
            ("bench_003", 0.7, 1150, 155, 185, 0.0004686),
            ("bench_004", 0.6, 1090, 148, 178, 0.00045012),
            ("bench_005", 0.7, 1120, 146, 176, 0.00044484),
        ],
        "DeepSeek V4 Pro + few_shot": [
            ("bench_001", 0.9, 1250, 190, 200, 0.0005214),
            ("bench_002", 1.0, 1230, 185, 195, 0.0005082),
            ("bench_003", 0.9, 1300, 195, 210, 0.0005445),
            ("bench_004", 0.8, 1240, 188, 198, 0.00051612),
            ("bench_005", 0.9, 1260, 186, 196, 0.00051084),
        ],
        "DeepSeek V4 Pro + chain_of_thought": [
            ("bench_001", 0.9, 1500, 200, 260, 0.0006468),
            ("bench_002", 1.0, 1480, 195, 255, 0.0006336),
            ("bench_003", 0.9, 1550, 205, 270, 0.0006699),
            ("bench_004", 1.0, 1490, 198, 258, 0.00064152),
            ("bench_005", 0.9, 1520, 196, 256, 0.00063624),
        ],
        "DeepSeek V4 Flash + zero_shot": [
            ("bench_001", 0.6, 420, 125, 130, 3.1525e-05),
            ("bench_002", 0.6, 410, 120, 125, 3.03e-05),
            ("bench_003", 0.5, 440, 128, 135, 3.262e-05),
            ("bench_004", 0.4, 415, 122, 128, 3.097e-05),
            ("bench_005", 0.5, 430, 124, 130, 3.146e-05),
        ],
        "DeepSeek V4 Flash + few_shot": [
            ("bench_001", 0.7, 480, 165, 155, 3.8625e-05),
            ("bench_002", 0.8, 470, 160, 150, 3.74e-05),
            ("bench_003", 0.7, 500, 168, 160, 3.972e-05),
            ("bench_004", 0.6, 475, 162, 152, 3.789e-05),
            ("bench_005", 0.7, 490, 164, 154, 3.838e-05),
        ],
        "DeepSeek V4 Flash + chain_of_thought": [
            ("bench_001", 0.8, 560, 175, 200, 4.7375e-05),
            ("bench_002", 0.8, 550, 170, 195, 4.615e-05),
            ("bench_003", 0.7, 580, 178, 210, 4.937e-05),
            ("bench_004", 0.7, 555, 172, 198, 4.682e-05),
            ("bench_005", 0.7, 570, 174, 202, 4.767e-05),
        ],
        "GLM 5.3 Flash + zero_shot": [
            ("bench_001", 0.7, 780, 130, 160, 4.975e-05),
            ("bench_002", 0.7, 760, 125, 155, 4.8125e-05),
            ("bench_003", 0.6, 800, 135, 165, 5.1375e-05),
            ("bench_004", 0.5, 770, 128, 158, 4.91e-05),
            ("bench_005", 0.6, 790, 126, 156, 4.845e-05),
        ],
        "GLM 5.3 Flash + few_shot": [
            ("bench_001", 0.8, 850, 170, 180, 5.775e-05),
            ("bench_002", 0.9, 840, 165, 175, 5.6125e-05),
            ("bench_003", 0.8, 880, 175, 185, 5.9375e-05),
            ("bench_004", 0.7, 845, 168, 178, 5.71e-05),
            ("bench_005", 0.8, 860, 166, 176, 5.645e-05),
        ],
        "GLM 5.3 Flash + chain_of_thought": [
            ("bench_001", 0.8, 1000, 180, 230, 7.1e-05),
            ("bench_002", 0.9, 980, 175, 225, 6.9375e-05),
            ("bench_003", 0.8, 1020, 185, 240, 7.3875e-05),
            ("bench_004", 0.8, 990, 178, 228, 7.035e-05),
            ("bench_005", 0.8, 1010, 176, 232, 7.12e-05),
        ],
    }

    results: dict[str, list[BenchmarkResult]] = {}
    for config_name, tasks in sim_data.items():
        results[config_name] = [
            BenchmarkResult(
                task_id=tid,
                config_name=config_name,
                answer=f"{config_name}による{tid}のシミュレートされた回答。",
                keyword_score=score,
                latency_ms=lat,
                input_tokens=inp,
                output_tokens=out,
                cost_usd=cost,
                tool_calls=1,
            )
            for tid, score, lat, inp, out, cost in tasks
        ]
    return results


SIMULATED_SUITE_RESULTS = _build_simulated_results()

# ---------------------------------------------------------------------------
# ベンチマークスイートクラス
# ---------------------------------------------------------------------------


class BenchmarkSuite:
    """設定マトリクスとパレート分析を備えた、完全なベンチマークスイート。"""

    def __init__(self) -> None:
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.token_tracker = OpenRouterTokenTracker()

    def build_config_matrix(
        self, models: list[ModelConfig], prompts: dict[str, str]
    ) -> list[BenchmarkConfig]:
        """すべてのモデル×プロンプトの組み合わせを構築する。"""
        configs: list[BenchmarkConfig] = []
        for model in models:
            for prompt_name, system_prompt in prompts.items():
                name = f"{model.name} + {prompt_name}"
                configs.append(BenchmarkConfig(name, model, prompt_name, system_prompt))
        logger.info(
            "Built %d configurations (%d models x %d prompts)",
            len(configs),
            len(models),
            len(prompts),
        )
        return configs

    def _run_task(self, task: dict, config: BenchmarkConfig) -> BenchmarkResult:
        """OpenRouter経由でタスクを実行する。

        OpenRouterでは全モデルが同じAPI形式でアクセスできるため、元のコードに
        あったAnthropic用・OpenAI用の別々のメソッド（_run_anthropic/_run_openai）は
        1つに統合されている。
        """
        messages: list[dict[str, Any]] = [{"role": "user", "content": task["question"]}]
        tool_call_count = 0

        start = time.perf_counter()

        # reasoning={"effort": "none"}は一部のモデル（z-ai/glm-5.3-flash等）が
        # 拒否するため指定しない。代わりにmax_tokensを余裕を持った値にする
        while True:
            response: ChatResult = self.client.chat.send(
                model=config.model.model_id,
                max_tokens=2048,
                messages=[  # type: ignore[arg-type]
                    {"role": "system", "content": config.system_prompt},
                    *messages,
                ],
                tools=TOOLS,  # type: ignore[arg-type]
            )
            assert response.usage is not None
            self.token_tracker.track(response.usage)

            message = response.choices[0].message
            finish_reason = response.choices[0].finish_reason
            tool_calls = message.tool_calls or []

            assistant_message: dict[str, Any] = {"role": "assistant", "content": message.content}
            if tool_calls:
                assistant_message["tool_calls"] = [
                    {
                        "id": tool_call.id,
                        "type": tool_call.type,
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        },
                    }
                    for tool_call in tool_calls
                ]
            messages.append(assistant_message)

            if finish_reason != "tool_calls" or not tool_calls:
                answer = str(message.content or "")
                break

            for tool_call in tool_calls:
                tool_call_count += 1
                args = json.loads(tool_call.function.arguments)
                result = search_knowledge_base(**args)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        latency_ms = (time.perf_counter() - start) * 1000
        inp = response.usage.prompt_tokens
        out = response.usage.completion_tokens
        cost = (
            inp * config.model.cost_per_input_token + out * config.model.cost_per_output_token
        ) / 1_000_000

        return BenchmarkResult(
            task_id=task["id"],
            config_name=config.name,
            answer=answer,
            keyword_score=score_answer(answer, task["expected_keywords"]),
            latency_ms=latency_ms,
            input_tokens=inp,
            output_tokens=out,
            cost_usd=cost,
            tool_calls=tool_call_count,
        )

    def run_suite(
        self,
        configs: list[BenchmarkConfig],
        tasks: list[dict],
        num_trials: int = 1,
    ) -> dict[str, list[BenchmarkResult]]:
        """すべての設定・タスクにわたって完全なベンチマークスイートを実行する。"""
        all_results: dict[str, list[BenchmarkResult]] = {}

        for config in configs:
            logger.info("Config: %s", config.name)
            config_results: list[BenchmarkResult] = []

            for trial in range(num_trials):
                for task in tasks:
                    logger.info("  Trial %d, Task %s", trial + 1, task["id"])
                    try:
                        result = self._run_task(task, config)
                        config_results.append(result)
                    except Exception as e:
                        logger.error("    Error: %s", e)

            all_results[config.name] = config_results

        return all_results

    def compute_summary(self, results: dict[str, list[BenchmarkResult]]) -> list[dict[str, Any]]:
        """設定ごとの集計統計を計算する。"""
        summaries: list[dict[str, Any]] = []
        for config_name, res_list in results.items():
            n = len(res_list)
            if n == 0:
                continue
            summaries.append(
                {
                    "config": config_name,
                    "accuracy": sum(r.keyword_score for r in res_list) / n,
                    "avg_latency_ms": sum(r.latency_ms for r in res_list) / n,
                    "avg_tokens": sum(r.input_tokens + r.output_tokens for r in res_list) / n,
                    "avg_cost": sum(r.cost_usd for r in res_list) / n,
                    "total_cost": sum(r.cost_usd for r in res_list),
                    "tasks": n,
                }
            )
        return summaries

    def find_pareto_optimal(self, summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """パレート最適な設定を見つける（正確性とコストの両面で劣位でないもの）。"""
        pareto: list[dict[str, Any]] = []

        for candidate in summaries:
            dominated = False
            for other in summaries:
                if other["config"] == candidate["config"]:
                    continue
                # "other"がすべての次元で少なくとも同等以上で、かつ少なくとも1つの
                # 次元で明確に優れている場合、"other"は"candidate"を支配する
                better_or_equal_acc = other["accuracy"] >= candidate["accuracy"]
                better_or_equal_cost = other["avg_cost"] <= candidate["avg_cost"]
                strictly_better = (
                    other["accuracy"] > candidate["accuracy"]
                    or other["avg_cost"] < candidate["avg_cost"]
                )
                if better_or_equal_acc and better_or_equal_cost and strictly_better:
                    dominated = True
                    break

            if not dominated:
                pareto.append(candidate)

        logger.info(
            "Pareto-optimal: %d of %d configs",
            len(pareto),
            len(summaries),
        )
        return pareto

    def generate_report(self, summaries: list[dict[str, Any]], pareto: list[dict[str, Any]]) -> str:
        """ベンチマーク結果のテキストサマリーレポートを生成する。"""
        lines: list[str] = ["ベンチマークレポート", "=" * 60, ""]

        # 全体の統計
        lines.append(f"テストした設定数: {len(summaries)}")
        lines.append(f"パレート最適な設定数: {len(pareto)}")
        lines.append("")

        # 各観点でのベスト
        best_acc = max(summaries, key=lambda s: s["accuracy"])
        best_cost = min(summaries, key=lambda s: s["avg_cost"])
        best_lat = min(summaries, key=lambda s: s["avg_latency_ms"])
        lines.append("観点別のベスト:")
        lines.append(f"  正確性:     {best_acc['config']} ({best_acc['accuracy']:.0%})")
        lines.append(f"  コスト:     {best_cost['config']} (${best_cost['avg_cost']:.6f})")
        lines.append(f"  レイテンシ: {best_lat['config']} ({best_lat['avg_latency_ms']:.0f}ms)")
        lines.append("")

        # パレート集合
        lines.append("パレート最適な設定:")
        for p in pareto:
            lines.append(
                f"  {p['config']}: 正確性={p['accuracy']:.0%}, "
                f"コスト=${p['avg_cost']:.6f}, レイテンシ={p['avg_latency_ms']:.0f}ms"
            )
        lines.append("")

        # 推奨事項
        lines.append("推奨事項:")
        if pareto:
            cheapest_pareto = min(pareto, key=lambda p: p["avg_cost"])
            best_pareto = max(pareto, key=lambda p: p["accuracy"])
            lines.append(f"  予算重視:   {cheapest_pareto['config']}")
            lines.append(f"  品質重視:   {best_pareto['config']}")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


def main() -> None:
    """マトリクス比較とパレート分析を含む、完全なベンチマークスイートを実行する。"""
    console = Console()
    console.print(
        Panel(
            "[bold cyan]完全なベンチマークスイート[/bold cyan]\n\n"
            "モデル×プロンプトの設定マトリクスをパレート分析とともに実行します。\n"
            "正確性とコストの両面で劣位でない設定を特定します。",
            title="ベンチマークチュートリアル3",
        )
    )

    # モードを決定する
    has_api_key = bool(os.environ.get("OPENROUTER_API_KEY"))

    suite = BenchmarkSuite()
    configs = suite.build_config_matrix(MODEL_CONFIGS, PROMPT_STRATEGIES)

    console.print(
        f"設定マトリクス: {len(MODEL_CONFIGS)}モデル × "
        f"{len(PROMPT_STRATEGIES)}プロンプト = {len(configs)}設定\n"
    )

    if has_api_key:
        console.print(
            "[green]APIキーが見つかりました — ライブベンチマークスイートを実行します[/green]\n"
        )
        results = suite.run_suite(configs, BENCHMARK_TASKS)
    else:
        console.print(
            "[yellow]APIキーが見つかりません — デモ用にシミュレートされた結果を使用します[/yellow]\n"
        )
        results = SIMULATED_SUITE_RESULTS

    # サマリーを計算する
    summaries = suite.compute_summary(results)
    pareto = suite.find_pareto_optimal(summaries)

    # 全マトリクスの結果テーブル
    matrix_table = Table(title="設定マトリクスの結果", show_lines=True)
    matrix_table.add_column("設定", style="bold", width=32)
    matrix_table.add_column("正確性", justify="center", width=10)
    matrix_table.add_column("平均レイテンシ", justify="right", width=14)
    matrix_table.add_column("平均トークン", justify="right", width=12)
    matrix_table.add_column("平均コスト", justify="right", width=12)
    matrix_table.add_column("パレート", justify="center", width=8)

    pareto_names = {p["config"] for p in pareto}
    for s in summaries:
        acc = s["accuracy"]
        acc_color = "green" if acc >= 0.8 else ("yellow" if acc >= 0.6 else "red")
        is_pareto = "yes" if s["config"] in pareto_names else ""
        pareto_style = "[bold green]yes[/bold green]" if is_pareto else "[dim]-[/dim]"
        matrix_table.add_row(
            s["config"],
            f"[{acc_color}]{acc:.0%}[/{acc_color}]",
            f"{s['avg_latency_ms']:.0f}ms",
            f"{s['avg_tokens']:.0f}",
            f"${s['avg_cost']:.6f}",
            pareto_style,
        )

    console.print(matrix_table)
    console.print()

    # パレート最適な設定のパネル
    pareto_lines: list[str] = []
    for p in pareto:
        pareto_lines.append(
            f"  [bold]{p['config']}[/bold]: "
            f"正確性={p['accuracy']:.0%}, "
            f"コスト=${p['avg_cost']:.6f}, "
            f"レイテンシ={p['avg_latency_ms']:.0f}ms"
        )
    console.print(
        Panel(
            "\n".join(pareto_lines)
            if pareto_lines
            else "パレート最適な設定は見つかりませんでした。",
            title="パレート最適な設定",
            subtitle="正確性とコストの両面で劣位でないもの",
        )
    )

    # 正確性 vs コストの散布図（テキストベース）
    console.print("\n[bold]正確性 vs コスト（テキストプロット）[/bold]")
    sorted_by_cost = sorted(summaries, key=lambda s: s["avg_cost"])
    for s in sorted_by_cost:
        bar_len = int(s["accuracy"] * 30)
        bar = "#" * bar_len + "." * (30 - bar_len)
        pareto_marker = " *" if s["config"] in pareto_names else ""
        console.print(
            f"  ${s['avg_cost']:.6f} |{bar}| {s['accuracy']:.0%}  "
            f"[dim]{s['config']}[/dim]{pareto_marker}"
        )
    console.print("  [dim](* = パレート最適)[/dim]")

    # レポート
    report = suite.generate_report(summaries, pareto)
    console.print()
    console.print(Panel(report, title="ベンチマークレポート"))

    # トークン使用量（ライブモードのみ）
    if has_api_key:
        console.print()
        suite.token_tracker.report()


if __name__ == "__main__":
    main()
