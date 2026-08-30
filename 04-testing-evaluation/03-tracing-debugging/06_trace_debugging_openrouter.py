"""
トレースデバッグ (OpenRouter)

トレースベースのデバッグワークフローを実演する: 失敗したエージェント実行に対し、
記録済みのトレースをたどって失敗箇所を見つけ、決定パスを抽出し、修正案を提案し、
チェックポイントから再実行する。

キーコンセプト:
- 失敗箇所の検出: スパンツリーをたどり、最初のエラーまたは予期しない出力を見つける
- 決定パスの抽出: エージェントが行った一連の選択を再構築する
- 修正案の提案: 失敗の種類を実行可能な対処ステップに対応付ける
- トレースリプレイ: チェックポイントを一覧表示し、選んだ地点から再実行をシミュレートする
"""

import json
import os
import time
from typing import Any

from common import OpenRouterTokenTracker, setup_logging
from dotenv import find_dotenv, load_dotenv
from openrouter import OpenRouter
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from shared_openrouter.tracer import collect_all_spans

load_dotenv(find_dotenv())

logger = setup_logging(__name__)

MODEL = "deepseek/deepseek-v4-flash-0731"


# ---------------------------------------------------------------------------
# 失敗したサンプルトレース
# ---------------------------------------------------------------------------

# 失敗1: エージェントが誤った語で検索し、結果が見つからなかった
TRACE_WRONG_SEARCH = {
    "trace_id": "fail_wrong_search",
    "question": "Kubernetesはどのようにオートスケーリングを処理しますか？",
    "expected_answer_contains": "オートスケーリング",
    "spans": [
        {
            "name": "answer_question",
            "span_type": "agent_step",
            "start_time": 1000.0,
            "end_time": 1005.0,
            "duration_ms": 5000.0,
            "inputs": {"question": "Kubernetesはどのようにオートスケーリングを処理しますか？"},
            "outputs": {"answer": "関連する情報が見つかりませんでした。"},
            "tokens": {},
            "error": None,
            "children": [
                {
                    "name": "llm_call_1",
                    "span_type": "llm_call",
                    "start_time": 1000.1,
                    "end_time": 1001.5,
                    "duration_ms": 1400.0,
                    "inputs": {"message_count": 1},
                    "outputs": {"finish_reason": "tool_calls"},
                    "tokens": {"input": 160, "output": 70},
                    "error": None,
                    "children": [],
                },
                {
                    "name": "tool_search_knowledge_base",
                    "span_type": "tool_call",
                    "start_time": 1001.5,
                    "end_time": 1001.52,
                    "duration_ms": 20.0,
                    "inputs": {
                        "tool": "search_knowledge_base",
                        "input": {"query": "水平Podオートスケーラー HPA"},
                    },
                    "outputs": {"result": "[]"},
                    "tokens": {},
                    "error": "No results found for overly specific query",
                    "children": [],
                },
                {
                    "name": "llm_call_2",
                    "span_type": "llm_call",
                    "start_time": 1001.6,
                    "end_time": 1003.0,
                    "duration_ms": 1400.0,
                    "inputs": {"message_count": 3},
                    "outputs": {"finish_reason": "tool_calls"},
                    "tokens": {"input": 280, "output": 65},
                    "error": None,
                    "children": [],
                },
                {
                    "name": "tool_search_knowledge_base",
                    "span_type": "tool_call",
                    "start_time": 3001.0,
                    "end_time": 3001.02,
                    "duration_ms": 20.0,
                    "inputs": {
                        "tool": "search_knowledge_base",
                        "input": {"query": "HPA メトリクス CPU"},
                    },
                    "outputs": {"result": "[]"},
                    "tokens": {},
                    "error": "No results — query too specific for knowledge base",
                    "children": [],
                },
                {
                    "name": "llm_call_3",
                    "span_type": "llm_call",
                    "start_time": 3001.1,
                    "end_time": 3004.8,
                    "duration_ms": 3700.0,
                    "inputs": {"message_count": 5},
                    "outputs": {"finish_reason": "stop"},
                    "tokens": {"input": 380, "output": 100},
                    "error": None,
                    "children": [],
                },
            ],
        }
    ],
}

