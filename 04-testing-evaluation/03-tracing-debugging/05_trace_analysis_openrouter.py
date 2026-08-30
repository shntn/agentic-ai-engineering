"""
トレース分析 (OpenRouter)

記録済みのトレースを読み込み、集計メトリクスを計算し、アンチパターンを検出し、
トレースを比較する方法を実演する。サンプルのトレースデータを使い、完全に
オフラインで動作する——APIキーは不要。

キーコンセプト:
- 集計メトリクス: 合計トークン数、コスト見積もり、スパンタイプ別のレイテンシ内訳
- アンチパターン検出: 過剰な呼び出し、検索の繰り返し、高トークン消費、エラー
- トレース比較: 同じタスクの2つのトレースを差分比較し、リグレッションを見つける
"""

import json
from typing import Any

from common import setup_logging
from dotenv import find_dotenv, load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from shared_openrouter.tracer import collect_all_spans

load_dotenv(find_dotenv())

logger = setup_logging(__name__)

# deepseek/deepseek-v4-flash-0731 の実際の料金（$ per トークン）。
# client.models.list() で取得した実測値を基にしている（2026年8月時点）。
COST_PER_INPUT_TOKEN = 0.000000065  # 入力100万トークンあたり$0.065
COST_PER_OUTPUT_TOKEN = 0.00000018  # 出力100万トークンあたり$0.18


# ---------------------------------------------------------------------------
# サンプルトレース — 自己完結型、APIキー不要
# ---------------------------------------------------------------------------

SAMPLE_TRACE_GOOD = {
    "trace_id": "trace_good",
    "question": "マイクロサービスの利点は何ですか？",
    "spans": [
        {
            "name": "answer_question",
            "span_type": "agent_step",
            "start_time": 1000.0,
            "end_time": 1003.5,
            "duration_ms": 3500.0,
            "inputs": {"question": "マイクロサービスの利点は何ですか？"},
            "outputs": {"answer": "マイクロサービスはスケーラビリティ、障害分離を提供します..."},
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
                    "outputs": {"result": "[{'id': 'doc_001'}]"},
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
                    "inputs": {"tool": "get_document", "input": {"doc_id": "doc_001"}},
                    "outputs": {
                        "result": "{'id': 'doc_001', 'title': 'マイクロサービスアーキテクチャ'}"
                    },
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
                    "tokens": {"input": 450, "output": 120},
                    "error": None,
                    "children": [],
                },
            ],
        }
    ],
}

