"""
Promptfoo — YAMLで駆動する評価フレームワーク (OpenRouter)

Promptfooを Pythonベースのエージェントと連携させる方法を示す。Promptfooは
Node.js製のCLIツールで、カスタムのPythonプロバイダーとアサーションに対応しており、
評価スイートをYAMLで宣言的に定義できる。

このスクリプトの流れ:
1. ゴールデンデータセットからテストケースを生成し、promptfooconfig.openrouter.yamlを作成
2. リサーチアシスタントをラップするカスタムPythonプロバイダーを作成
3. キーワード採点用のカスタムPythonアサーションを作成
4. `npx promptfoo eval`で評価を実行する方法を示す

注意: Promptfooの実行にはNode.jsが必要。`npm install -g promptfoo`または
`npx promptfoo@latest eval`でインストールする。`pip install promptfoo`パッケージは
Nodeバイナリをラップしたもの。
"""

import json
from pathlib import Path

from common import setup_logging
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax

from shared_openrouter.knowledge_base import EVAL_TASKS, get_agent_response

logger = setup_logging(__name__)

# OpenRouter経由でデフォルトモデルとして使うモデルID
DEFAULT_MODEL = "deepseek/deepseek-v4-flash-0731"


# ---------------------------------------------------------------------------
# Promptfoo YAML設定ジェネレーター
# ---------------------------------------------------------------------------