# 失敗2: エージェントは結果を見つけたが、ドキュメントにない情報をハルシネートした
TRACE_HALLUCINATION = {
    "trace_id": "fail_hallucination",
    "question": "どのようなキャッシュ戦略がありますか？",
    "expected_answer_contains": "cache-aside",
    "spans": [
        {
            "name": "answer_question",
            "span_type": "agent_step",
            "start_time": 2000.0,
            "end_time": 2004.0,
            "duration_ms": 4000.0,
            "inputs": {"question": "どのようなキャッシュ戦略がありますか？"},
            "outputs": {
                "answer": (
                    "主なキャッシュ戦略はcache-aside、write-through、write-behind、そして"
                    "一貫性ハッシュを使った分散キャッシュです。静的アセットにはCloudflareに"
                    "よるCDNレベルのキャッシュも検討すべきです。"
                ),
            },
            "tokens": {},
            "error": "hallucination_detected",
            "children": [
                {
                    "name": "llm_call_1",
                    "span_type": "llm_call",
                    "start_time": 2000.1,
                    "end_time": 2001.3,
                    "duration_ms": 1200.0,
                    "inputs": {"message_count": 1},
                    "outputs": {"finish_reason": "tool_calls"},
                    "tokens": {"input": 150, "output": 60},
                    "error": None,
                    "children": [],
                },
                {
                    "name": "tool_search_knowledge_base",
                    "span_type": "tool_call",
                    "start_time": 2001.3,
                    "end_time": 2001.32,
                    "duration_ms": 20.0,
                    "inputs": {
                        "tool": "search_knowledge_base",
                        "input": {"query": "キャッシュ戦略"},
                    },
                    "outputs": {"result": "[{'id': 'doc_008', 'title': 'キャッシュ戦略'}]"},
                    "tokens": {},
                    "error": None,
                    "children": [],
                },
                {
                    "name": "llm_call_2",
                    "span_type": "llm_call",
                    "start_time": 2001.4,
                    "end_time": 2002.8,
                    "duration_ms": 1400.0,
                    "inputs": {"message_count": 3},
                    "outputs": {"finish_reason": "tool_calls"},
                    "tokens": {"input": 300, "output": 50},
                    "error": None,
                    "children": [],
                },
                {
                    "name": "tool_get_document",
                    "span_type": "tool_call",
                    "start_time": 2002.8,
                    "end_time": 2002.81,
                    "duration_ms": 10.0,
                    "inputs": {"tool": "get_document", "input": {"doc_id": "doc_008"}},
                    "outputs": {
                        "result": (
                            "キャッシュはレイテンシを削減します...戦略: cache-aside、"
                            "write-through、write-behind。RedisまたはMemcachedを使用してください。"
                        ),
                    },
                    "tokens": {},
                    "error": None,
                    "children": [],
                },
                {
                    "name": "llm_call_3",
                    "span_type": "llm_call",
                    "start_time": 2002.9,
                    "end_time": 2003.9,
                    "duration_ms": 1000.0,
                    "inputs": {"message_count": 5},
                    "outputs": {
                        "finish_reason": "stop",
                        "answer_includes_hallucination": True,
                        "hallucinated_claims": [
                            "一貫性ハッシュを使った分散キャッシュ",
                            "CloudflareによるCDNレベルのキャッシュ",
                        ],
                    },
                    "tokens": {"input": 500, "output": 150},
                    "error": "LLM added claims not present in retrieved documents",
                    "children": [],
                },
            ],
        }
    ],
}

# 失敗3: エージェントがループにはまり、同じ呼び出しを繰り返した
TRACE_LOOP = {
    "trace_id": "fail_loop",
    "question": "マイクロサービスとイベント駆動アーキテクチャを比較してください",
    "expected_answer_contains": "マイクロサービス",
    "spans": [
        {
            "name": "answer_question",
            "span_type": "agent_step",
            "start_time": 3000.0,
            "end_time": 3020.0,
            "duration_ms": 20000.0,
            "inputs": {
                "question": "マイクロサービスとイベント駆動アーキテクチャを比較してください"
            },
            "outputs": {"answer": "Max iterations reached"},
            "tokens": {},
            "error": "Max iterations reached",
            "children": [
                {
                    "name": f"llm_call_{i}",
                    "span_type": "llm_call",
                    "start_time": 3000.0 + i * 2,
                    "end_time": 3001.5 + i * 2,
                    "duration_ms": 1500.0,
                    "inputs": {"message_count": 1 + i * 2},
                    "outputs": {"finish_reason": "tool_calls"},
                    "tokens": {"input": 200 + i * 100, "output": 60},
                    "error": None,
                    "children": [],
                }
                for i in range(8)
            ]
            + [
                {
                    "name": f"tool_search_knowledge_base_{i}",
                    "span_type": "tool_call",
                    "start_time": 3001.5 + i * 2,
                    "end_time": 3001.52 + i * 2,
                    "duration_ms": 20.0,
                    "inputs": {
                        "tool": "search_knowledge_base",
                        "input": {"query": "マイクロサービス" if i % 2 == 0 else "イベント駆動"},
                    },
                    "outputs": {
                        "result": ("[{'id': 'doc_001'}]" if i % 2 == 0 else "[{'id': 'doc_007'}]"),
                    },
                    "tokens": {},
                    "error": None,
                    "children": [],
                }
                for i in range(8)
            ],
        }
    ],
}

