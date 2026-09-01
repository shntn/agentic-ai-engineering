"""
Braintrust AutoEvals — エージェント評価向けの構築済みスコアラー (OpenRouter)

Braintrustの`autoevals`ライブラリを使ってエージェント応答を評価する方法を示す。
AutoEvalsはすぐに使えるスコアラーを提供する: 文字列類似度（ローカル、APIキー不要）、
factualityなどLLMベースのスコアラー（OpenRouter経由のOpenAI互換クライアントが必要）、
カスタムLLM分類器。

このスクリプトの流れ:
1. 文字列ベースのスコアラー（Levenshtein、ExactMatch）を示す — 完全にローカル
2. LLMベースのスコアラー（Factuality、ClosedQA）を示す — OPENROUTER_API_KEYが必要
3. ドメイン固有の採点用にカスタムLLM分類器を構築
4. リサーチアシスタントの応答に対して全スコアラーを実行

Install: pip install autoevals openai
"""

import os

from common import setup_logging
from dotenv import find_dotenv, load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from shared_openrouter.knowledge_base import EVAL_TASKS, get_agent_response

load_dotenv(find_dotenv())

logger = setup_logging(__name__)

# OpenRouter経由でLLMスコアラーに使うデフォルトモデル
DEFAULT_MODEL = "deepseek/deepseek-v4-flash-0731"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


# ---------------------------------------------------------------------------
# デモモード用のシミュレートされたスコアラー結果（autoevals未インストール時）
# ---------------------------------------------------------------------------

SIMULATED_SCORES: dict[str, dict[str, float]] = {
    "task_001": {"levenshtein": 0.42, "factuality": 0.9, "closedqa": 0.85},
    "task_002": {"levenshtein": 0.38, "factuality": 0.95, "closedqa": 0.90},
    "task_003": {"levenshtein": 0.45, "factuality": 0.85, "closedqa": 0.80},
    "task_004": {"levenshtein": 0.51, "factuality": 0.90, "closedqa": 0.85},
    "task_005": {"levenshtein": 0.30, "factuality": 0.80, "closedqa": 0.70},
}


def init_openrouter_client() -> None:
    """autoevalsのグローバルクライアントをOpenRouter経由のOpenAI互換クライアントに設定する。

    autoevalsのLLMスコアラーはOpenAI SDK形状のクライアントを想定しているが、
    OpenRouterはOpenAI互換のChat Completions APIを公開しているため、base_urlを
    向け替えるだけで再利用できる（Braintrustプロキシ経由でAnthropicを使う公式
    サンプルと同じ手法）。
    """
    from autoevals import init
    from openai import OpenAI

    init(
        client=OpenAI(
            api_key=os.environ["OPENROUTER_API_KEY"],
            base_url=OPENROUTER_BASE_URL,
        ),
        default_model=DEFAULT_MODEL,
    )


def run_string_scorers(output: str, expected: str, task_id: str) -> dict[str, float]:
    """文字列ベースのスコアラーを実行する（APIキー不要）。"""
    try:
        from autoevals import Levenshtein

        lev = Levenshtein()
        lev_result = lev.eval(output=output, expected=expected)
        return {"levenshtein": lev_result.score or 0.0}
    except ImportError:
        logger.info("autoevals not installed, using simulated scores")
        return {"levenshtein": SIMULATED_SCORES.get(task_id, {}).get("levenshtein", 0.0)}


