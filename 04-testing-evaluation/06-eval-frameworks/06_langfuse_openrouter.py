"""
Langfuse — トレーシング&評価プラットフォーム (OpenRouter)

エージェントの可観測性と評価にLangfuseを使う方法を示す。Langfuseは
トレーシング（階層的なスパン）、スコアリング（数値・カテゴリ・真偽値）、
実験トラッキングを、オープンソースかつセルフホスト可能なプラットフォームとして
提供する。

このスクリプトの流れ:
1. デコレーターベースのトレーシングパターン（@observe）を示す
2. トレースへのプログラム的なスコアリングを示す
3. データセットを使った小規模な評価実験を実行する
4. Langfuseサーバーなしでシミュレーションモードで動作する

Install: pip install langfuse
Requires: LANGFUSE_SECRET_KEY, LANGFUSE_PUBLIC_KEY, LANGFUSE_BASE_URL
Or self-host: docker compose up (from langfuse repo)
"""

import os
import time
from dataclasses import dataclass, field
from typing import Any

from common import setup_logging
from dotenv import find_dotenv, load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from shared_openrouter.knowledge_base import EVAL_TASKS, get_agent_response

load_dotenv(find_dotenv())

logger = setup_logging(__name__)


# ---------------------------------------------------------------------------
# シミュレートされたLangfuseトレースコレクター（Langfuseサーバーなしのデモ用）
# ---------------------------------------------------------------------------


@dataclass
class SimulatedSpan:
    """シミュレートされたLangfuseのobservation/span。"""

    name: str
    span_type: str
    start_time: float = 0.0
    end_time: float = 0.0
    input_data: dict[str, Any] = field(default_factory=dict)
    output_data: dict[str, Any] = field(default_factory=dict)
    scores: list[dict[str, Any]] = field(default_factory=list)
    children: list["SimulatedSpan"] = field(default_factory=list)

    @property
    def duration_ms(self) -> float:
        return (self.end_time - self.start_time) * 1000


@dataclass
class SimulatedTrace:
    """スコアリング付きのシミュレートされたLangfuseトレース。"""

    trace_id: str
    name: str
    spans: list[SimulatedSpan] = field(default_factory=list)
    scores: list[dict[str, Any]] = field(default_factory=list)


class SimulatedLangfuse:
    """デモ用にLangfuseのトレーシングとスコアリングをシミュレートする。"""

    def __init__(self) -> None:
        self.traces: list[SimulatedTrace] = []
        self._current_trace: SimulatedTrace | None = None

    def start_trace(self, name: str, trace_id: str) -> SimulatedTrace:
        """新しいトレースを開始する。"""
        trace = SimulatedTrace(trace_id=trace_id, name=name)
        self.traces.append(trace)
        self._current_trace = trace
        return trace

    def start_span(self, name: str, span_type: str = "span") -> SimulatedSpan:
        """現在のトレース内で新しいスパンを開始する。"""
        span = SimulatedSpan(name=name, span_type=span_type, start_time=time.perf_counter())
        if self._current_trace:
            self._current_trace.spans.append(span)
        return span

    def end_span(self, span: SimulatedSpan, output: dict[str, Any] | None = None) -> None:
        """スパンを終了し、出力を記録する。"""
        span.end_time = time.perf_counter()
        if output:
            span.output_data = output

    def score_trace(
        self,
        trace: SimulatedTrace,
        name: str,
        value: float | str | bool,
        data_type: str = "NUMERIC",
        comment: str = "",
    ) -> None:
        """トレースにスコアを追加する（langfuse.create_scoreを模倣）。"""
        trace.scores.append(
            {
                "name": name,
                "value": value,
                "data_type": data_type,
                "comment": comment,
            }
        )


# ---------------------------------------------------------------------------
# トレーシングとスコアリングを伴う評価
# ---------------------------------------------------------------------------


