"""
ツール使用 (OpenRouter)

モデルが関数を呼び出し、ツールを使用できるようにする方法を示します。
実用的なツールを使用します: 電卓、ファイル読み込み、bash コマンド実行。
"""

import json
import os
import subprocess
from typing import Any

from dotenv import find_dotenv, load_dotenv
from openrouter import OpenRouter
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from common import OpenRouterTokenTracker, setup_logging

# ルートの .env ファイルから環境変数をロードする
load_dotenv(find_dotenv())

# ロギングの構成
logger = setup_logging(__name__)


# 利用可能なツールを定義
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "基本的な算術演算を実行します。加算、減算、乗算、除算に対応しています。",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["add", "subtract", "multiply", "divide"],
                        "description": "実行する算術演算",
                    },
                    "a": {"type": "number", "description": "1つ目の数値"},
                    "b": {"type": "number", "description": "2つ目の数値"},
                },
                "required": ["operation", "a", "b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "指定されたパスのファイルの内容を読み込みます。ファイルの内容をテキストとして返します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "読み込むファイルへのパス",
                    },
                    "max_lines": {
                        "type": "integer",
                        "description": "読み込む最大行数（デフォルト: 100）",
                        "default": 100,
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_bash",
            "description": "bash コマンドを実行し、出力を返します。ls、pwd、echo、date などのシステムコマンドに使用してください。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "実行する bash コマンド",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "タイムアウト秒数（デフォルト: 30）",
                        "default": 30,
                    },
                },
                "required": ["command"],
            },
        },
    },
]


def calculator(operation: str, a: float, b: float) -> dict[str, Any]:
    """電卓ツールを実行"""
    operations = {
        "add": lambda x, y: x + y,
        "subtract": lambda x, y: x - y,
        "multiply": lambda x, y: x * y,
        "divide": lambda x, y: x / y if y != 0 else "Error: Division by zero",
    }

    result = operations[operation](a, b)
    logger.info("Calculator: %s %s %s = %s", a, operation, b, result)

    return {"result": result, "operation": operation, "operands": [a, b]}


def read_file(path: str, max_lines: int = 100) -> dict[str, Any]:
    """ファイルの内容を読み込む"""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()

        total_lines = len(lines)
        content = "".join(lines[:max_lines])
        truncated = total_lines > max_lines

        logger.info("Read file: %s (%d lines)", path, total_lines)

        return {
            "path": path,
            "content": content,
            "total_lines": total_lines,
            "truncated": truncated,
        }
    except FileNotFoundError:
        return {"error": f"File not found: {path}"}
    except PermissionError:
        return {"error": f"Permission denied: {path}"}
    except Exception as e:
        return {"error": str(e)}


BLOCKED_COMMANDS = ["rm", "sudo", "chmod", "chown", "mkfs", "dd", "shutdown", "reboot", ">", ">>"]