def run_llm_scorers(question: str, output: str, expected: str, task_id: str) -> dict[str, float]:
    """LLMベースのスコアラーを実行する（OPENROUTER_API_KEYが必要）。"""
    has_openrouter_key = bool(os.environ.get("OPENROUTER_API_KEY"))

    if not has_openrouter_key:
        logger.info("No OPENROUTER_API_KEY, using simulated LLM scorer results")
        sim = SIMULATED_SCORES.get(task_id, {})
        return {
            "factuality": sim.get("factuality", 0.0),
            "closedqa": sim.get("closedqa", 0.0),
        }

    try:
        from autoevals import ClosedQA, Factuality

        scores: dict[str, float] = {}

        # Factuality: 出力がexpectedと事実として整合しているかをチェック
        factuality = Factuality()
        fact_result = factuality.eval(
            input=question,
            output=output,
            expected=expected,
        )
        scores["factuality"] = fact_result.score or 0.0

        # ClosedQA: 質問に対する回答の質を評価する
        closedqa = ClosedQA()
        cqa_result = closedqa.eval(
            input=question,
            output=output,
            expected=expected,
        )
        scores["closedqa"] = cqa_result.score or 0.0

        return scores
    except ImportError:
        logger.info("autoevals not installed, using simulated scores")
        sim = SIMULATED_SCORES.get(task_id, {})
        return {
            "factuality": sim.get("factuality", 0.0),
            "closedqa": sim.get("closedqa", 0.0),
        }
    except Exception as e:
        logger.error("LLM scorer error: %s", e)
        return {"factuality": 0.0, "closedqa": 0.0}


def run_custom_classifier(output: str, task_id: str) -> dict[str, float]:
    """出典の裏付け（source grounding）を評価するカスタムLLM分類器を実行する。"""
    has_openrouter_key = bool(os.environ.get("OPENROUTER_API_KEY"))

    if not has_openrouter_key:
        # シミュレーション: 出力にdoc_XXXパターンが含まれているかをチェック
        has_source = "doc_" in output
        return {"grounding": 1.0 if has_source else 0.0}

    try:
        from autoevals import LLMClassifier

        grounding_classifier = LLMClassifier(
            name="SourceGrounding",
            prompt_template=(
                "以下の応答は、その主張を裏付けるために具体的な出典"
                "（例: doc_001）を引用していますか？\n\n応答: {{output}}\n\n"
                "出典が引用されていればYes、されていなければNoと答えてください。"
            ),
            choice_scores={"Yes": 1.0, "No": 0.0},
        )
        result = grounding_classifier.eval(output=output)
        return {"grounding": result.score or 0.0}
    except ImportError:
        has_source = "doc_" in output
        return {"grounding": 1.0 if has_source else 0.0}
    except Exception as e:
        logger.error("Custom classifier error: %s", e)
        return {"grounding": 0.0}


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