SAMPLE_TRACE_ANTI_PATTERNS = {
    "trace_id": "trace_anti",
    "question": "キャッシュについて教えてください",
    "spans": [
        {
            "name": "answer_question",
            "span_type": "agent_step",
            "start_time": 2000.0,
            "end_time": 2018.0,
            "duration_ms": 18000.0,
            "inputs": {"question": "キャッシュについて教えてください"},
            "outputs": {"answer": "キャッシュとは..."},
            "tokens": {},
            "error": None,
            "children": [
                {
                    "name": "llm_call_1",
                    "span_type": "llm_call",
                    "start_time": 2000.1,
                    "end_time": 2001.5,
                    "duration_ms": 1400.0,
                    "inputs": {"message_count": 1},
                    "outputs": {"finish_reason": "tool_calls"},
                    "tokens": {"input": 200, "output": 90},
                    "error": None,
                    "children": [],
                },
                {
                    "name": "tool_search_knowledge_base",
                    "span_type": "tool_call",
                    "start_time": 2001.5,
                    "end_time": 2001.52,
                    "duration_ms": 20.0,
                    "inputs": {"tool": "search_knowledge_base", "input": {"query": "キャッシュ"}},
                    "outputs": {"result": "[{'id': 'doc_008'}]"},
                    "tokens": {},
                    "error": None,
                    "children": [],
                },
                {
                    "name": "llm_call_2",
                    "span_type": "llm_call",
                    "start_time": 2001.6,
                    "end_time": 2003.0,
                    "duration_ms": 1400.0,
                    "inputs": {"message_count": 3},
                    "outputs": {"finish_reason": "tool_calls"},
                    "tokens": {"input": 350, "output": 70},
                    "error": None,
                    "children": [],
                },
                # 検索の繰り返し — 同じクエリを再度実行（アンチパターン）
                {
                    "name": "tool_search_knowledge_base",
                    "span_type": "tool_call",
                    "start_time": 2003.0,
                    "end_time": 2003.02,
                    "duration_ms": 20.0,
                    "inputs": {"tool": "search_knowledge_base", "input": {"query": "キャッシュ"}},
                    "outputs": {"result": "[{'id': 'doc_008'}]"},
                    "tokens": {},
                    "error": None,
                    "children": [],
                },
                {
                    "name": "llm_call_3",
                    "span_type": "llm_call",
                    "start_time": 2003.1,
                    "end_time": 2005.0,
                    "duration_ms": 1900.0,
                    "inputs": {"message_count": 5},
                    "outputs": {"finish_reason": "tool_calls"},
                    "tokens": {"input": 500, "output": 100},
                    "error": None,
                    "children": [],
                },
                {
                    "name": "tool_get_document",
                    "span_type": "tool_call",
                    "start_time": 2005.0,
                    "end_time": 2005.01,
                    "duration_ms": 10.0,
                    "inputs": {"tool": "get_document", "input": {"doc_id": "doc_008"}},
                    "outputs": {"result": "{'id': 'doc_008'}"},
                    "tokens": {},
                    "error": None,
                    "children": [],
                },
                {
                    "name": "llm_call_4",
                    "span_type": "llm_call",
                    "start_time": 2005.1,
                    "end_time": 2007.0,
                    "duration_ms": 1900.0,
                    "inputs": {"message_count": 7},
                    "outputs": {"finish_reason": "tool_calls"},
                    "tokens": {"input": 700, "output": 110},
                    "error": None,
                    "children": [],
                },
                # 検索の繰り返し — 3回目（アンチパターン）
                {
                    "name": "tool_search_knowledge_base",
                    "span_type": "tool_call",
                    "start_time": 2007.0,
                    "end_time": 2007.02,
                    "duration_ms": 20.0,
                    "inputs": {"tool": "search_knowledge_base", "input": {"query": "キャッシュ"}},
                    "outputs": {"result": "[{'id': 'doc_008'}]"},
                    "tokens": {},
                    "error": None,
                    "children": [],
                },
                {
                    "name": "llm_call_5",
                    "span_type": "llm_call",
                    "start_time": 2007.1,
                    "end_time": 2009.0,
                    "duration_ms": 1900.0,
                    "inputs": {"message_count": 9},
                    "outputs": {"finish_reason": "tool_calls"},
                    "tokens": {"input": 900, "output": 130},
                    "error": None,
                    "children": [],
                },
                {
                    "name": "tool_get_document",
                    "span_type": "tool_call",
                    "start_time": 2009.0,
                    "end_time": 2009.01,
                    "duration_ms": 10.0,
                    "inputs": {"tool": "get_document", "input": {"doc_id": "doc_008"}},
                    "outputs": {"result": "{'id': 'doc_008'}"},
                    "tokens": {},
                    "error": None,
                    "children": [],
                },
                # 遅いLLM呼び出し（アンチパターン: 10秒超）
                {
                    "name": "llm_call_6",
                    "span_type": "llm_call",
                    "start_time": 2009.1,
                    "end_time": 2017.5,
                    "duration_ms": 8400.0,
                    "inputs": {"message_count": 11},
                    "outputs": {"finish_reason": "stop"},
                    "tokens": {"input": 1100, "output": 250},
                    "error": None,
                    "children": [],
                },
            ],
        }
    ],
}

