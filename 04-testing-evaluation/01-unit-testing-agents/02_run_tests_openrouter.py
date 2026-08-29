"""
ユニットテストエージェント — テストランナー (OpenRouter)

ユニットテストチュートリアルの全テストスイートを実行する。4層のエージェント
テストを実演する: モックLLMレスポンス、ツールの分離、振る舞いの契約、
レスポンスカセットによる統合テスト。

テストはtests_openrouter/にあり、pytest経由で直接実行することもできる:
  pytest tests_openrouter/ -v
"""

from pathlib import Path

import pytest
from common import setup_logging
from dotenv import find_dotenv, load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

load_dotenv(find_dotenv())

logger = setup_logging(__name__)

# テストモジュールとその説明
TEST_SUITES = [
    (
        "tests_openrouter/test_mock_llm.py",
        "モックLLMテスト",
        "APIレスポンスをモックし、エージェントループのロジックをテストする",
    ),
    (
        "tests_openrouter/test_tools.py",
        "ツールのテスト",
        "エッジケースを含め、ツール関数を単体でテストする",
    ),
    (
        "tests_openrouter/test_behavioral_contracts.py",
        "振る舞いの契約",
        "エージェントの不変条件（安全性・終了・履歴）を検証する",
    ),
    (
        "tests_openrouter/test_integration.py",
        "統合テスト",
        "APIレスポンスの記録/再生、スナップショット回帰",
    ),
]


def main() -> None:
    """テストスイートの概要を表示し、人による確認の上でテストを実行する。"""
    console = Console()

    console.print(
        Panel(
            "[bold cyan]ユニットテストエージェント (OpenRouter)[/bold cyan]\n\n"
            "4つの相補的な戦略を使って、ツール使用エージェントループをテストします:\n"
            "  1. モックLLMレスポンス — 決定的なエージェントループのテスト\n"
            "  2. ツールの分離 — エッジケースを含む純粋な関数のテスト\n"
            "  3. 振る舞いの契約 — 安全性・終了・履歴の不変条件\n"
            "  4. 統合テスト — レスポンスカセットによる全体ループ\n\n"
            "APIキーは不要——すべてシミュレートされています。",
            title="01 — ユニットテストエージェント",
        )
    )

    # 利用可能なテストスイートを表示
    table = Table(title="テストスイート", show_lines=True)
    table.add_column("#", width=3, justify="center")
    table.add_column("スイート", style="cyan", width=24)
    table.add_column("説明", width=50)

    for i, (_, name, desc) in enumerate(TEST_SUITES, 1):
        table.add_row(str(i), name, desc)

    console.print(table)

    # 実行前に人による確認を行う
    console.print("\n[bold]上記のテストスイートをすべて実行します。[/bold]")
    try:
        answer = console.input("[dim]Enterキーで実行、'q'で終了: [/dim]")
    except (EOFError, KeyboardInterrupt):
        console.print("\n[yellow]キャンセルしました。[/yellow]")
        return

    if answer.strip().lower() in ("q", "quit", "exit"):
        console.print("[yellow]キャンセルしました。[/yellow]")
        return

    # テストを実行
    console.print("\n[bold]テストを実行中...[/bold]\n")

    output_dir = Path(__file__).parent / "output"
    output_dir.mkdir(exist_ok=True)
    report_path = output_dir / "test_results.xml"

    test_files = [str(Path(__file__).parent / path) for path, _, _ in TEST_SUITES]
    exit_code = pytest.main(
        [
            *test_files,
            "-v",
            "--tb=short",
            "--no-header",
            f"--junitxml={report_path}",
        ]
    )

    if exit_code == 0:
        console.print("\n[bold green]すべてのテストが成功しました！[/bold green]")
    else:
        console.print(
            f"\n[bold red]一部のテストが失敗しました（終了コード: {exit_code}）[/bold red]"
        )

    console.print(f"[dim]テストレポートを保存しました: {report_path}[/dim]")


if __name__ == "__main__":
    main()