ALL_FAILING_TRACES = {
    "wrong_search": TRACE_WRONG_SEARCH,
    "hallucination": TRACE_HALLUCINATION,
    "loop": TRACE_LOOP,
}


# ---------------------------------------------------------------------------
# デバッグツール
# ---------------------------------------------------------------------------


class TraceDebugger:
    """実行トレースを使ってエージェントの失敗をデバッグする。"""

    def find_failure_point(self, trace: dict[str, Any]) -> dict[str, Any] | None:
        """トレースをたどり、エラーを持つ最初のスパンを見つける。"""
        all_spans = collect_all_spans(trace.get("spans", []))
        for span in all_spans:
            if span.get("error"):
                return {
                    "span_name": span["name"],
                    "span_type": span.get("span_type", "unknown"),
                    "error": span["error"],
                    "inputs": span.get("inputs", {}),
                    "outputs": span.get("outputs", {}),
                    "duration_ms": span.get("duration_ms", 0),
                }
        return None

    def get_decision_path(self, trace: dict[str, Any]) -> list[dict[str, Any]]:
        """エージェントが行った一連の決定を抽出する。"""
        all_spans = collect_all_spans(trace.get("spans", []))
        decisions: list[dict[str, Any]] = []

        for span in all_spans:
            span_type = span.get("span_type", "")
            if span_type == "agent_step":
                continue  # ルートのラッパーはスキップする

            decision: dict[str, Any] = {
                "step": len(decisions) + 1,
                "name": span["name"],
                "type": span_type,
                "duration_ms": span.get("duration_ms", 0),
            }

            if span_type == "llm_call":
                decision["action"] = "LLM decision"
                decision["outcome"] = span.get("outputs", {}).get("finish_reason", "unknown")
            elif span_type == "tool_call":
                tool_name = span.get("inputs", {}).get("tool", "unknown")
                tool_input = span.get("inputs", {}).get("input", {})
                decision["action"] = f"Called {tool_name}"
                decision["detail"] = json.dumps(tool_input, ensure_ascii=False)
                decision["outcome"] = "error" if span.get("error") else "success"

            if span.get("error"):
                decision["error"] = span["error"]

            decisions.append(decision)

        return decisions

    def suggest_fixes(self, failure: dict[str, Any]) -> list[str]:
        """失敗の種類に基づいて考えられる修正案を提案する。"""
        suggestions: list[str] = []
        error = failure.get("error", "")
        span_type = failure.get("span_type", "")

        # 誤った検索 / 結果なし
        if "no results" in error.lower() or "not found" in error.lower():
            suggestions.append("検索クエリを広げる——より少なく、より一般的な語を使う")
            suggestions.append(
                "フォールバックロジックを追加する: 結果が空の場合、よりシンプルな"
                "キーワードで再試行する"
            )
            suggestions.append("より多くのトピックをカバーするようナレッジベースを拡充する")

        # ハルシネーション
        if "hallucin" in error.lower() or "not present" in error.lower():
            suggestions.append(
                "明示的な根拠付けの指示を追加する: 「検索されたドキュメントの情報のみを"
                "使用すること」"
            )
            suggestions.append("生成後に主張を出典ドキュメントと照合して検証するチェックを実装する")
            suggestions.append("temperatureを下げて創造的な生成を抑える")

        # ループ / 最大反復回数
        if "max iterations" in error.lower() or "loop" in error.lower():
            suggestions.append("既出クエリの集合を追加し、同一検索の繰り返しを防ぐ")
            suggestions.append(
                "max_iterationsを減らし、これまでの情報を要約するフォールバックを追加する"
            )
            suggestions.append(
                "2〜3回検索したら統合するようエージェントに指示するシステムプロンプトに改善する"
            )

        # 遅いスパン
        if span_type == "llm_call" and failure.get("duration_ms", 0) > 10000:
            suggestions.append("プロンプトが長すぎないか確認する——以前のコンテキストを要約する")
            suggestions.append("中間ステップにより高速なモデルの使用を検討する")

        # ツール実行エラー
        if span_type == "tool_call" and error:
            suggestions.append(
                "一時的なエラーに対して指数バックオフ付きのリトライロジックを追加する"
            )
            suggestions.append("実行前にツールの入力を検証する")

        # 汎用
        if not suggestions:
            suggestions.append("エージェントの推論を理解するため、決定パス全体を確認する")
            suggestions.append("失敗したスパンの周辺により詳細なロギングを追加する")

        return suggestions