def run_bash(command: str, timeout: int = 30) -> dict[str, Any]:
    """bash コマンドを実行し、出力を返す"""
    # シンプルなガードレール: 危険なコマンドをブロック
    cmd_lower = command.lower().strip()
    for blocked in BLOCKED_COMMANDS:
        if blocked in cmd_lower:
            logger.warning("Blocked dangerous command: %s", command)
            return {"error": f"Command blocked for safety: contains '{blocked}'"}

    logger.info("Running bash command: %s", command)

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

        return {
            "command": command,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after {timeout} seconds"}
    except Exception as e:
        return {"error": str(e)}


# ツール実行のマッピング
TOOL_FUNCTIONS = {
    "calculator": calculator,
    "read_file": read_file,
    "run_bash": run_bash,
}


def execute_tool(tool_name: str, tool_input: dict[str, Any]) -> Any:
    """ツールを実行し、その結果を返す"""
    if tool_name not in TOOL_FUNCTIONS:
        return {"error": f"Unknown tool: {tool_name}"}

    try:
        func = TOOL_FUNCTIONS[tool_name]
        return func(**tool_input)  # type: ignore[operator]
    except Exception as e:
        logger.error("Tool execution error: %s", e)
        return {"error": str(e)}


class ToolUseChat:
    """ツール使用機能を備えたチャットセッション"""

    def __init__(
        self,
        model: str,
        token_tracker: OpenRouterTokenTracker,
        console: Console,
    ):
        """ツールを備えたチャットセッションを初期化"""
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.token_tracker = token_tracker
        self.console = console
        self.messages: list[dict[str, Any]] = []
        self.model = model

    def send_message(self, user_message: str) -> str:
        """メッセージを送信し、必要に応じてツール使用を処理"""
        # ユーザーメッセージを追加
        self.messages.append({"role": "user", "content": user_message})

        # 最終的なテキスト応答を得るまで処理を続ける
        while True:
            logger.info("API call (messages: %d)", len(self.messages))

            # ツールを指定して API 呼び出しを行う
            response = self.client.chat.send(
                model=self.model,
                max_tokens=4096,
                reasoning={"effort": "none", "summary": "null"},
                tools=TOOLS,
                messages=self.messages,
            )

            # トークンを追跡
            self.token_tracker.track(response.usage)

            message = response.choices[0].message
            finish_reason = response.choices[0].finish_reason

            # 終了理由を確認
            logger.info("Finish reason: %s", finish_reason)

            tool_calls = message.tool_calls or []
            text_content = message.content or ""

            # アシスタントの応答をメッセージに追加
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
            self.messages.append(assistant_message)

            # ツール使用がなければ終了
            if finish_reason != "tool_calls" or not tool_calls:
                return text_content

            # ツールを実行し、結果を収集
            self.console.print("\n[yellow]→ ツールを実行中...[/yellow]")

            for tool_call in tool_calls:
                tool_input = json.loads(tool_call.function.arguments)
                self.console.print(
                    f"  [dim]• {tool_call.function.name}({json.dumps(tool_input, indent=2, ensure_ascii=False)})[/dim]"
                )

                # ツールを実行
                result = execute_tool(tool_call.function.name, tool_input)

                # ツール結果を追加
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

    def get_message_count(self) -> int:
        """会話内のメッセージの総数を取得"""
        return len(self.messages)


def main() -> None:
    """ユーザーとのやり取りを処理し、チャットフローを調整するメインのオーケストレーション関数"""
    console = Console()
    token_tracker = OpenRouterTokenTracker()
    chat = ToolUseChat("deepseek/deepseek-v4-flash", token_tracker, console)

    # ウェルカムメッセージ
    console.print(
        Panel(
            "[bold cyan]ツールを使うエージェント！[/bold cyan]\n\n"
            "利用可能なツール:\n"
            "• 電卓（加算、減算、乗算、除算）\n"
            "• ファイル読み込み（任意のファイルの内容を読み込む）\n"
            "• bash 実行（シェルコマンドを実行する）\n\n"
            "試してみてください: '123 * 456 は？' や 'カレントディレクトリのファイル一覧を教えて'\n"
            "または: 'pyproject.toml ファイルを読んで'\n\n"
            "終了するには 'quit' と入力してください。",
            title="ツール使用デモ",
        )
    )

    # チャットループ
    try:
        while True:
            console.print("\n[bold green]You:[/bold green] ", end="")
            user_input = input().strip()

            if user_input.lower() in ["quit", "exit", ""]:
                console.print("\n[yellow]チャットセッションを終了します...[/yellow]")
                break

            try:
                response = chat.send_message(user_input)

                if response:
                    console.print("\n[bold blue]Agent:[/bold blue]")
                    console.print(Markdown(response))

            except Exception as e:
                logger.error("Error during chat: %s", e)
                console.print(f"\n[red]Error: {e}[/red]")
                break

    except KeyboardInterrupt:
        console.print("\n[yellow]中断されました。チャットセッションを終了します...[/yellow]")

    # 使用状況を報告
    console.print()
    token_tracker.report()
    console.print(f"\n[dim]Total messages exchanged: {chat.get_message_count()}[/dim]")


if __name__ == "__main__":
    main()