SAMPLE_TRACE_ERROR = {
    "trace_id": "trace_error",
    "question": "GraphQLとは何ですか？",
    "spans": [
        {
            "name": "answer_question",
            "span_type": "agent_step",
            "start_time": 3000.0,
            "end_time": 3004.0,
            "duration_ms": 4000.0,
            "inputs": {"question": "GraphQLとは何ですか？"},
            "outputs": {},
            "tokens": {},
            "error": "No relevant documents found",
            "children": [
                {
                    "name": "llm_call_1",
                    "span_type": "llm_call",
                    "start_time": 3000.1,
                    "end_time": 3001.3,
                    "duration_ms": 1200.0,
                    "inputs": {"message_count": 1},
                    "outputs": {"finish_reason": "tool_calls"},
                    "tokens": {"input": 150, "output": 70},
                    "error": None,
                    "children": [],
                },
                {
                    "name": "tool_search_knowledge_base",
                    "span_type": "tool_call",
                    "start_time": 3001.3,
                    "end_time": 3001.32,
                    "duration_ms": 20.0,
                    "inputs": {"tool": "search_knowledge_base", "input": {"query": "GraphQL"}},
                    "outputs": {"result": "[]"},
                    "tokens": {},
                    "error": "No results found",
                    "children": [],
                },
                {
                    "name": "llm_call_2",
                    "span_type": "llm_call",
                    "start_time": 3001.4,
                    "end_time": 3003.8,
                    "duration_ms": 2400.0,
                    "inputs": {"message_count": 3},
                    "outputs": {"finish_reason": "stop"},
                    "tokens": {"input": 300, "output": 180},
                    "error": None,
                    "children": [],
                },
            ],
        }
    ],
}

ALL_SAMPLE_TRACES = {
    "good": SAMPLE_TRACE_GOOD,
    "anti_patterns": SAMPLE_TRACE_ANTI_PATTERNS,
    "error": SAMPLE_TRACE_ERROR,
}


# ---------------------------------------------------------------------------
# トレース分析
# ---------------------------------------------------------------------------


