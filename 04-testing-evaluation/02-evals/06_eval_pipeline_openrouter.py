"""
エンドツーエンド評価パイプライン (OpenRouter)

完全なevalパイプラインを実演する: 正解データセットの読み込み、エージェント
トライアルの実行、複数グレーダーによる採点、結果の集計、リグレッションの検出。
pass@k（少なくとも1回成功）とpass^k（全回成功）のメトリクスをレポートし、
評価タイプ（capability vs regression）別に結果を分解する。
"""

import json
import os
import time
from dataclasses import dataclass
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
    SourceCitationGrader,
    ToolCallGrader,
)
from shared_openrouter.knowledge_base import KNOWLEDGE_BASE

load_dotenv(find_dotenv())

logger = setup_logging(__name__)


# ---------------------------------------------------------------------------
# パイプラインのデータ構造
# ---------------------------------------------------------------------------


@dataclass
class EvalTask:
    """単一の評価タスク。"""

    id: str
    question: str
    expected_keywords: list[str]
    expected_source_ids: list[str]
    difficulty: str
    category: str
    eval_type: str = "capability"  # "capability"（新機能）または"regression"（壊れてはいけない）


@dataclass
class EvalTrial:
    """タスクに対するエージェントの1回の実行。"""

    task_id: str
    trial_number: int
    answer: str
    tool_calls: list[dict[str, Any]]
    sources: list[Any]
    latency_ms: float


@dataclass
class EvalResult:
    """複数トライアルにわたる、タスクごとの集計結果。"""

    task_id: str
    trials: list[EvalTrial]
    grader_results: dict[str, list[GraderResult]]
    pass_rate: float
    avg_score: float
    # pass@k: k回のトライアルのうち少なくとも1回成功する確率（楽観的——能力を測る）
    pass_at_k: float = 0.0
    # pass^k: k回のトライアルすべてが成功する確率（厳格——一貫性を測る）
    pass_pow_k: float = 0.0


# ---------------------------------------------------------------------------
# デモモード用のシミュレートされた応答
# ---------------------------------------------------------------------------

# 各タスクはトライアル応答のリストにマッピングされ、run_trialがそれらを順に使う。
# トライアル間のばらつきは、pass@kとpass^kの違いを実演するためのもの。
SIMULATED_RESPONSES: dict[str, dict[str, Any]] = {
    "task_001": {
        "answer": (
            "doc_001によると、マイクロサービスアーキテクチャの主な利点は"
            "スケーラビリティ、障害分離、サービスを独立してデプロイできることです。"
            "各サービスは独自のプロセスで実行され、API経由で通信します。"
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
            "doc_002によると、REST APIのベストプラクティスは、エンドポイントに"
            "名詞を使う（例: /users）、アクションにHTTPメソッド（GET、POST、PUT、"
            "DELETE）を使う、適切なステータスコードを使うことです。また、"
            "コレクションにはバージョニングとページネーションを使用します。"
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
            "doc_003によると、データベースインデックスは効率的な検索構造に"
            "よってクエリのパフォーマンスを向上させます。B-treeインデックスは"
            "等価検索と範囲検索を処理します。クエリプランの分析にはEXPLAINを"
            "使用してください。"
        ),
        "tool_calls": [
            {
                "name": "search_knowledge_base",
                "input": {"query": "データベース インデックス"},
                "results": [KNOWLEDGE_BASE[2]],
            }
        ],
        "sources": [[KNOWLEDGE_BASE[2]]],
    },
    "task_004": {
        "answer": (
            "doc_004によると、認証は本人確認（あなたが誰か）を行い、認可は"
            "アクセス制御（何ができるか）を行います。JWTトークンはステートレスな"
            "認証を提供します。パスワードは必ずbcryptまたはargon2でハッシュ化"
            "してください。"
        ),
        "tool_calls": [
            {
                "name": "search_knowledge_base",
                "input": {"query": "認証 認可"},
                "results": [KNOWLEDGE_BASE[3]],
            }
        ],
        "sources": [[KNOWLEDGE_BASE[3]]],
    },
    "task_005": {
        "answer": (
            "doc_005によると、CI/CDの主な実践には、コミットのたびに自動で"
            "コードをビルド・テストする継続的インテグレーションと、成功した"
            "ビルドを本番環境にデプロイする継続的デプロイが含まれます。"
            "高速なフィードバックループとトランクベース開発が重要です。"
        ),
        "tool_calls": [
            {
                "name": "search_knowledge_base",
                "input": {"query": "CI/CD パイプライン"},
                "results": [KNOWLEDGE_BASE[4]],
            }
        ],
        "sources": [[KNOWLEDGE_BASE[4]]],
    },
    "task_013": {
        "answer": (
            "機械学習のプログラミング言語について、関連する情報が見つかりません"
            "でした。ナレッジベースには情報がありません。"
        ),
        "tool_calls": [
            {
                "name": "search_knowledge_base",
                "input": {"query": "機械学習 プログラミング言語"},
                "results": [],
            }
        ],
        "sources": [[]],
    },
}

