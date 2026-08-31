"""
プロンプト戦略比較ベンチマーク (OpenRouter)

同じモデル・同じタスクに対して3つのプロンプト戦略（zero-shot・few-shot・
chain-of-thought）をベンチマークする。プロンプトエンジニアリングが正確性・
冗長さ・コストにどう影響するかを測定する。ライブAPI呼び出しと、APIキーなしでの
デモ用シミュレーションモードの両方に対応する。
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
from shared_openrouter.models import BenchmarkResult

load_dotenv(find_dotenv())

logger = setup_logging(__name__)

# ---------------------------------------------------------------------------
# プロンプト戦略 — テスト対象となる中心的な変数
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

# プロンプト比較用のデフォルトモデル——プロンプトという変数だけを切り分ける
DEFAULT_MODEL = "deepseek/deepseek-v4-flash-0731"
# client.models.list() で取得した実測値（2026年8月時点）
COST_PER_INPUT = 0.065  # 100万トークンあたりのドル
COST_PER_OUTPUT = 0.18  # 100万トークンあたりのドル

# ---------------------------------------------------------------------------
# デモモード用のシミュレートされた結果
# ---------------------------------------------------------------------------

SIMULATED_RESULTS: dict[str, list[BenchmarkResult]] = {
    "zero_shot": [
        BenchmarkResult(
            "bench_001",
            "zero_shot",
            "マイクロサービスはスケーラビリティと障害分離を提供します（doc_001）。",
            0.7,
            1100,
            140,
            120,
            0.0000307,
            1,
        ),
        BenchmarkResult(
            "bench_002",
            "zero_shot",
            "エンドポイントには名詞とHTTPメソッドを使用します（doc_002）。",
            0.7,
            1050,
            135,
            115,
            0.00002948,
            1,
        ),
        BenchmarkResult(
            "bench_003",
            "zero_shot",
            "B-treeインデックスがクエリのパフォーマンスを向上させます（doc_003）。",
            0.6,
            1120,
            142,
            118,
            0.00003047,
            1,
        ),
        BenchmarkResult(
            "bench_004",
            "zero_shot",
            "認証は本人確認、認可はアクセス制御です（doc_004）。",
            0.5,
            1080,
            138,
            122,
            0.00003093,
            1,
        ),
        BenchmarkResult(
            "bench_005",
            "zero_shot",
            "CI/CDには自動テストと継続的デプロイが含まれます（doc_005）。",
            0.6,
            1090,
            136,
            116,
            0.00002972,
            1,
        ),
    ],
    "few_shot": [
        BenchmarkResult(
            "bench_001",
            "few_shot",
            (
                "マイクロサービスアーキテクチャはスケーラビリティ、障害分離、"
                "独立したデプロイを提供します（doc_001）。"
            ),
            0.9,
            1250,
            185,
            160,
            0.00004083,
            1,
        ),
        BenchmarkResult(
            "bench_002",
            "few_shot",
            (
                "REST APIのエンドポイントは名詞を使い、アクションにHTTPメソッド、"
                "結果にステータスコードを使用します（doc_002）。"
            ),
            1.0,
            1200,
            180,
            155,
            0.0000396,
            1,
        ),
        BenchmarkResult(
            "bench_003",
            "few_shot",
            (
                "データベースインデックスはB-treeインデックスと複合インデックスを"
                "使い、クエリのパフォーマンスを向上させます（doc_003）。"
            ),
            0.9,
            1280,
            188,
            162,
            0.00004138,
            1,
        ),
        BenchmarkResult(
            "bench_004",
            "few_shot",
            ("認証は本人確認、認可はアクセス制御です。JWTとOAuth 2.0が使われます（doc_004）。"),
            0.8,
            1220,
            182,
            158,
            0.00004027,
            1,
        ),
        BenchmarkResult(
            "bench_005",
            "few_shot",
            (
                "主なCI/CDの実践には継続的インテグレーション、自動テスト、"
                "高速なフィードバックループが含まれます（doc_005）。"
            ),
            0.9,
            1240,
            184,
            156,
            0.00004004,
            1,
        ),
    ],
    "chain_of_thought": [
        BenchmarkResult(
            "bench_001",
            "chain_of_thought",
            (
                "ステップ1: マイクロサービスを検索。ステップ2: 重要な事実——"
                "スケーラビリティ、障害分離、独立したデプロイ。ステップ3: "
                "マイクロサービスは独立したスケーリングと障害分離を可能に"
                "します（doc_001）。"
            ),
            0.9,
            1500,
            195,
            250,
            0.00005768,
            1,
        ),
        BenchmarkResult(
            "bench_002",
            "chain_of_thought",
            (
                "ステップ1: REST APIを検索。ステップ2: エンドポイントには名詞、"
                "HTTPメソッド、ステータスコード。ステップ3: REST APIは名詞・"
                "HTTPメソッド・ステータスコードを使用すべきです（doc_002）。"
            ),
            1.0,
            1450,
            190,
            245,
            0.00005645,
            1,
        ),
        BenchmarkResult(
            "bench_003",
            "chain_of_thought",
            (
                "ステップ1: インデックスを検索。ステップ2: B-tree、複合、"
                "クエリのパフォーマンス。ステップ3: B-treeと複合インデックスは"
                "クエリのパフォーマンスを向上させます（doc_003）。"
            ),
            0.9,
            1520,
            198,
            255,
            0.00005877,
            1,
        ),
        BenchmarkResult(
            "bench_004",
            "chain_of_thought",
            (
                "ステップ1: 認証を検索。ステップ2: 本人確認、アクセス、JWT、"
                "OAuth。ステップ3: 認証は本人確認を行い、認可はJWTとOAuthを"
                "使ってアクセスを制御します（doc_004）。"
            ),
            1.0,
            1480,
            192,
            248,
            0.00005712,
            1,
        ),
        BenchmarkResult(
            "bench_005",
            "chain_of_thought",
            (
                "ステップ1: CI/CDを検索。ステップ2: 継続的、自動化、"
                "フィードバック。ステップ3: CI/CDは継続的な自動ビルドと"
                "高速なフィードバックループに依存します（doc_005）。"
            ),
            0.9,
            1510,
            196,
            252,
            0.0000581,
            1,
        ),
    ],
}

# ---------------------------------------------------------------------------
# プロンプトベンチマーククラス
# ---------------------------------------------------------------------------


class PromptBenchmark:
    """同じモデル・同じタスクで異なるプロンプト戦略をベンチマークする。"""

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self.model = model
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.token_tracker = OpenRouterTokenTracker()

    def run_with_prompt(self, task: dict, prompt_name: str, system_prompt: str) -> BenchmarkResult:
        """特定のプロンプト戦略で単一のタスクを実行する。"""
        messages: list[dict[str, Any]] = [{"role": "user", "content": task["question"]}]
        tool_call_count = 0

        start = time.perf_counter()

        # reasoning={"effort": "none"}は一部のモデルが拒否するため指定しない。
        # 代わりにmax_tokensを余裕を持った値にする
        while True:
            response: ChatResult = self.client.chat.send(
                model=self.model,
                max_tokens=2048,
                messages=[  # type: ignore[arg-type]
                    {"role": "system", "content": system_prompt},
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
        input_tok = response.usage.prompt_tokens
        output_tok = response.usage.completion_tokens
        cost = (input_tok * COST_PER_INPUT + output_tok * COST_PER_OUTPUT) / 1_000_000

        return BenchmarkResult(
            task_id=task["id"],
            config_name=prompt_name,
            answer=answer,
            keyword_score=score_answer(answer, task["expected_keywords"]),
            latency_ms=latency_ms,
            input_tokens=input_tok,
            output_tokens=output_tok,
            cost_usd=cost,
            tool_calls=tool_call_count,
        )

    def run_comparison(self, tasks: list[dict]) -> dict[str, list[BenchmarkResult]]:
        """各プロンプト戦略ですべてのタスクを実行する。"""
        all_results: dict[str, list[BenchmarkResult]] = {}

        for prompt_name, system_prompt in PROMPT_STRATEGIES.items():
            logger.info("Running prompt strategy: %s", prompt_name)
            strategy_results: list[BenchmarkResult] = []

            for task in tasks:
                logger.info("  Task %s: %s", task["id"], task["question"][:50])
                try:
                    result = self.run_with_prompt(task, prompt_name, system_prompt)
                    strategy_results.append(result)
                    logger.info(
                        "    score=%.2f, latency=%dms, tokens=%d",
                        result.keyword_score,
                        result.latency_ms,
                        result.input_tokens + result.output_tokens,
                    )
                except Exception as e:
                    logger.error("    Error: %s", e)

            all_results[prompt_name] = strategy_results

        return all_results


# ---------------------------------------------------------------------------
# 集計ヘルパー
# ---------------------------------------------------------------------------


def aggregate_by_strategy(
    results: dict[str, list[BenchmarkResult]],
) -> dict[str, dict[str, float]]:
    """戦略ごとの平均値を計算する。"""
    summaries: dict[str, dict[str, float]] = {}
    for strategy, res_list in results.items():
        n = len(res_list)
        if n == 0:
            continue
        summaries[strategy] = {
            "accuracy": sum(r.keyword_score for r in res_list) / n,
            "avg_latency_ms": sum(r.latency_ms for r in res_list) / n,
            "avg_output_tokens": sum(r.output_tokens for r in res_list) / n,
            "avg_cost": sum(r.cost_usd for r in res_list) / n,
            "tasks": n,
        }
    return summaries


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


def main() -> None:
    """プロンプト戦略比較を実行し、結果を表示する。"""
    console = Console()
    console.print(
        Panel(
            "[bold cyan]プロンプト戦略比較[/bold cyan]\n\n"
            "同じモデルで、3つのプロンプト戦略: zero-shot・few-shot・chain-of-thought。\n"
            "プロンプトエンジニアリングが正確性・冗長さ・コストにどう影響するかを測定します。",
            title="ベンチマークチュートリアル2",
        )
    )

    # モードを決定する
    has_api_key = bool(os.environ.get("OPENROUTER_API_KEY"))

    if has_api_key:
        console.print(
            f"[green]APIキーが見つかりました — {DEFAULT_MODEL}でライブベンチマークを実行します[/green]\n"
        )
        benchmark = PromptBenchmark()
        results = benchmark.run_comparison(BENCHMARK_TASKS)
    else:
        console.print(
            "[yellow]APIキーが見つかりません — デモ用にシミュレートされた結果を使用します[/yellow]\n"
        )
        results = SIMULATED_RESULTS

    # タスクごとの詳細テーブル
    detail_table = Table(title="プロンプト戦略別のタスクごとの結果", show_lines=True)
    detail_table.add_column("タスク", style="cyan", width=10)
    detail_table.add_column("戦略", width=16)
    detail_table.add_column("スコア", justify="center", width=7)
    detail_table.add_column("レイテンシ", justify="right", width=9)
    detail_table.add_column("出力トークン", justify="right", width=10)
    detail_table.add_column("コスト", justify="right", width=11)

    for strategy, res_list in results.items():
        for r in res_list:
            score_color = (
                "green"
                if r.keyword_score >= 0.8
                else ("yellow" if r.keyword_score >= 0.5 else "red")
            )
            detail_table.add_row(
                r.task_id,
                strategy,
                f"[{score_color}]{r.keyword_score:.0%}[/{score_color}]",
                f"{r.latency_ms:.0f}ms",
                str(r.output_tokens),
                f"${r.cost_usd:.6f}",
            )

    console.print(detail_table)
    console.print()

    # サマリー比較テーブル
    summaries = aggregate_by_strategy(results)

    summary_table = Table(title="プロンプト戦略比較サマリー", show_lines=True)
    summary_table.add_column("戦略", style="bold", width=18)
    summary_table.add_column("正確性", justify="center", width=10)
    summary_table.add_column("平均レイテンシ", justify="right", width=12)
    summary_table.add_column("平均出力トークン", justify="right", width=14)
    summary_table.add_column("平均コスト", justify="right", width=12)

    for strategy, stats in summaries.items():
        acc = stats["accuracy"]
        acc_color = "green" if acc >= 0.8 else ("yellow" if acc >= 0.6 else "red")
        summary_table.add_row(
            strategy,
            f"[{acc_color}]{acc:.0%}[/{acc_color}]",
            f"{stats['avg_latency_ms']:.0f}ms",
            f"{stats['avg_output_tokens']:.0f}",
            f"${stats['avg_cost']:.6f}",
        )

    console.print(summary_table)

    # 分析
    console.print("\n[bold]分析[/bold]")
    best_acc = max(summaries.items(), key=lambda x: x[1]["accuracy"])
    cheapest = min(summaries.items(), key=lambda x: x[1]["avg_cost"])
    most_verbose = max(summaries.items(), key=lambda x: x[1]["avg_output_tokens"])
    console.print(f"  最高正確性:   {best_acc[0]} ({best_acc[1]['accuracy']:.0%})")
    console.print(f"  最低コスト:   {cheapest[0]} (${cheapest[1]['avg_cost']:.6f})")
    console.print(
        f"  最も冗長:     {most_verbose[0]} ({most_verbose[1]['avg_output_tokens']:.0f}トークン)"
    )

    # トレードオフに関する洞察
    console.print(
        Panel(
            "Few-shotプロンプトは、出力フォーマットの例を示すことで通常は正確性を"
            "向上させます。\n"
            "Chain-of-thoughtはトークン使用量（コスト）を増やしますが、推論の質を"
            "向上させることがあります。\n"
            "Zero-shotは最も安価ですが、ニュアンスを見逃す可能性があります。"
            "正確性とコストのバランスで選んでください。",
            title="重要な洞察",
            style="dim",
        )
    )

    # トークン使用量レポート（ライブモードのみ）
    if has_api_key:
        console.print()
        benchmark.token_tracker.report()


if __name__ == "__main__":
    main()
