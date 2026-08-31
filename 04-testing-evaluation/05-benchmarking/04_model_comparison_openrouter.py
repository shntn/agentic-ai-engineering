"""
モデル比較ベンチマーク (OpenRouter)

同じリサーチアシスタントタスクを複数のモデルにわたってベンチマークする。
正確性（キーワードマッチング）・レイテンシ・トークン使用量・クエリごとのコストを
測定する。ライブAPI呼び出しと、APIキーなしでのデモ用シミュレーションモードの
両方に対応する。
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
    SYSTEM_PROMPT,
    TOOLS,
    score_answer,
    search_knowledge_base,
)
from shared_openrouter.models import MODEL_CONFIGS, BenchmarkResult, ModelConfig

load_dotenv(find_dotenv())

logger = setup_logging(__name__)

# ---------------------------------------------------------------------------
# デモモード用のシミュレートされた結果
# ---------------------------------------------------------------------------

SIMULATED_RESULTS = [
    # bench_001 — マイクロサービス
    BenchmarkResult(
        "bench_001",
        "DeepSeek V4 Pro",
        "マイクロサービスはスケーラビリティ、障害分離、そして独立したデプロイを提供します（doc_001）。",
        0.9,
        1200,
        150,
        200,
        0.000495,
        1,
    ),
    BenchmarkResult(
        "bench_001",
        "DeepSeek V4 Flash",
        "利点には障害分離とスケーラビリティが含まれます（doc_001）。",
        0.7,
        450,
        130,
        150,
        0.00003545,
        1,
    ),
    BenchmarkResult(
        "bench_001",
        "GLM 5.3 Flash",
        "主な利点はスケーラビリティ、障害分離、独立したサービスです（doc_001）。",
        0.8,
        800,
        140,
        180,
        0.0000555,
        1,
    ),
    # bench_002 — REST API
    BenchmarkResult(
        "bench_002",
        "DeepSeek V4 Pro",
        (
            "REST APIはエンドポイントに名詞を使い、アクションにHTTPメソッドを使い、"
            "結果にステータスコードを使います（doc_002）。"
        ),
        1.0,
        1150,
        145,
        190,
        0.0004719,
        1,
    ),
    BenchmarkResult(
        "bench_002",
        "DeepSeek V4 Flash",
        "名詞とHTTPメソッドを適切なステータスコードとともに使用します（doc_002）。",
        0.8,
        420,
        125,
        140,
        0.00003333,
        1,
    ),
    BenchmarkResult(
        "bench_002",
        "GLM 5.3 Flash",
        "エンドポイントは名詞で設計し、HTTPメソッドとステータスコードを使用します（doc_002）。",
        0.9,
        780,
        135,
        170,
        0.0000526,
        1,
    ),
    # bench_003 — データベースインデックス
    BenchmarkResult(
        "bench_003",
        "DeepSeek V4 Pro",
        (
            "B-treeインデックスは等価検索を処理し、複合インデックスは複数列の"
            "クエリのパフォーマンスをサポートします（doc_003）。"
        ),
        0.9,
        1300,
        155,
        210,
        0.0005181,
        1,
    ),
    BenchmarkResult(
        "bench_003",
        "DeepSeek V4 Flash",
        "データベースインデックスはB-tree構造を使ってクエリのパフォーマンスを向上させます（doc_003）。",
        0.6,
        440,
        128,
        145,
        0.0000344,
        1,
    ),
    BenchmarkResult(
        "bench_003",
        "GLM 5.3 Flash",
        "B-treeと複合インデックスがクエリのパフォーマンスを向上させます（doc_003）。",
        0.8,
        820,
        138,
        175,
        0.0000541,
        1,
    ),
    # bench_004 — 認証
    BenchmarkResult(
        "bench_004",
        "DeepSeek V4 Pro",
        (
            "認証は本人確認を行い、認可はアクセスを制御します。JWTとOAuth 2.0が"
            "主要なメカニズムです（doc_004）。"
        ),
        0.9,
        1250,
        148,
        205,
        0.0005037,
        1,
    ),
    BenchmarkResult(
        "bench_004",
        "DeepSeek V4 Flash",
        "認証は本人確認、認可はアクセスです。JWTトークンを使用します（doc_004）。",
        0.6,
        430,
        122,
        138,
        0.0000328,
        1,
    ),
    BenchmarkResult(
        "bench_004",
        "GLM 5.3 Flash",
        "認証は本人確認を検証し、認可はJWTとOAuthによるアクセスを制御します（doc_004）。",
        0.8,
        810,
        132,
        172,
        0.0000529,
        1,
    ),
    # bench_005 — CI/CD
    BenchmarkResult(
        "bench_005",
        "DeepSeek V4 Pro",
        (
            "CIは自動化されたテストと高速なフィードバックループによる継続的"
            "インテグレーションを提供します（doc_005）。"
        ),
        0.8,
        1180,
        142,
        195,
        0.0004798,
        1,
    ),
    BenchmarkResult(
        "bench_005",
        "DeepSeek V4 Flash",
        "自動化されたビルドと高速なフィードバックを伴う継続的インテグレーション（doc_005）。",
        0.7,
        460,
        126,
        142,
        0.0000338,
        1,
    ),
    BenchmarkResult(
        "bench_005",
        "GLM 5.3 Flash",
        "主なCI/CDの実践には継続的な自動テストとフィードバックループが含まれます（doc_005）。",
        0.7,
        790,
        130,
        168,
        0.0000518,
        1,
    ),
]

# ---------------------------------------------------------------------------
# モデルベンチマーククラス
# ---------------------------------------------------------------------------


class ModelBenchmark:
    """同じタスクを複数のモデルにわたってベンチマークする。"""

    def __init__(self) -> None:
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.token_tracker = OpenRouterTokenTracker()

    def run_task(self, task: dict, config: ModelConfig) -> BenchmarkResult:
        """OpenRouter経由で単一のベンチマークタスクを実行する。

        OpenRouterでは全モデルが同じAPI形式でアクセスできるため、元のコードに
        あったAnthropic用・OpenAI用の別々のメソッドは1つに統合されている。
        """
        messages: list[dict[str, Any]] = [{"role": "user", "content": task["question"]}]
        tool_call_count = 0

        start = time.perf_counter()

        # エージェントループ——最終応答が得られるまでツール使用を処理する
        # reasoning={"effort": "none"}は一部のモデル（z-ai/glm-5.3-flash等）が
        # 拒否するため指定しない。代わりにmax_tokensを余裕を持った値にし、
        # 思考モデルでreasoningトークンがmax_tokensを食い尽くさないようにする
        while True:
            response: ChatResult = self.client.chat.send(
                model=config.model_id,
                max_tokens=2048,
                messages=[  # type: ignore[arg-type]
                    {"role": "system", "content": SYSTEM_PROMPT},
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

            # ツール呼び出しを処理する
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
        input_tok = response.usage.prompt_tokens
        output_tok = response.usage.completion_tokens
        cost = (
            input_tok * config.cost_per_input_token + output_tok * config.cost_per_output_token
        ) / 1_000_000

        return BenchmarkResult(
            task_id=task["id"],
            config_name=config.name,
            answer=answer,
            keyword_score=score_answer(answer, task["expected_keywords"]),
            latency_ms=latency_ms,
            input_tokens=input_tok,
            output_tokens=output_tok,
            cost_usd=cost,
            tool_calls=tool_call_count,
        )

    def run_benchmark(self, tasks: list[dict], configs: list[ModelConfig]) -> list[BenchmarkResult]:
        """すべてのモデル設定にわたってすべてのタスクを実行する。"""
        results: list[BenchmarkResult] = []
        for config in configs:
            logger.info("Benchmarking model: %s (%s)", config.name, config.model_id)
            for task in tasks:
                logger.info("  Task %s: %s", task["id"], task["question"][:50])
                try:
                    result = self.run_task(task, config)
                    results.append(result)
                    logger.info(
                        "    score=%.2f, latency=%dms, cost=$%.6f",
                        result.keyword_score,
                        result.latency_ms,
                        result.cost_usd,
                    )
                except Exception as e:
                    logger.error("    Error: %s", e)
        return results


# ---------------------------------------------------------------------------
# 集計ヘルパー
# ---------------------------------------------------------------------------


def aggregate_by_model(results: list[BenchmarkResult]) -> dict[str, dict[str, float]]:
    """全タスクにわたるモデルごとの平均値を計算する。"""
    model_results: dict[str, list[BenchmarkResult]] = {}
    for r in results:
        model_results.setdefault(r.config_name, []).append(r)

    summaries: dict[str, dict[str, float]] = {}
    for model, mrs in model_results.items():
        n = len(mrs)
        summaries[model] = {
            "accuracy": sum(r.keyword_score for r in mrs) / n,
            "avg_latency_ms": sum(r.latency_ms for r in mrs) / n,
            "avg_tokens": sum(r.input_tokens + r.output_tokens for r in mrs) / n,
            "avg_cost": sum(r.cost_usd for r in mrs) / n,
            "tasks": n,
        }
    return summaries


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


def main() -> None:
    """モデル比較ベンチマークを実行し、結果を表示する。"""
    console = Console()
    console.print(
        Panel(
            "[bold cyan]モデル比較ベンチマーク[/bold cyan]\n\n"
            "同じリサーチアシスタントタスクを複数のモデルにわたって比較します。\n"
            "測定項目: 正確性（キーワードマッチ）・レイテンシ・トークン使用量・コスト。",
            title="ベンチマークチュートリアル1",
        )
    )

    # モードを決定する: ライブかシミュレーションか
    has_api_key = bool(os.environ.get("OPENROUTER_API_KEY"))

    if has_api_key:
        console.print("[green]APIキーが見つかりました — ライブベンチマークを実行します[/green]\n")
        benchmark = ModelBenchmark()
        results = benchmark.run_benchmark(BENCHMARK_TASKS, MODEL_CONFIGS)
    else:
        console.print(
            "[yellow]APIキーが見つかりません — デモ用にシミュレートされた結果を使用します[/yellow]\n"
        )
        results = SIMULATED_RESULTS

    # タスクごとの詳細テーブル
    detail_table = Table(title="タスクごとの結果", show_lines=True)
    detail_table.add_column("タスク", style="cyan", width=10)
    detail_table.add_column("モデル", width=18)
    detail_table.add_column("スコア", justify="center", width=7)
    detail_table.add_column("レイテンシ", justify="right", width=9)
    detail_table.add_column("トークン", justify="right", width=8)
    detail_table.add_column("コスト", justify="right", width=11)
    detail_table.add_column("ツール", justify="center", width=6)

    for r in results:
        score_color = (
            "green" if r.keyword_score >= 0.8 else ("yellow" if r.keyword_score >= 0.5 else "red")
        )
        detail_table.add_row(
            r.task_id,
            r.config_name,
            f"[{score_color}]{r.keyword_score:.0%}[/{score_color}]",
            f"{r.latency_ms:.0f}ms",
            str(r.input_tokens + r.output_tokens),
            f"${r.cost_usd:.6f}",
            str(r.tool_calls),
        )

    console.print(detail_table)
    console.print()

    # 集計比較テーブル
    summaries = aggregate_by_model(results)

    summary_table = Table(title="モデル比較サマリー", show_lines=True)
    summary_table.add_column("モデル", style="bold", width=18)
    summary_table.add_column("正確性", justify="center", width=10)
    summary_table.add_column("平均レイテンシ", justify="right", width=14)
    summary_table.add_column("平均トークン", justify="right", width=12)
    summary_table.add_column("平均コスト", justify="right", width=12)

    for model, stats in summaries.items():
        acc = stats["accuracy"]
        acc_color = "green" if acc >= 0.8 else ("yellow" if acc >= 0.6 else "red")
        summary_table.add_row(
            model,
            f"[{acc_color}]{acc:.0%}[/{acc_color}]",
            f"{stats['avg_latency_ms']:.0f}ms",
            f"{stats['avg_tokens']:.0f}",
            f"${stats['avg_cost']:.6f}",
        )

    console.print(summary_table)

    # 観点別のベストモデルをハイライトする
    console.print("\n[bold]観点別のベストモデル[/bold]")
    best_acc = max(summaries.items(), key=lambda x: x[1]["accuracy"])
    best_lat = min(summaries.items(), key=lambda x: x[1]["avg_latency_ms"])
    best_cost = min(summaries.items(), key=lambda x: x[1]["avg_cost"])
    console.print(f"  正確性:     {best_acc[0]} ({best_acc[1]['accuracy']:.0%})")
    console.print(f"  レイテンシ: {best_lat[0]} ({best_lat[1]['avg_latency_ms']:.0f}ms)")
    console.print(f"  コスト:     {best_cost[0]} (${best_cost[1]['avg_cost']:.6f})")

    # トークン使用量レポート（ライブモードのみ）
    if has_api_key:
        console.print()
        benchmark.token_tracker.report()


if __name__ == "__main__":
    main()