# 非決定的なLLMの挙動をシミュレートするための、トライアルごとの上書き。
# 該当するトライアル番号がない場合はデフォルトのSIMULATED_RESPONSESにフォールバックする。
SIMULATED_TRIAL_OVERRIDES: dict[str, dict[int, dict[str, Any]]] = {
    "task_001": {
        # トライアル2: 期待されるキーワードを欠いた弱い回答——不整合を示す
        2: {
            "answer": (
                "マイクロサービスを使うと、アプリケーションをネットワーク経由で"
                "通信する小さなサービスに分割できます。"
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
    },
    "task_003": {
        # トライアル3: 出典引用を欠いた回答——citationグレーダーで失敗する
        3: {
            "answer": (
                "データベースインデックスは効率的なB-tree検索構造によって"
                "パフォーマンスを向上させます。クエリプランの分析にはEXPLAINを"
                "使用してください。"
            ),
            "tool_calls": [
                {
                    "name": "search_knowledge_base",
                    "input": {"query": "データベース インデックス"},
                    "results": [KNOWLEDGE_BASE[2]],
                }
            ],
            "sources": [[]],  # 出典が引用されていない
        },
    },
}


# ---------------------------------------------------------------------------
# Evalパイプライン
# ---------------------------------------------------------------------------


class EvalPipeline:
    """複数グレーダーによる採点を伴うエンドツーエンドの評価パイプライン。"""

    def __init__(self, agent: ResearchAssistant | None = None) -> None:
        self.agent = agent
        self.keyword_grader = KeywordGrader()
        self.citation_grader = SourceCitationGrader()
        self.tool_grader = ToolCallGrader()

    def load_tasks(self, path: str) -> list[EvalTask]:
        """JSONファイルから評価タスクを読み込み、パースする。"""
        with Path(path).open(encoding="utf-8") as f:
            data = json.load(f)
        tasks = [
            EvalTask(
                id=t["id"],
                question=t["question"],
                expected_keywords=t["expected_keywords"],
                expected_source_ids=t["expected_source_ids"],
                difficulty=t["difficulty"],
                category=t["category"],
                eval_type=t.get("eval_type", "capability"),
            )
            for t in data["tasks"]
        ]
        logger.info("Loaded %d eval tasks from %s", len(tasks), path)
        return tasks

    def run_trial(self, task: EvalTask, trial_number: int = 1) -> EvalTrial:
        """1回のトライアルを実行する——エージェントを動かしレイテンシを測定する。"""
        start = time.perf_counter()

        if self.agent is not None:
            try:
                response = self.agent.answer(task.question)
            except Exception as e:
                logger.error("Agent error on %s: %s", task.id, e)
                response = {"answer": f"Error: {e}", "tool_calls": [], "sources": []}
        else:
            # まずトライアル固有の上書きを確認し、なければデフォルトにフォールバックする
            overrides = SIMULATED_TRIAL_OVERRIDES.get(task.id, {})
            response = overrides.get(
                trial_number,
                SIMULATED_RESPONSES.get(
                    task.id,
                    {"answer": "No simulated response.", "tool_calls": [], "sources": []},
                ),
            )

        elapsed_ms = (time.perf_counter() - start) * 1000

        return EvalTrial(
            task_id=task.id,
            trial_number=0,
            answer=response["answer"],
            tool_calls=response.get("tool_calls", []),
            sources=response.get("sources", []),
            latency_ms=elapsed_ms,
        )

    def grade_trial(self, task: EvalTask, trial: EvalTrial) -> dict[str, GraderResult]:
        """1回のトライアルにすべてのグレーダーを適用する。"""
        return {
            "keywords": self.keyword_grader.grade(trial.answer, task.expected_keywords),
            "citations": self.citation_grader.grade(trial.answer, task.expected_source_ids),
            "tool_calls": self.tool_grader.grade(trial.tool_calls),
        }

    def run_evaluation(self, tasks: list[EvalTask], num_trials: int = 1) -> list[EvalResult]:
        """全体の評価を実行する: タスクごとに複数トライアルを実行し、それぞれ採点する。"""
        results: list[EvalResult] = []

        for task in tasks:
            logger.info("Evaluating %s (%s, %s)", task.id, task.difficulty, task.category)
            trials: list[EvalTrial] = []
            all_grader_results: dict[str, list[GraderResult]] = {
                "keywords": [],
                "citations": [],
                "tool_calls": [],
            }

            for trial_num in range(num_trials):
                trial = self.run_trial(task, trial_number=trial_num + 1)
                trial.trial_number = trial_num + 1
                trials.append(trial)

                grader_results = self.grade_trial(task, trial)
                for name, result in grader_results.items():
                    all_grader_results[name].append(result)

            # どのトライアルが合格したかを判定する（すべてのグレーダーが合格する必要がある）
            pass_count = 0
            for i in range(num_trials):
                all_passed = all(all_grader_results[g][i].passed for g in all_grader_results)
                if all_passed:
                    pass_count += 1
            pass_rate = pass_count / num_trials

            # pass@k: 少なくとも1回のトライアルが成功した（楽観的——能力を測る）
            pass_at_k = 1.0 if pass_count > 0 else 0.0
            # pass^k: すべてのトライアルが成功した（厳格——一貫性/信頼性を測る）
            pass_pow_k = 1.0 if pass_count == num_trials else 0.0

            # すべてのグレーダー・トライアルにわたる平均スコア
            all_scores = [
                r.score for grader_list in all_grader_results.values() for r in grader_list
            ]
            avg_score = sum(all_scores) / len(all_scores) if all_scores else 0.0

            results.append(
                EvalResult(
                    task_id=task.id,
                    trials=trials,
                    grader_results=all_grader_results,
                    pass_rate=pass_rate,
                    avg_score=avg_score,
                    pass_at_k=pass_at_k,
                    pass_pow_k=pass_pow_k,
                )
            )

        return results

    def detect_regressions(
        self, current: list[EvalResult], baseline: list[EvalResult]
    ) -> list[str]:
        """現在の結果をベースラインと比較し、リグレッションを検出する。"""
        baseline_map = {r.task_id: r for r in baseline}
        regressions: list[str] = []

        for result in current:
            base = baseline_map.get(result.task_id)
            if base is None:
                continue

            # 合格率が下がっていればフラグを立てる
            if result.pass_rate < base.pass_rate:
                regressions.append(
                    f"{result.task_id}: pass rate {base.pass_rate:.0%} -> {result.pass_rate:.0%}"
                )

            # 平均スコアが大きく（0.1以上）下がっていればフラグを立てる
            if result.avg_score < base.avg_score - 0.1:
                regressions.append(
                    f"{result.task_id}: avg score {base.avg_score:.2f} -> {result.avg_score:.2f}"
                )

        return regressions


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


def main() -> None:
    """エンドツーエンドの評価パイプラインを実行する。"""
    console = Console()
    console.print(
        Panel(
            "[bold cyan]評価パイプライン[/bold cyan]\n\n"
            "エンドツーエンドのパイプライン: 正解データセットの読み込み、エージェント\n"
            "トライアルの実行、複数グレーダーによる採点、pass@kとpass^kの集計、\n"
            "評価タイプ（capability vs regression）別の内訳、リグレッション検出。",
            title="評価チュートリアル3",
        )
    )

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

    pipeline = EvalPipeline(agent=agent)

    # タスクを読み込む
    dataset_path = Path(__file__).parent / "datasets_openrouter" / "golden_tasks.json"
    all_tasks = pipeline.load_tasks(str(dataset_path))

    # シミュレーションモードでは、シミュレートされた応答があるタスクに限定する
    if agent is None:
        eval_tasks = [t for t in all_tasks if t.id in SIMULATED_RESPONSES]
        console.print(f"{len(eval_tasks)}件のタスクを実行します（シミュレーションモード）...\n")
    else:
        eval_tasks = all_tasks
        console.print(f"{len(eval_tasks)}件のタスクを実行します...\n")

    # 評価を実行する——pass@kとpass^kの違いを示すため、シミュレーションモードでは3トライアル使う
    num_trials = 3 if agent is None else 1
    results = pipeline.run_evaluation(eval_tasks, num_trials=num_trials)

    # タスクごとの結果テーブル
    table = Table(title="タスクごとの結果", show_lines=True)
    table.add_column("タスク", style="cyan", width=12)
    table.add_column("種別", width=12)
    table.add_column("難易度", width=10)
    table.add_column("キーワード", width=10, justify="center")
    table.add_column("出典引用", width=10, justify="center")
    table.add_column("ツール", width=10, justify="center")
    table.add_column("pass@k", width=8, justify="center")
    table.add_column("pass^k", width=8, justify="center")
    table.add_column("レイテンシ", width=10, justify="right")

    def grader_cell(grader_name: str, eval_result: "EvalResult") -> str:
        """グレーダーのスコアを色付きのRichセルとして整形する。"""
        scores = eval_result.grader_results[grader_name]
        avg = sum(r.score for r in scores) / len(scores) if scores else 0.0
        color = "green" if avg >= 0.7 else ("yellow" if avg >= 0.4 else "red")
        return f"[{color}]{avg:.0%}[/{color}]"

    for result in results:
        task = next(t for t in eval_tasks if t.id == result.task_id)

        avg_latency = sum(t.latency_ms for t in result.trials) / len(result.trials)
        at_k_color = "green" if result.pass_at_k == 1.0 else "red"
        pow_k_color = "green" if result.pass_pow_k == 1.0 else "red"

        table.add_row(
            result.task_id,
            task.eval_type,
            task.difficulty,
            grader_cell("keywords", result),
            grader_cell("citations", result),
            grader_cell("tool_calls", result),
            f"[{at_k_color}]{result.pass_at_k:.0%}[/{at_k_color}]",
            f"[{pow_k_color}]{result.pass_pow_k:.0%}[/{pow_k_color}]",
            f"{avg_latency:.0f}ms",
        )

    console.print(table)

    # 集計メトリクス
    total_at_k = sum(r.pass_at_k for r in results) / len(results) if results else 0.0
    total_pow_k = sum(r.pass_pow_k for r in results) / len(results) if results else 0.0
    total_score = sum(r.avg_score for r in results) / len(results) if results else 0.0

    # 評価タイプ別の内訳（capability vs regression）
    eval_types: dict[str, list[EvalResult]] = {}
    for result in results:
        task = next(t for t in eval_tasks if t.id == result.task_id)
        eval_types.setdefault(task.eval_type, []).append(result)

    type_table = Table(title="評価タイプ別の内訳")
    type_table.add_column("評価タイプ", style="bold")
    type_table.add_column("タスク数", justify="center")
    type_table.add_column("pass@k", justify="center")
    type_table.add_column("pass^k", justify="center")
    type_table.add_column("平均スコア", justify="center")

    for etype, etype_results in sorted(eval_types.items()):
        e_at_k = sum(r.pass_at_k for r in etype_results) / len(etype_results)
        e_pow_k = sum(r.pass_pow_k for r in etype_results) / len(etype_results)
        e_score = sum(r.avg_score for r in etype_results) / len(etype_results)
        type_table.add_row(
            etype, str(len(etype_results)), f"{e_at_k:.0%}", f"{e_pow_k:.0%}", f"{e_score:.2f}"
        )

    console.print(type_table)

    # カテゴリ別の内訳
    categories: dict[str, list[EvalResult]] = {}
    for result in results:
        task = next(t for t in eval_tasks if t.id == result.task_id)
        categories.setdefault(task.category, []).append(result)

    cat_table = Table(title="カテゴリ別の内訳")
    cat_table.add_column("カテゴリ", style="bold")
    cat_table.add_column("タスク数", justify="center")
    cat_table.add_column("pass@k", justify="center")
    cat_table.add_column("pass^k", justify="center")
    cat_table.add_column("平均スコア", justify="center")

    for cat, cat_results in sorted(categories.items()):
        cat_at_k = sum(r.pass_at_k for r in cat_results) / len(cat_results)
        cat_pow_k = sum(r.pass_pow_k for r in cat_results) / len(cat_results)
        cat_score = sum(r.avg_score for r in cat_results) / len(cat_results)
        cat_table.add_row(
            cat, str(len(cat_results)), f"{cat_at_k:.0%}", f"{cat_pow_k:.0%}", f"{cat_score:.2f}"
        )

    console.print(cat_table)

    console.print(f"\n[bold]総合 pass@{num_trials}:[/bold] {total_at_k:.0%}")
    console.print(f"[bold]総合 pass^{num_trials}:[/bold] {total_pow_k:.0%}")
    console.print(f"[bold]総合 平均スコア:[/bold] {total_score:.2f}")

    # リグレッション検出のデモ
    # デモのため、スコアがやや良い「ベースライン」をシミュレートする
    console.print("\n[bold]リグレッション検出[/bold]")
    baseline = [
        EvalResult(
            task_id=r.task_id,
            trials=r.trials,
            grader_results=r.grader_results,
            pass_rate=min(r.pass_rate + 0.1, 1.0),
            avg_score=min(r.avg_score + 0.15, 1.0),
            pass_at_k=min(r.pass_at_k + 0.1, 1.0),
            pass_pow_k=min(r.pass_pow_k + 0.1, 1.0),
        )
        for r in results
    ]

    regressions = pipeline.detect_regressions(results, baseline)
    if regressions:
        console.print(f"[red]{len(regressions)}件のリグレッションが見つかりました:[/red]")
        for reg in regressions:
            console.print(f"  [red]- {reg}[/red]")
    else:
        console.print("[green]リグレッションは検出されませんでした[/green]")

    # トークン使用量
    if agent is not None:
        console.print()
        agent.token_tracker.report()


if __name__ == "__main__":
    main()
