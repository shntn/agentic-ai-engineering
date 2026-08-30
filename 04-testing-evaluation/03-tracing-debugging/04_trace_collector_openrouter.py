"""
トレースコレクター (OpenRouter)

エージェントの実行を追跡する、純粋なPython製のトレーシングシステムの構築方法を
実演する。すべてのLLM呼び出し・ツール呼び出し・エージェントステップを、
タイミング・トークン使用量・入出力を記録する階層的なスパンで計装する——
エージェントの可観測性の基盤となる考え方。

キーコンセプト:
- スパンベースのトレーシング: 操作をツリー状にネストし、実行の全体像を把握する
- コンテキストマネージャーのスパン: 適切なネストで自動的に開始/終了時刻を記録する
- デコレーターベースのトレーシング: 関数本体を変更せずに計装する
- トレースのシリアライズ: あとで分析・デバッグできるようトレースをJSONとして出力する
"""

import os
from typing import Any

from common import setup_logging
from dotenv import find_dotenv, load_dotenv
from openrouter import OpenRouter
from rich.console import Console
from rich.panel import Panel
from rich.tree import Tree

from shared_openrouter.agent import TracedResearchAssistant
from shared_openrouter.tracer import TraceCollector

load_dotenv(find_dotenv())

logger = setup_logging(__name__)


# ---------------------------------------------------------------------------
# 可視化用のヘルパー
# ---------------------------------------------------------------------------


def build_span_tree(span_data: dict[str, Any], tree: Tree) -> None:
    """スパンデータからRichのツリーを再帰的に構築する。"""
    duration = span_data.get("duration_ms", 0)
    tokens = span_data.get("tokens", {})
    error = span_data.get("error")

    label = f"[bold]{span_data['name']}[/bold] [{span_data['span_type']}]"
    label += f"  {duration:.1f}ms"
    if tokens:
        label += f"  tokens: {tokens.get('input', 0)}in/{tokens.get('output', 0)}out"
    if error:
        label += f"  [red]ERROR: {error}[/red]"

    branch = tree.add(label)
    for child in span_data.get("children", []):
        build_span_tree(child, branch)


# ---------------------------------------------------------------------------
# オフラインモード用のサンプルトレース
# ---------------------------------------------------------------------------

SAMPLE_TRACE = {
    "trace_id": "sample_001",
    "question": "マイクロサービスの利点は何ですか？",
    "spans": [
        {
            "name": "answer_question",
            "span_type": "agent_step",
            "start_time": 1000.0,
            "end_time": 1003.5,
            "duration_ms": 3500.0,
            "inputs": {
                "question": "マイクロサービスの利点は何ですか？",
            },
            "outputs": {
                "answer": "マイクロサービスはスケーラビリティを提供します...",
                "llm_calls": 3,
            },
            "metadata": {},
            "tokens": {},
            "error": None,
            "children": [
                {
                    "name": "llm_call_1",
                    "span_type": "llm_call",
                    "start_time": 1000.1,
                    "end_time": 1001.2,
                    "duration_ms": 1100.0,
                    "inputs": {"message_count": 1},
                    "outputs": {"finish_reason": "tool_calls"},
                    "metadata": {},
                    "tokens": {"input": 150, "output": 80},
                    "error": None,
                    "children": [],
                },
                {
                    "name": "tool_search_knowledge_base",
                    "span_type": "tool_call",
                    "start_time": 1001.2,
                    "end_time": 1001.22,
                    "duration_ms": 20.0,
                    "inputs": {
                        "tool": "search_knowledge_base",
                        "input": {"query": "マイクロサービス 利点"},
                    },
                    "outputs": {
                        "result": "[{'id': 'doc_001', 'title': 'マイクロサービスアーキテクチャ'}]",
                    },
                    "metadata": {},
                    "tokens": {},
                    "error": None,
                    "children": [],
                },
                {
                    "name": "llm_call_2",
                    "span_type": "llm_call",
                    "start_time": 1001.3,
                    "end_time": 1002.5,
                    "duration_ms": 1200.0,
                    "inputs": {"message_count": 3},
                    "outputs": {"finish_reason": "tool_calls"},
                    "metadata": {},
                    "tokens": {"input": 280, "output": 60},
                    "error": None,
                    "children": [],
                },
                {
                    "name": "tool_get_document",
                    "span_type": "tool_call",
                    "start_time": 1002.5,
                    "end_time": 1002.51,
                    "duration_ms": 10.0,
                    "inputs": {
                        "tool": "get_document",
                        "input": {"doc_id": "doc_001"},
                    },
                    "outputs": {
                        "result": "{'id': 'doc_001', 'title': 'マイクロサービスアーキテクチャ'}",
                    },
                    "metadata": {},
                    "tokens": {},
                    "error": None,
                    "children": [],
                },
                {
                    "name": "llm_call_3",
                    "span_type": "llm_call",
                    "start_time": 1002.6,
                    "end_time": 1003.4,
                    "duration_ms": 800.0,
                    "inputs": {"message_count": 5},
                    "outputs": {"finish_reason": "stop"},
                    "metadata": {},
                    "tokens": {"input": 450, "output": 120},
                    "error": None,
                    "children": [],
                },
            ],
        }
    ],
}