def main() -> None:
    """リサーチアシスタントの応答に対してBraintrust autoevalsのスコアラーを実行する。"""
    console = Console()
    console.print(
        Panel(
            "[bold cyan]Braintrust AutoEvals — 構築済みスコアラー[/bold cyan]\n\n"
            "以下を使ってエージェント応答を評価する:\n"
            "  - 文字列スコアラー: Levenshtein類似度（ローカル、APIキー不要）\n"
            "  - LLMスコアラー: Factuality, ClosedQA（OPENROUTER_API_KEYが必要）\n"
            "  - カスタム分類器: 出典裏付けチェック\n\n"
            f"モデル: {DEFAULT_MODEL}（OpenRouter経由）\n"
            "Install: pip install autoevals openai",
            title="05 - Braintrust AutoEvals (OpenRouter)",
        )
    )

    # 何が使えるかをチェック
    has_autoevals = False
    try:
        import autoevals  # noqa: F401

        has_autoevals = True
    except ImportError:
        pass

    has_openrouter_key = bool(os.environ.get("OPENROUTER_API_KEY"))

    if has_autoevals and has_openrouter_key:
        init_openrouter_client()
        console.print(
            "[green]autoevals installed + OpenRouter key — running all scorers live[/green]"
        )
    elif has_autoevals:
        console.print(
            "[yellow]autoevals installed, no OpenRouter key — "
            "string scorers live, LLM scorers simulated[/yellow]"
        )
    else:
        console.print("[yellow]autoevals not installed — using simulated scores for demo[/yellow]")
    console.print()

    # 結果テーブル
    table = Table(title="AutoEvals スコアラー結果", show_lines=True)
    table.add_column("Task", style="cyan", width=12)
    table.add_column("Levenshtein", width=12, justify="center")
    table.add_column("Factuality", width=12, justify="center")
    table.add_column("ClosedQA", width=12, justify="center")
    table.add_column("Grounding", width=12, justify="center")
    table.add_column("Avg Score", width=10, justify="center")

    all_scores: list[dict[str, float]] = []

    for task in EVAL_TASKS:
        response = get_agent_response(task["id"])
        output = response["answer"]
        expected = task["reference_answer"]

        # 全カテゴリのスコアラーを実行
        scores: dict[str, float] = {}
        scores.update(run_string_scorers(output, expected, task["id"]))
        scores.update(run_llm_scorers(task["question"], output, expected, task["id"]))
        scores.update(run_custom_classifier(output, task["id"]))

        all_scores.append(scores)

        # テーブル行のフォーマット
        def fmt(s: float) -> str:
            color = "green" if s >= 0.7 else ("yellow" if s >= 0.4 else "red")
            return f"[{color}]{s:.2f}[/{color}]"

        avg = sum(scores.values()) / len(scores) if scores else 0.0
        avg_color = "green" if avg >= 0.7 else ("yellow" if avg >= 0.4 else "red")

        table.add_row(
            task["id"],
            fmt(scores.get("levenshtein", 0.0)),
            fmt(scores.get("factuality", 0.0)),
            fmt(scores.get("closedqa", 0.0)),
            fmt(scores.get("grounding", 0.0)),
            f"[{avg_color}]{avg:.2f}[/{avg_color}]",
        )

    console.print(table)

    # 集計
    if all_scores:
        console.print("\n[bold]集計スコア[/bold]")
        for scorer_name in ["levenshtein", "factuality", "closedqa", "grounding"]:
            values = [s.get(scorer_name, 0.0) for s in all_scores]
            avg = sum(values) / len(values)
            console.print(f"  {scorer_name:12s}: {avg:.2f}")

    # コード例を表示
    console.print("\n[bold]使用例（autoevals単体、OpenRouter経由）:[/bold]\n")
    code = (
        "from autoevals import Factuality, Levenshtein, init\n"
        "from openai import OpenAI\n\n"
        "# OpenRouter経由のOpenAI互換クライアントをグローバルに設定\n"
        "init(\n"
        '    client=OpenAI(api_key=os.environ["OPENROUTER_API_KEY"],\n'
        '                   base_url="https://openrouter.ai/api/v1"),\n'
        f'    default_model="{DEFAULT_MODEL}",\n'
        ")\n\n"
        "# 文字列スコアラー — 完全にローカル、APIキー不要\n"
        "lev = Levenshtein()\n"
        'result = lev.eval(output="hello wrld", expected="hello world")\n'
        "print(result.score)  # ~0.91\n\n"
        "# LLMスコアラー — OpenRouter経由でinit()したクライアントを使う\n"
        "fact = Factuality()\n"
        "result = fact.eval(\n"
        '    input="フランスの首都はどこですか？",\n'
        '    output="パリがフランスの首都です。",\n'
        '    expected="フランスの首都はパリです。",\n'
        ")\n"
        "print(result.score)  # 1.0\n"
    )
    from rich.syntax import Syntax

    console.print(Syntax(code, "python", theme="monokai", line_numbers=True))

    # Braintrust Eval()パターンを示す
    console.print("\n[bold]完全な評価パイプライン（Braintrustプラットフォーム利用）:[/bold]\n")
    eval_code = (
        "from braintrust import Eval\n"
        "from autoevals import Factuality, Levenshtein\n\n"
        "Eval(\n"
        '    "Research Assistant Suite",\n'
        "    data=lambda: [\n"
        '        {"input": "マイクロサービスとは？", "expected": "..."},\n'
        "    ],\n"
        "    task=lambda input: my_agent.answer(input),\n"
        "    scores=[Factuality, Levenshtein],\n"
        ")\n"
    )
    console.print(Syntax(eval_code, "python", theme="monokai", line_numbers=True))


if __name__ == "__main__":
    main()