class TraceAnalyzer:
    """パターンの検出とメトリクスの計算のために実行トレースを分析する。"""

    def load_trace(self, path: str) -> dict[str, Any]:
        """JSONファイルからトレースを読み込む。"""
        from pathlib import Path

        with Path(path).open(encoding="utf-8") as f:
            result: dict[str, Any] = json.load(f)
            return result

    def load_trace_from_dict(self, trace_data: dict[str, Any]) -> dict[str, Any]:
        """メモリ上の辞書からトレースを読み込む。"""
        return trace_data

    def compute_metrics(self, trace: dict[str, Any]) -> dict[str, Any]:
        """集計メトリクスを計算する: 合計トークン数、コスト、ステップ数、レイテンシ内訳。"""
        all_spans = collect_all_spans(trace.get("spans", []))

        total_input_tokens = 0
        total_output_tokens = 0
        llm_latency_ms = 0.0
        tool_latency_ms = 0.0
        llm_call_count = 0
        tool_call_count = 0
        error_count = 0

        for span in all_spans:
            tokens = span.get("tokens", {})
            total_input_tokens += tokens.get("input", 0)
            total_output_tokens += tokens.get("output", 0)

            duration = span.get("duration_ms", 0.0)
            span_type = span.get("span_type", "")

            if span_type == "llm_call":
                llm_latency_ms += duration
                llm_call_count += 1
            elif span_type == "tool_call":
                tool_latency_ms += duration
                tool_call_count += 1

            if span.get("error"):
                error_count += 1

        total_tokens = total_input_tokens + total_output_tokens
        estimated_cost = (
            total_input_tokens * COST_PER_INPUT_TOKEN + total_output_tokens * COST_PER_OUTPUT_TOKEN
        )

        # ルートスパンからの合計所要時間
        total_duration_ms = sum(s.get("duration_ms", 0.0) for s in trace.get("spans", []))

        return {
            "total_input_tokens": total_input_tokens,
            "total_output_tokens": total_output_tokens,
            "total_tokens": total_tokens,
            "estimated_cost_usd": round(estimated_cost, 6),
            "llm_call_count": llm_call_count,
            "tool_call_count": tool_call_count,
            "error_count": error_count,
            "total_duration_ms": round(total_duration_ms, 2),
            "llm_latency_ms": round(llm_latency_ms, 2),
            "tool_latency_ms": round(tool_latency_ms, 2),
            "total_spans": len(all_spans),
        }

    def detect_anti_patterns(self, trace: dict[str, Any]) -> list[dict[str, str]]:
        """過剰なツール呼び出し・ループ・失敗したツールなどのアンチパターンを検出する。"""
        issues: list[dict[str, str]] = []
        all_spans = collect_all_spans(trace.get("spans", []))

        # チェック1: 過剰なLLM呼び出し（1つの質問に対して5回超）
        llm_calls = [s for s in all_spans if s.get("span_type") == "llm_call"]
        if len(llm_calls) > 5:
            issues.append(
                {
                    "pattern": "excessive_llm_calls",
                    "severity": "warning",
                    "message": f"Found {len(llm_calls)} LLM calls — consider simplifying the prompt",
                }
            )

        # チェック2: 同一のツール呼び出しの繰り返し
        tool_calls = [s for s in all_spans if s.get("span_type") == "tool_call"]
        seen_calls: dict[str, int] = {}
        for tc in tool_calls:
            tool_input = tc.get("inputs", {}).get("input", {})
            key = f"{tc.get('inputs', {}).get('tool', '')}:{json.dumps(tool_input, sort_keys=True, ensure_ascii=False)}"
            seen_calls[key] = seen_calls.get(key, 0) + 1

        for key, count in seen_calls.items():
            if count > 1:
                issues.append(
                    {
                        "pattern": "repeated_tool_call",
                        "severity": "warning",
                        "message": f"Tool call '{key}' repeated {count} times — agent may be looping",
                    }
                )

        # チェック3: 高いトークン消費（単純なタスクで合計2000超）
        total_tokens = sum(
            s.get("tokens", {}).get("input", 0) + s.get("tokens", {}).get("output", 0)
            for s in all_spans
        )
        if total_tokens > 2000:
            issues.append(
                {
                    "pattern": "high_token_usage",
                    "severity": "info",
                    "message": f"Total token usage is {total_tokens} — review if the task warrants it",
                }
            )

        # チェック4: リトライされなかった失敗したツール呼び出し
        failed_tools = [s for s in tool_calls if s.get("error")]
        for ft in failed_tools:
            tool_name = ft.get("inputs", {}).get("tool", "unknown")
            retried = any(
                s.get("inputs", {}).get("tool") == tool_name
                for s in tool_calls
                if s is not ft and not s.get("error")
            )
            if not retried:
                issues.append(
                    {
                        "pattern": "unretried_failure",
                        "severity": "error",
                        "message": f"Tool '{tool_name}' failed but was not retried",
                    }
                )

        # チェック5: 非常に長いスパン（単一操作で10秒超）
        for span in all_spans:
            duration = span.get("duration_ms", 0.0)
            if duration > 10000 and span.get("span_type") != "agent_step":
                issues.append(
                    {
                        "pattern": "slow_span",
                        "severity": "warning",
                        "message": (
                            f"Span '{span['name']}' took {duration:.0f}ms (>{10000}ms threshold)"
                        ),
                    }
                )

        return issues

    def compare_traces(self, trace_a: dict[str, Any], trace_b: dict[str, Any]) -> dict[str, Any]:
        """同じタスクの2つのトレースを比較する。"""
        metrics_a = self.compute_metrics(trace_a)
        metrics_b = self.compute_metrics(trace_b)

        comparison: dict[str, Any] = {}
        for key in metrics_a:
            val_a = metrics_a[key]
            val_b = metrics_b[key]
            if isinstance(val_a, (int, float)) and isinstance(val_b, (int, float)):
                diff = val_b - val_a
                pct = (diff / val_a * 100) if val_a != 0 else 0.0
                comparison[key] = {
                    "trace_a": val_a,
                    "trace_b": val_b,
                    "diff": round(diff, 4),
                    "pct_change": round(pct, 1),
                }

        return comparison


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


