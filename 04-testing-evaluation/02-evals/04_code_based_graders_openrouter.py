"""
エージェント評価のためのコードベースグレーダー (OpenRouter)

決定的なグレーダー（キーワードマッチング・正規表現パターン・出典引用の検証・
ツール呼び出しチェック）を使ったエージェント応答の評価を実演する。
リサーチアシスタントを正解データセットに対して実行し、各応答を採点する。
"""

import json
import os
from pathlib import Path
from typing import Any

from common import setup_logging
from dotenv import find_dotenv, load_dotenv
from openrouter import OpenRouter
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from shared_openrouter.agent import ResearchAssistant
from shared_openrouter.graders import (
    GraderResult,
    KeywordGrader,
    RegexGrader,
    SourceCitationGrader,
    ToolCallGrader,
)
from shared_openrouter.knowledge_base import KNOWLEDGE_BASE

load_dotenv(find_dotenv())

logger = setup_logging(__name__)

# ---------------------------------------------------------------------------
# デモモード用のシミュレートされた応答（APIキーがない場合）
# ---------------------------------------------------------------------------

SIMULATED_RESPONSES: dict[str, dict[str, Any]] = {
    "task_001": {
        "answer": (
            "検索結果（doc_001）によると、マイクロサービスアーキテクチャには"
            "いくつかの重要な利点があります: スケーラビリティ、障害分離、"
            "技術選定の柔軟性です。各サービスは独立してデプロイでき、それぞれ"
            "独自のプロセスで実行されます。"
        ),
        "tool_calls": [
            {
                "name": "search_knowledge_base",
                "input": {"query": "マイクロサービス 利点"},
                "results": [KNOWLEDGE_BASE[0]],
            }
        ],
        "sources": [[KNOWLEDGE_BASE[0]]],
    },
    "task_002": {
        "answer": (
            "doc_002によると、REST APIのベストプラクティスは次の通りです: "
            "/usersや/ordersのようにエンドポイントには名詞を使う、アクションには"
            "HTTPメソッド（GET、POST、PUT、DELETE）を使う、適切なステータスコード"
            "を使う、バージョニングを実装する、コレクションにはページネーション"
            "を使う。"
        ),
        "tool_calls": [
            {
                "name": "search_knowledge_base",
                "input": {"query": "REST API 設計"},
                "results": [KNOWLEDGE_BASE[1]],
            }
        ],
        "sources": [[KNOWLEDGE_BASE[1]]],
    },
    "task_003": {
        "answer": (
            "doc_003によると、データベースインデックスは効率的な検索構造を"
            "作ることでクエリのパフォーマンスを向上させます。B-treeインデックス"
            "は等価検索と範囲検索を処理します。クエリプランを分析するには"
            "EXPLAINを使用してください。"
        ),
        "tool_calls": [
            {
                "name": "search_knowledge_base",
                "input": {"query": "データベース インデックス パフォーマンス"},
                "results": [KNOWLEDGE_BASE[2]],
            }
        ],
        "sources": [[KNOWLEDGE_BASE[2]]],
    },
}


# ---------------------------------------------------------------------------
# 評価ランナー
# ---------------------------------------------------------------------------


def load_golden_tasks(path: str) -> list[dict[str, Any]]:
    """JSONデータセットファイルから評価タスクを読み込む。"""
    with Path(path).open(encoding="utf-8") as f:
        data = json.load(f)
    logger.info("Loaded %d tasks from %s (v%s)", len(data["tasks"]), path, data["version"])
    tasks: list[dict[str, Any]] = data["tasks"]
    return tasks


def evaluate_task(
    task: dict[str, Any],
    agent_response: dict[str, Any],
    keyword_grader: KeywordGrader,
    citation_grader: SourceCitationGrader,
    tool_grader: ToolCallGrader,
    regex_grader: RegexGrader,
) -> dict[str, GraderResult]:
    """1つのタスクのエージェント応答に対して、すべてのグレーダーを実行する。"""
    answer = agent_response["answer"]
    tool_calls = agent_response.get("tool_calls", [])

    results: dict[str, GraderResult] = {}
    results["keywords"] = keyword_grader.grade(answer, task["expected_keywords"])
    results["citations"] = citation_grader.grade(answer, task["expected_source_ids"])
    results["tool_calls"] = tool_grader.grade(tool_calls)

    # 正規表現チェック: 回答にdoc_XXX形式の引用パターン（または対応不可の旨）が含まれるべき
    if task["expected_source_ids"]:
        results["regex"] = regex_grader.grade(answer, r"doc_\d{3}")
    else:
        results["regex"] = regex_grader.grade(
            answer, r"(?:関連する情報|見つかりません|情報がありません|持っていません)"
        )

    return results


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