class TraceReplay:
    """記録済みトレースのチェックポイントからエージェントの実行をリプレイする。"""

    def list_checkpoints(self, trace: dict[str, Any]) -> list[dict[str, Any]]:
        """トレース内で利用可能なチェックポイント（決定ポイント）を一覧表示する。"""
        all_spans = collect_all_spans(trace.get("spans", []))
        checkpoints: list[dict[str, Any]] = []

        for i, span in enumerate(all_spans):
            if span.get("span_type") in ("llm_call", "tool_call"):
                checkpoints.append(
                    {
                        "index": len(checkpoints),
                        "span_index": i,
                        "name": span["name"],
                        "type": span.get("span_type"),
                        "inputs": span.get("inputs", {}),
                        "had_error": bool(span.get("error")),
                    }
                )

        return checkpoints

    def replay_from(
        self,
        trace: dict[str, Any],
        checkpoint_index: int,
        client: OpenRouter | None = None,
    ) -> dict[str, Any]:
        """指定したチェックポイントからリプレイする。ライブLLMを使うこともできる。"""
        checkpoints = self.list_checkpoints(trace)

        if checkpoint_index < 0 or checkpoint_index >= len(checkpoints):
            return {"error": f"Invalid checkpoint index: {checkpoint_index}"}

        checkpoint = checkpoints[checkpoint_index]
        preceding = checkpoints[:checkpoint_index]

        # 先行するステップからコンテキストを構築する
        context: list[dict[str, Any]] = []
        for cp in preceding:
            context.append(
                {
                    "step": cp["name"],
                    "type": cp["type"],
                    "inputs": cp["inputs"],
                }
            )

        result: dict[str, Any] = {
            "checkpoint": checkpoint,
            "preceding_steps": len(preceding),
            "context_summary": context,
        }

        if client is not None:
            # ライブリプレイ: チェックポイントまでのコンテキストでLLM呼び出しを再実行する
            logger.info("Live replay from checkpoint %d: %s", checkpoint_index, checkpoint["name"])
            question = trace.get("question", "")

            system_prompt = (
                "あなたはリサーチアシスタントです。直前のエージェント実行は失敗しました。"
                "あなたはチェックポイントからリプレイしています。提供されたコンテキストを"
                "使って元の質問に答えてください。簡潔に、事実に基づいて回答してください。"
            )

            context_text = f"元の質問: {question}\n\n"
            context_text += "失敗前の実行コンテキスト:\n"
            for step in context:
                context_text += (
                    f"- {step['step']}: {json.dumps(step['inputs'], ensure_ascii=False)}\n"
                )
            context_text += f"\n失敗箇所: {checkpoint['name']}\n"
            context_text += "修正した応答を提供してください。"

            token_tracker = OpenRouterTokenTracker()
            start = time.time()
            response = client.chat.send(  # type: ignore[call-overload]
                model=MODEL,
                max_tokens=1024,
                reasoning={"effort": "none"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": context_text},
                ],
            )
            elapsed = (time.time() - start) * 1000
            assert response.usage is not None
            token_tracker.track(response.usage)

            answer = response.choices[0].message.content or ""

            result["replayed_answer"] = answer
            result["replay_duration_ms"] = round(elapsed, 2)
            result["replay_tokens"] = {
                "input": response.usage.prompt_tokens,
                "output": response.usage.completion_tokens,
            }
        else:
            result["replayed_answer"] = (
                f"[Dry run] Would re-execute from '{checkpoint['name']}' "
                f"with {len(preceding)} preceding steps as context"
            )

        return result


# ---------------------------------------------------------------------------
# 可視化用のヘルパー
# ---------------------------------------------------------------------------


