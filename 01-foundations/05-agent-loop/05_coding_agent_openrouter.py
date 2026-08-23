"""
エージェントループ (OpenRouter)

以下を行う最小構成の自律エージェントを実演します:
- ユーザーからタスクを受け取る
- 使用するツールを判断する
- 完了するまでツールをループで実行する
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv
from openrouter import OpenRouter
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from common.logging_config import setup_logging
from common.token_tracking import OpenRouterTokenTracker

# ルートの .env ファイルから環境変数をロードする
load_dotenv(find_dotenv())

# ロギングの構成
logger = setup_logging(__name__)

SYSTEM_PROMPT = """あなたはコーディングエージェントです。提供されたツールを使ってタスクを完了してください。

ガイドライン:
- ファイルを変更する前に読み込むこと
- 段階的に変更を行い、各ステップを検証すること
- コマンドが失敗した場合は、エラーを分析して別のアプローチを試すこと
- 完了したら、達成した内容の簡潔な要約を提供すること"""


# ツール定義
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "指定されたパスのファイルの内容を読み込みます。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "読み込むファイルパス",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "指定されたパスのファイルにコンテンツを書き込みます。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "書き込み先のファイルパス",
                    },
                    "content": {
                        "type": "string",
                        "description": "書き込む内容",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "bash コマンドを実行し、その出力を返します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "実行する bash コマンド",
                    }
                },
                "required": ["command"],
            },
        },
    },
]


def execute_tool(name: str, tool_input: dict[str, Any]) -> str:
    """ツールを実行し、結果を文字列として返す"""
    if name == "read_file":
        try:
            return Path(tool_input["path"]).read_text()
        except Exception as e:
            return f"Error: {e}"

    elif name == "write_file":
        try:
            Path(tool_input["path"]).write_text(tool_input["content"])
            return f"Successfully wrote to {tool_input['path']}"
        except Exception as e:
            return f"Error: {e}"

    elif name == "bash":
        try:
            result = subprocess.run(
                tool_input["command"],
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            output = result.stdout + result.stderr
            return output if output else "(no output)"
        except subprocess.TimeoutExpired:
            return "Error: Command timed out"
        except Exception as e:
            return f"Error: {e}"

    return f"Unknown tool: {name}"


class CodingAgent:
    """
    最小構成の自律コーディングエージェント。

    完了するまでツールをループで実行する。
    """

    def __init__(self, model: str = "deepseek/deepseek-v4-flash"):
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.model = model
        self.max_iterations = 10
        self.token_tracker = OpenRouterTokenTracker()

    def run(self, task: str) -> str:
        """与えられたタスクに対してエージェントループを実行する"""
        logger.info(f"Task: {task}")

        messages: list[dict[str, Any]] = [{"role": "user", "content": task}]

        for iteration in range(self.max_iterations):
            logger.info(f"--- Iteration {iteration + 1} ---")

            # モデルを呼び出す
            response = self.client.chat.send(  # type: ignore[call-overload]
                model=self.model,
                temperature=0.1,
                max_tokens=4096,
                reasoning={"effort": "none"},
                tools=TOOLS,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, *messages],
            )

            self.token_tracker.track(response.usage)

            message = response.choices[0].message
            finish_reason = response.choices[0].finish_reason
            tool_calls = message.tool_calls or []

            if message.content:
                logger.info(f"🤖 Agent: {message.content}")

            # レスポンスの内容を処理
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

            # ツール使用がなければタスク完了
            if finish_reason != "tool_calls" or not tool_calls:
                return message.content or "Done"

            # ツールを実行し、結果を収集
            for tool_call in tool_calls:
                tool_input = json.loads(tool_call.function.arguments)
                logger.info(f"🔧 Tool: {tool_call.function.name}({json.dumps(tool_input)})")
                result = execute_tool(tool_call.function.name, tool_input)
                logger.info(f"📋 Result: {result[:100]}{'...' if len(result) > 100 else ''}")
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    }
                )

        return "Max iterations reached"


def main() -> None:
    """メインのオーケストレーション関数"""
    console = Console()
    console.print(
        Panel(
            "例:\n"
            "  - 既存ファイルのスタイルに従って電卓を作成する\n"
            "  - 現在の依存関係を一覧表示する\n"
            "  - カレントフォルダのコードを説明する\n\n"
            "終了するには 'quit' と入力してください。",
            title="コーディングエージェント (OpenRouter)",
        )
    )

    agent = CodingAgent()

    try:
        while True:
            console.print("\n[bold green]You:[/bold green] ", end="")
            user_input = input().strip()

            if user_input.lower() in ("exit", "quit", "q", ""):
                console.print("\n[yellow]セッションを終了します...[/yellow]")
                break

            response = agent.run(user_input)
            console.print("\n[bold blue]Agent:[/bold blue]")
            console.print(Markdown(response))

    except KeyboardInterrupt:
        console.print("\n[yellow]中断されました。[/yellow]")

    console.print()
    agent.token_tracker.report()


if __name__ == "__main__":
    main()