def main() -> None:
    """トレース対象のリサーチアシスタントを実行し、実行トレースを可視化する。"""
    console = Console()

    console.print(
        Panel(
            "[bold cyan]トレースコレクター[/bold cyan]\n\n"
            "すべての操作について、タイミング・トークン使用量・入出力を記録する\n"
            "階層的なスパンでエージェントの実行を計装します。\n\n"
            "コンセプト: スパン階層、コンテキストマネージャーによるトレーシング、トレースのシリアライズ",
            title="01 - トレースコレクター",
        )
    )

    # 実行モードを決定する
    has_api_key = bool(os.environ.get("OPENROUTER_API_KEY"))

    if has_api_key:
        console.print(
            "\n[green]APIキーが見つかりました — ライブのトレース対象エージェントを実行します[/green]\n"
        )
        client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        tracer = TraceCollector()
        assistant = TracedResearchAssistant(client, tracer)

        questions = [
            "マイクロサービスの利点は何ですか？",
            "REST APIをどのように設計すればよいですか？",
        ]

        for question in questions:
            console.print(f"\n[bold yellow]Question:[/bold yellow] {question}")
            try:
                result = assistant.answer(question)
                console.print(f"[dim]Answer: {result['answer'][:150]}...[/dim]")
                console.print(f"[dim]LLM calls: {result['llm_calls']}[/dim]")
            except Exception as e:
                logger.error("Error answering question: %s", e)

        trace_data = tracer.to_dict()
        trace_path = "trace_output.json"
        tracer.save(trace_path)
        console.print(f"\n[green]Trace saved to {trace_path}[/green]")

    else:
        console.print(
            "\n[yellow]APIキーが見つかりません — サンプルのトレースデータを使用します[/yellow]\n"
        )
        trace_data = SAMPLE_TRACE

    # トレースをツリーとして可視化する
    console.print("\n[bold]Trace Visualization[/bold]\n")

    tree = Tree(f"[bold magenta]Trace {trace_data.get('trace_id', 'unknown')}[/bold magenta]")
    for span_data in trace_data.get("spans", []):
        build_span_tree(span_data, tree)

    console.print(tree)

    # サマリー統計
    total_tokens = {"input": 0, "output": 0}
    span_count = 0

    def count_spans(spans: list[dict[str, Any]]) -> None:
        nonlocal span_count
        for s in spans:
            span_count += 1
            tokens = s.get("tokens", {})
            total_tokens["input"] += tokens.get("input", 0)
            total_tokens["output"] += tokens.get("output", 0)
            count_spans(s.get("children", []))

    count_spans(trace_data.get("spans", []))

    console.print(
        f"\n[bold]Summary:[/bold] {span_count} spans, "
        f"{total_tokens['input']} input tokens, "
        f"{total_tokens['output']} output tokens"
    )


if __name__ == "__main__":
    main()