def _build_decision_tree(decisions: list[dict[str, Any]], tree: Tree) -> None:
    """決定ステップをRichのツリーに追加する。"""
    for d in decisions:
        label = f"[bold]ステップ{d['step']}:[/bold] {d['name']}"
        if d.get("action"):
            label += f" — {d['action']}"
        if d.get("outcome"):
            style = "red" if d["outcome"] == "error" else "green"
            label += f" [{style}]({d['outcome']})[/{style}]"
        if d.get("error"):
            label += f"\n  [red]Error: {d['error']}[/red]"
        if d.get("detail"):
            label += f"\n  [dim]{d['detail']}[/dim]"
        tree.add(label)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


def main() -> None:
    """トレースベースのデバッグワークフローを実演する。"""
    console = Console()

    console.print(
        Panel(
            "[bold cyan]トレースデバッグ[/bold cyan]\n\n"
            "失敗したエージェント実行に対し、トレースをたどって失敗箇所を見つけ、\n"
            "決定パスを抽出し、修正案を提案し、リプレイのチェックポイントを一覧表示します。\n\n"
            "コンセプト: 失敗検出、決定パス、修正案の提案、トレースリプレイ",
            title="03 - トレースデバッグ",
        )
    )

    debugger = TraceDebugger()
    replayer = TraceReplay()

    for trace_name, trace in ALL_FAILING_TRACES.items():
        console.print(f"\n{'=' * 80}")
        console.print(
            f"\n[bold magenta]トレースをデバッグ中: {trace_name}[/bold magenta]"
            f"\n[dim]質問: {trace.get('question', 'N/A')}[/dim]\n"
        )

        # ステップ1: 失敗箇所を見つける
        failure = debugger.find_failure_point(trace)
        if failure:
            console.print(
                Panel(
                    f"[bold red]失敗箇所[/bold red]\n\n"
                    f"スパン: {failure['span_name']} ({failure['span_type']})\n"
                    f"エラー: {failure['error']}\n"
                    f"所要時間: {failure['duration_ms']:.0f}ms",
                    title="失敗を検出",
                    border_style="red",
                )
            )
        else:
            console.print("[green]トレース内に明示的な失敗は見つかりませんでした[/green]")

        # ステップ2: 決定パスを表示する
        decisions = debugger.get_decision_path(trace)
        decision_tree = Tree(f"[bold]決定パス（{len(decisions)}ステップ）[/bold]")
        _build_decision_tree(decisions, decision_tree)
        console.print(decision_tree)

        # ステップ3: 修正案を提案する
        if failure:
            suggestions = debugger.suggest_fixes(failure)
            console.print("\n[bold yellow]修正案:[/bold yellow]")
            for i, suggestion in enumerate(suggestions, 1):
                console.print(f"  {i}. {suggestion}")

        # ステップ4: リプレイのチェックポイントを一覧表示する
        checkpoints = replayer.list_checkpoints(trace)
        if checkpoints:
            cp_table = Table(title="リプレイチェックポイント")
            cp_table.add_column("インデックス", justify="center")
            cp_table.add_column("名前")
            cp_table.add_column("種別")
            cp_table.add_column("エラーあり", justify="center")
            for cp in checkpoints:
                error_marker = "[red]Yes[/red]" if cp["had_error"] else "[green]No[/green]"
                cp_table.add_row(
                    str(cp["index"]),
                    cp["name"],
                    cp["type"],
                    error_marker,
                )
            console.print()
            console.print(cp_table)

        # ステップ5: 最初にエラーが発生したチェックポイントからドライランでリプレイする
        errored_cps = [cp for cp in checkpoints if cp["had_error"]]
        if errored_cps:
            first_error_cp = errored_cps[0]["index"]
            console.print(
                f"\n[bold]チェックポイント{first_error_cp}からのドライランリプレイ:[/bold]"
            )

            has_api_key = bool(os.environ.get("OPENROUTER_API_KEY"))
            client = (
                OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", "")) if has_api_key else None
            )

            replay_result = replayer.replay_from(trace, first_error_cp, client=client)
            console.print(f"  [dim]{replay_result['replayed_answer']}[/dim]")

            if replay_result.get("replay_tokens"):
                tokens = replay_result["replay_tokens"]
                console.print(
                    f"  [dim]Replay tokens: {tokens['input']}in / {tokens['output']}out, "
                    f"duration: {replay_result.get('replay_duration_ms', 0):.0f}ms[/dim]"
                )

    console.print(f"\n{'=' * 80}")
    console.print("\n[bold green]デバッグワークフローが完了しました。[/bold green]")


if __name__ == "__main__":
    main()