def main() -> None:
    """サンプルトレースを分析する: メトリクスの計算、アンチパターンの検出、比較。"""
    console = Console()

    console.print(
        Panel(
            "[bold cyan]トレース分析[/bold cyan]\n\n"
            "記録済みのトレースを読み込み、集計メトリクスを計算し、\n"
            "アンチパターンを検出し、トレースを比較します。完全にオフラインで動作します。\n\n"
            "コンセプト: メトリクス集計、アンチパターン検出、トレース比較",
            title="02 - トレース分析",
        )
    )

    analyzer = TraceAnalyzer()

    # --- メトリクステーブル ---
    console.print("\n[bold]トレースメトリクス[/bold]\n")

    metrics_table = Table(title="トレースごとのメトリクス")
    metrics_table.add_column("メトリクス", style="cyan")
    for name in ALL_SAMPLE_TRACES:
        metrics_table.add_column(name, justify="right")

    all_metrics: dict[str, dict[str, Any]] = {}
    for name, trace in ALL_SAMPLE_TRACES.items():
        all_metrics[name] = analyzer.compute_metrics(trace)

    metric_labels = {
        "total_tokens": "合計トークン数",
        "total_input_tokens": "入力トークン数",
        "total_output_tokens": "出力トークン数",
        "estimated_cost_usd": "コスト見積もり（USD）",
        "llm_call_count": "LLM呼び出し回数",
        "tool_call_count": "ツール呼び出し回数",
        "error_count": "エラー数",
        "total_duration_ms": "合計所要時間（ms）",
        "llm_latency_ms": "LLMレイテンシ（ms）",
        "tool_latency_ms": "ツールレイテンシ（ms）",
        "total_spans": "合計スパン数",
    }

    for key, label in metric_labels.items():
        row = [label]
        for name in ALL_SAMPLE_TRACES:
            val = all_metrics[name].get(key, 0)
            if key == "estimated_cost_usd":
                row.append(f"${val:.6f}")
            elif isinstance(val, float):
                row.append(f"{val:.1f}")
            else:
                row.append(str(val))
        metrics_table.add_row(*row)

    console.print(metrics_table)

    # --- アンチパターン検出 ---
    console.print("\n[bold]アンチパターン検出[/bold]\n")

    for name, trace in ALL_SAMPLE_TRACES.items():
        issues = analyzer.detect_anti_patterns(trace)
        if issues:
            issue_table = Table(title=f"'{name}'の問題")
            issue_table.add_column("重大度", style="bold")
            issue_table.add_column("パターン")
            issue_table.add_column("メッセージ")
            for issue in issues:
                severity = issue["severity"]
                style = {"error": "red", "warning": "yellow", "info": "blue"}.get(severity, "")
                issue_table.add_row(
                    f"[{style}]{severity.upper()}[/{style}]",
                    issue["pattern"],
                    issue["message"],
                )
            console.print(issue_table)
        else:
            console.print(f"  [green]'{name}'に問題は検出されませんでした[/green]")
        console.print()

    # --- トレース比較 ---
    console.print("[bold]トレース比較: good vs anti_patterns[/bold]\n")

    comparison = analyzer.compare_traces(SAMPLE_TRACE_GOOD, SAMPLE_TRACE_ANTI_PATTERNS)
    comp_table = Table(title="比較")
    comp_table.add_column("メトリクス", style="cyan")
    comp_table.add_column("Good", justify="right")
    comp_table.add_column("Anti-Patterns", justify="right")
    comp_table.add_column("差分", justify="right")
    comp_table.add_column("変化率", justify="right")

    for key, vals in comparison.items():
        label = metric_labels.get(key, key)
        pct = vals["pct_change"]
        pct_style = "red" if pct > 0 else "green" if pct < 0 else ""
        comp_table.add_row(
            label,
            str(vals["trace_a"]),
            str(vals["trace_b"]),
            str(vals["diff"]),
            f"[{pct_style}]{pct:+.1f}%[/{pct_style}]" if pct_style else f"{pct:+.1f}%",
        )

    console.print(comp_table)


if __name__ == "__main__":
    main()