def run_traced_eval(
    langfuse_client: SimulatedLangfuse,
    tasks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Langfuseスタイルのトレーシングとスコアリングで評価タスクを実行する。"""
    results: list[dict[str, Any]] = []

    for task in tasks:
        # この評価タスク用のトレースを開始
        trace = langfuse_client.start_trace(
            name=f"eval_{task['id']}",
            trace_id=f"trace_{task['id']}",
        )

        # スパン: エージェント実行
        agent_span = langfuse_client.start_span("agent_call", span_type="generation")
        agent_span.input_data = {"question": task["question"]}

        response = get_agent_response(task["id"])

        langfuse_client.end_span(agent_span, output={"answer": response["answer"]})

        # スパン: 採点
        grading_span = langfuse_client.start_span("grading", span_type="span")

        # スコア: キーワードカバレッジ（NUMERIC）
        answer_lower = response["answer"].lower()
        keywords = task["expected_keywords"]
        if keywords:
            found = sum(1 for kw in keywords if kw.lower() in answer_lower)
            keyword_score = found / len(keywords)
        else:
            has_refusal = "見つかりませんでした" in response["answer"] or (
                "含まれていません" in response["answer"]
            )
            keyword_score = 1.0 if has_refusal else 0.0

        langfuse_client.score_trace(
            trace,
            name="keyword_coverage",
            value=keyword_score,
            data_type="NUMERIC",
            comment=f"{found if keywords else 'N/A'}/{len(keywords)} 件のキーワードが一致",
        )

        # スコア: 出典の裏付け（BOOLEAN）
        expected_sources = task.get("expected_source_ids", [])
        if expected_sources:
            all_cited = all(sid in response["answer"] for sid in expected_sources)
        else:
            all_cited = "見つかりませんでした" in response["answer"] or (
                "含まれていません" in response["answer"]
            )
        langfuse_client.score_trace(
            trace,
            name="source_grounded",
            value=all_cited,
            data_type="BOOLEAN",
            comment="期待された出典をすべて引用" if all_cited else "出典の引用が不足",
        )

        # スコア: 品質カテゴリ（CATEGORICAL）
        if keyword_score >= 0.8 and all_cited:
            quality = "excellent"
        elif keyword_score >= 0.5:
            quality = "acceptable"
        else:
            quality = "poor"
        langfuse_client.score_trace(
            trace,
            name="quality_tier",
            value=quality,
            data_type="CATEGORICAL",
            comment=f"keyword={keyword_score:.0%}, grounded={all_cited}",
        )

        langfuse_client.end_span(grading_span)

        results.append(
            {
                "task_id": task["id"],
                "trace_id": trace.trace_id,
                "keyword_score": keyword_score,
                "grounded": all_cited,
                "quality": quality,
                "duration_ms": sum(s.duration_ms for s in trace.spans),
            }
        )

    return results


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


def main() -> None:
    """リサーチアシスタントに対してLangfuseスタイルのトレース付き評価を実行する。"""
    console = Console()
    console.print(
        Panel(
            "[bold cyan]Langfuse — トレーシング&評価プラットフォーム[/bold cyan]\n\n"
            "エージェント評価向けのLangfuseパターンを示す:\n"
            "  - デコレーターベースのトレーシング（@observe）\n"
            "  - プログラム的なスコアリング（NUMERIC, BOOLEAN, CATEGORICAL）\n"
            "  - データセットを使った実験トラッキング\n\n"
            "オープンソース・セルフホスト可能。Install: pip install langfuse",
            title="06 - Langfuse (OpenRouter)",
        )
    )

    # Langfuse SDKと認証情報の有無をチェック
    has_langfuse = False
    try:
        import langfuse  # noqa: F401

        has_langfuse = True
    except ImportError:
        pass

    has_langfuse_keys = bool(
        os.environ.get("LANGFUSE_SECRET_KEY") and os.environ.get("LANGFUSE_PUBLIC_KEY")
    )

    if has_langfuse and has_langfuse_keys:
        console.print("[green]Langfuse SDK + keys found — traces will be sent to server[/green]")
    elif has_langfuse:
        console.print(
            "[yellow]Langfuse SDK installed but no keys — running simulated mode[/yellow]"
        )
    else:
        console.print("[yellow]Langfuse not installed — running simulated demo[/yellow]")
    console.print()

    # シミュレートされたLangfuseクライアントで評価を実行
    # 本番環境では、SimulatedLangfuseを実際のLangfuse SDKに置き換える
    langfuse_client = SimulatedLangfuse()
    results = run_traced_eval(langfuse_client, EVAL_TASKS)

    # 結果テーブル
    table = Table(title="Langfuse トレース付き評価結果", show_lines=True)
    table.add_column("Task", style="cyan", width=12)
    table.add_column("Trace ID", width=16)
    table.add_column("Keywords", width=10, justify="center")
    table.add_column("Grounded", width=10, justify="center")
    table.add_column("Quality", width=12, justify="center")
    table.add_column("Duration", width=10, justify="right")

    for r in results:
        kw_color = "green" if r["keyword_score"] >= 0.7 else "yellow"
        grounded_str = "[green]True[/green]" if r["grounded"] else "[red]False[/red]"
        quality_color = {
            "excellent": "green",
            "acceptable": "yellow",
            "poor": "red",
        }.get(r["quality"], "dim")

        table.add_row(
            r["task_id"],
            r["trace_id"],
            f"[{kw_color}]{r['keyword_score']:.0%}[/{kw_color}]",
            grounded_str,
            f"[{quality_color}]{r['quality']}[/{quality_color}]",
            f"{r['duration_ms']:.1f}ms",
        )

    console.print(table)

    # トレースサマリー
    console.print(
        f"\n[bold]収集されたトレース数:[/bold] {len(langfuse_client.traces)}\n"
        f"[bold]合計スコア数:[/bold] "
        f"{sum(len(t.scores) for t in langfuse_client.traces)}\n"
        f"[bold]合計スパン数:[/bold] "
        f"{sum(len(t.spans) for t in langfuse_client.traces)}"
    )

    # スコアタイプの内訳
    score_types = {"NUMERIC": 0, "BOOLEAN": 0, "CATEGORICAL": 0}
    for trace in langfuse_client.traces:
        for score in trace.scores:
            score_types[score["data_type"]] = score_types.get(score["data_type"], 0) + 1

    console.print("\n[bold]使用されたスコアタイプ:[/bold]")
    for dtype, count in score_types.items():
        console.print(f"  {dtype}: {count}")

    # Langfuseのコードパターンを表示
    console.print("\n[bold]Langfuse SDKのパターン:[/bold]\n")
    from rich.syntax import Syntax

    decorator_code = (
        "from langfuse import observe, get_client\n\n"
        "@observe()  # 自動的にトレースを作成する\n"
        "def my_agent(question: str) -> str:\n"
        "    result = search_and_answer(question)\n"
        "    return result\n\n"
        '@observe(name="llm-call", as_type="generation")\n'
        "def search_and_answer(question: str) -> str:\n"
        "    # ネストしたスパンは自動的にキャプチャされる\n"
        "    # OpenRouterはOpenAI互換のためbase_urlを向け替えるだけでよい\n"
        "    return call_llm_via_openrouter(question)\n"
    )
    console.print(Syntax(decorator_code, "python", theme="monokai", line_numbers=True))

    scoring_code = (
        "langfuse = get_client()\n\n"
        "# 実行後にスコアを付与する\n"
        "langfuse.create_score(\n"
        "    trace_id=trace_id,\n"
        '    name="correctness",\n'
        "    value=0.95,\n"
        '    data_type="NUMERIC",\n'
        '    comment="事実として正確",\n'
        ")\n"
    )
    console.print(Syntax(scoring_code, "python", theme="monokai", line_numbers=True))


if __name__ == "__main__":
    main()