def generate_promptfoo_config(tasks: list[dict], output_dir: Path) -> str:
    """リサーチアシスタント向けのpromptfooconfig.openrouter.yamlを生成する。"""
    tests = []
    for task in tasks:
        test_case: dict = {
            "vars": {"question": task["question"]},
            "assert": [],
        }

        # コードベースのアサーション: カスタムPython関数によるキーワードチェック
        if task["expected_keywords"]:
            test_case["assert"].append(
                {
                    "type": "python",
                    "value": "file://assertion_keywords_openrouter.py",
                    "metadata": {"keywords": task["expected_keywords"]},
                }
            )
        else:
            # スコープ外: 拒否応答が含まれているべき
            test_case["assert"].append(
                {
                    "type": "icontains",
                    "value": "見つかりませんでした",
                }
            )

        # 出典引用チェック
        for source_id in task.get("expected_source_ids", []):
            test_case["assert"].append(
                {
                    "type": "contains",
                    "value": source_id,
                }
            )

        # LLM-as-judgeルーブリック（Promptfoo組み込みのllm-rubricアサーションを使用）
        if task["expected_keywords"]:
            test_case["assert"].append(
                {
                    "type": "llm-rubric",
                    "value": (
                        f"以下の質問に正確に回答できているか: '{task['question']}'。"
                        f"出典を引用し、次のトピックをカバーしている必要がある: "
                        f"{', '.join(task['expected_keywords'])}。"
                    ),
                }
            )

        tests.append(test_case)

    config = {
        "description": "リサーチアシスタント評価スイート (OpenRouter)",
        "providers": [
            {
                "id": "file://provider_agent_openrouter.py",
                "label": "Research Assistant (OpenRouter)",
                "config": {"mode": "simulated"},
            }
        ],
        "prompts": ["{{question}}"],
        "tests": tests,
        "defaultTest": {
            "options": {
                "provider": f"openrouter:{DEFAULT_MODEL}",
            }
        },
    }

    # YAML設定を書き出す
    import yaml  # type: ignore[import-untyped]

    config_path = output_dir / "promptfooconfig.openrouter.yaml"
    with config_path.open("w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    return config_path.name


def generate_provider_script(output_dir: Path) -> None:
    """Promptfoo向けのカスタムPythonプロバイダースクリプトを生成する。"""
    # Promptfooはテストケースごとにこのスクリプトのcall_api関数を呼び出す
    provider_code = '''"""
リサーチアシスタントをラップするカスタムPromptfooプロバイダー (OpenRouter)。

Promptfooはテストケースごとにcall_api()を呼び出す。関数は以下を受け取る:
- prompt: レンダリング済みのプロンプト文字列
- options: YAMLの'config'を含むdict
- context: テストケースの'vars'を含むdict
"""


def call_api(prompt, options, context):
    """Promptfooプロバイダーのエントリーポイント。"""
    from shared_openrouter.knowledge_base import get_agent_response, EVAL_TASKS

    question = context.get("vars", {}).get("question", prompt)

    # 質問文でタスクを照合
    task_id = None
    for task in EVAL_TASKS:
        if task["question"] == question:
            task_id = task["id"]
            break

    if task_id is None:
        return {"output": "一致するタスクが見つかりませんでした。"}

    response = get_agent_response(task_id)

    return {
        "output": response["answer"],
        "tokenUsage": {"total": 100, "prompt": 50, "completion": 50},
    }
'''
    (output_dir / "provider_agent_openrouter.py").write_text(provider_code, encoding="utf-8")


def generate_assertion_script(output_dir: Path) -> None:
    """キーワード採点用のカスタムPromptfooアサーションスクリプトを生成する。"""
    assertion_code = '''"""
キーワードカバレッジ採点用のカスタムPromptfooアサーション (OpenRouter)。

Promptfooはtype='python'の各アサーションに対してget_assert()を呼び出す。
pass・score・reasonを含むdictを返す。
"""


def get_assert(output, context):
    """エージェント出力のキーワードカバレッジをチェックする。"""
    metadata = context.get("test", {}).get("metadata", {})
    keywords = metadata.get("keywords", [])

    if not keywords:
        return {"pass": True, "score": 1.0, "reason": "チェック対象のキーワードなし"}

    output_lower = output.lower()
    found = [kw for kw in keywords if kw.lower() in output_lower]
    missing = [kw for kw in keywords if kw.lower() not in output_lower]

    score = len(found) / len(keywords)
    passed = score >= 0.5

    reason = f"{len(found)}/{len(keywords)} 件のキーワードが一致"
    if missing:
        reason += f"（不足: {', '.join(missing)}）"

    return {"pass": passed, "score": score, "reason": reason}
'''
    (output_dir / "assertion_keywords_openrouter.py").write_text(assertion_code, encoding="utf-8")


# ---------------------------------------------------------------------------
# メイン — 設定を生成し、YAML駆動のパターンを示す
# ---------------------------------------------------------------------------


def main() -> None:
    """Promptfoo設定を生成し、YAML駆動の評価パターンを示す。"""
    console = Console()
    console.print(
        Panel(
            "[bold cyan]Promptfoo — YAMLで駆動する評価フレームワーク[/bold cyan]\n\n"
            "以下を含むPromptfoo評価スイートを生成する:\n"
            "  - カスタムPythonプロバイダー（リサーチアシスタントをラップ）\n"
            "  - カスタムPythonアサーション（キーワード採点）\n"
            "  - 組み込みアサーション（contains, icontains, llm-rubric）\n\n"
            "PromptfooはPythonを第一級サポートするNode.js製CLI。\n"
            f"モデル: {DEFAULT_MODEL}（OpenRouter経由）\n"
            "Install: npm install -g promptfoo",
            title="04 - Promptfoo (OpenRouter)",
        )
    )

    output_dir = Path(__file__).parent
    try:
        import yaml  # noqa: F401  # type: ignore[import-untyped]
    except ImportError:
        console.print(
            "[yellow]PyYAMLがインストールされていません — 代わりにJSON設定を表示します。[/yellow]\n"
            "[dim]Install with: pip install pyyaml[/dim]\n"
        )

    # Promptfoo用ファイルを生成
    generate_provider_script(output_dir)
    generate_assertion_script(output_dir)
    console.print(
        "[green]provider_agent_openrouter.py と "
        "assertion_keywords_openrouter.py を生成しました[/green]\n"
    )

    # 設定がどのようになるかを表示
    console.print("[bold]Promptfoo設定 (promptfooconfig.openrouter.yaml)[/bold]\n")

    # 表示用のサンプル設定を組み立てる（表示にはyaml依存を避ける）
    sample_config = {
        "description": "リサーチアシスタント評価スイート (OpenRouter)",
        "providers": [
            {
                "id": "file://provider_agent_openrouter.py",
                "label": "Research Assistant (OpenRouter)",
                "config": {"mode": "simulated"},
            }
        ],
        "prompts": ["{{question}}"],
        "tests": [
            {
                "vars": {"question": EVAL_TASKS[0]["question"]},
                "assert": [
                    {
                        "type": "python",
                        "value": "file://assertion_keywords_openrouter.py",
                        "metadata": {"keywords": EVAL_TASKS[0]["expected_keywords"]},
                    },
                    {"type": "contains", "value": "doc_001"},
                    {
                        "type": "llm-rubric",
                        "value": "マイクロサービスの利点を正確にカバーしているべき。",
                    },
                ],
            },
            {"vars": {"question": "..."}, "assert": [{"type": "..."}]},
        ],
    }
    config_yaml = json.dumps(sample_config, indent=2, ensure_ascii=False)
    console.print(Syntax(config_yaml, "json", theme="monokai", line_numbers=True))

    # pyyamlが利用可能であればYAML設定を生成してみる
    try:
        config_name = generate_promptfoo_config(EVAL_TASKS, output_dir)
        console.print(f"\n[green]{config_name} を生成しました[/green]")
    except ImportError:
        console.print("\n[yellow]YAML生成をスキップします（pyyaml未インストール）[/yellow]")

    # 採点ロジックを示すため、アサーションをローカルで実行する
    console.print("\n[bold]アサーションをローカルで実行（シミュレーション）:[/bold]\n")

    for task in EVAL_TASKS:
        response = get_agent_response(task["id"])
        output = response["answer"]

        # キーワードチェック
        if task["expected_keywords"]:
            output_lower = output.lower()
            found = [kw for kw in task["expected_keywords"] if kw.lower() in output_lower]
            score = len(found) / len(task["expected_keywords"])
            status = "[green]PASS[/green]" if score >= 0.5 else "[red]FAIL[/red]"
            console.print(f"  {task['id']}: {status} keywords={score:.0%}", end="")
        else:
            has_refusal = "見つかりませんでした" in output or "含まれていません" in output
            status = "[green]PASS[/green]" if has_refusal else "[red]FAIL[/red]"
            console.print(f"  {task['id']}: {status} refusal={has_refusal}", end="")

        # 出典引用チェック
        for sid in task.get("expected_source_ids", []):
            cited = sid in output
            cite_status = "[green]yes[/green]" if cited else "[red]no[/red]"
            console.print(f"  {sid}={cite_status}", end="")

        console.print()

    # 実行方法を表示
    console.print(
        "\n[bold]Promptfoo CLIでの実行方法:[/bold]\n"
        "  [dim]npx promptfoo@latest eval -c promptfooconfig.openrouter.yaml[/dim]\n"
        "  [dim]npx promptfoo@latest view  # Web UIで結果を開く[/dim]"
    )


if __name__ == "__main__":
    main()