def main() -> None:
    """正解データセットに対してコードベースグレーダーを実行する。"""
    console = Console()
    console.print(
        Panel(
            "[bold cyan]コードベースグレーダー[/bold cyan]\n\n"
            "決定的なグレーダーを使ってリサーチアシスタントを評価します:\n"
            "キーワードマッチング・正規表現・出典引用・ツール呼び出しの検証。",
            title="評価チュートリアル1",
        )
    )

    # モードを決定する: ライブAPIかシミュレーションか
    has_api_key = bool(os.environ.get("OPENROUTER_API_KEY"))
    if has_api_key:
        console.print("[green]APIキーが見つかりました — ライブ評価を実行します[/green]\n")
        client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        agent = ResearchAssistant(client, KNOWLEDGE_BASE)
    else:
        console.print(
            "[yellow]APIキーが見つかりません — デモ用にシミュレートされた応答を使用します[/yellow]\n"
        )
        agent = None

    # 正解データセットを読み込む
    dataset_path = Path(__file__).parent / "datasets_openrouter" / "golden_tasks.json"
    tasks = load_golden_tasks(str(dataset_path))

    # グレーダーをインスタンス化する
    keyword_grader = KeywordGrader()
    citation_grader = SourceCitationGrader()
    tool_grader = ToolCallGrader()
    regex_grader = RegexGrader()

    # 結果テーブル
    table = Table(title="評価結果", show_lines=True)
    table.add_column("タスク", style="cyan", width=12)
    table.add_column("難易度", width=8)
    table.add_column("キーワード", width=18)
    table.add_column("出典引用", width=18)
    table.add_column("ツール呼び出し", width=18)
    table.add_column("正規表現", width=18)

    total_scores: dict[str, list[float]] = {
        "keywords": [],
        "citations": [],
        "tool_calls": [],
        "regex": [],
    }

    # シミュレーションモードでは、簡潔なデモのために最初の数タスクに限定する
    eval_tasks = tasks[:3] if agent is None else tasks
    console.print(f"{len(eval_tasks)}件のタスクを実行します...\n")

    for task in eval_tasks:
        logger.info("Evaluating task %s: %s", task["id"], task["question"][:60])

        # エージェントの応答を取得する（ライブまたはシミュレーション）
        if agent is not None:
            try:
                response = agent.answer(task["question"])
            except Exception as e:
                logger.error("Agent error on %s: %s", task["id"], e)
                response = {"answer": f"Error: {e}", "tool_calls": [], "sources": []}
        else:
            response = SIMULATED_RESPONSES.get(
                task["id"],
                {"answer": "No simulated response available.", "tool_calls": [], "sources": []},
            )

        # 応答を採点する
        grader_results = evaluate_task(
            task, response, keyword_grader, citation_grader, tool_grader, regex_grader
        )

        # テーブル表示用にフォーマットする
        def fmt(result: GraderResult) -> str:
            icon = "[green]PASS[/green]" if result.passed else "[red]FAIL[/red]"
            return f"{icon} ({result.score:.0%})"

        table.add_row(
            task["id"],
            task["difficulty"],
            fmt(grader_results["keywords"]),
            fmt(grader_results["citations"]),
            fmt(grader_results["tool_calls"]),
            fmt(grader_results["regex"]),
        )

        for grader_name, result in grader_results.items():
            total_scores[grader_name].append(result.score)

    console.print(table)

    # サマリー
    console.print("\n[bold]集計スコア[/bold]")
    for grader_name, scores in total_scores.items():
        if scores:
            avg = sum(scores) / len(scores)
            console.print(f"  {grader_name:12s}: {avg:.0%} avg ({len(scores)} tasks)")

    # トークン使用量レポート（ライブモードのみ）
    if agent is not None:
        console.print()
        agent.token_tracker.report()


if __name__ == "__main__":
    main()
