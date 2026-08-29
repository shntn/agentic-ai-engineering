"""
共有ツール定義・実装・ディスパッチ。

すべてのユニットテストチュートリアルスクリプトで使われる電卓・read_file・
run_bashツールを提供する。安全機構（ブロック対象コマンド）と汎用的な
execute_toolディスパッチャーを含む。
"""

import subprocess
from pathlib import Path
from typing import Any

from common import setup_logging

logger = setup_logging(__name__)

# ---------------------------------------------------------------------------
# ツール定義（OpenAI function calling形式）
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": "基本的な算術演算を実行します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["add", "subtract", "multiply", "divide"],
                    },
                    "a": {"type": "number"},
                    "b": {"type": "number"},
                },
                "required": ["operation", "a", "b"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "指定されたパスのファイルの内容を読み込みます。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "max_lines": {"type": "integer", "default": 100},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_bash",
            "description": "bashコマンドを実行し、出力を返します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "default": 30},
                },
                "required": ["command"],
            },
        },
    },
]

BLOCKED_COMMANDS = ["rm", "sudo", "chmod", "chown", "mkfs", "dd", "shutdown", "reboot", ">", ">>"]


# ---------------------------------------------------------------------------
# ツールの実装
# ---------------------------------------------------------------------------


def calculator(operation: str, a: float, b: float) -> dict[str, Any]:
    """電卓ツールを実行する。"""
    operations = {
        "add": lambda x, y: x + y,
        "subtract": lambda x, y: x - y,
        "multiply": lambda x, y: x * y,
        "divide": lambda x, y: x / y if y != 0 else "Error: Division by zero",
    }
    if operation not in operations:
        return {"error": f"Unknown operation: {operation}"}
    result = operations[operation](a, b)
    logger.info("Calculator: %s %s %s = %s", a, operation, b, result)
    return {"result": result, "operation": operation, "operands": [a, b]}


def read_file(path: str, max_lines: int = 100) -> dict[str, Any]:
    """ファイルの内容を読み込む。"""
    try:
        with Path(path).open(encoding="utf-8") as f:
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


def run_bash(command: str, timeout: int = 30) -> dict[str, Any]:
    """bashコマンドを実行し、出力を返す。"""
    cmd_lower = command.lower().strip()
    for blocked in BLOCKED_COMMANDS:
        if blocked in cmd_lower:
            logger.warning("Blocked dangerous command: %s", command)
            return {"error": f"Command blocked for safety: contains '{blocked}'"}
    logger.info("Running bash command: %s", command)
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return {
            "command": command,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": f"Command timed out after {timeout} seconds"}


# ---------------------------------------------------------------------------
# ツールディスパッチ
# ---------------------------------------------------------------------------

TOOL_FUNCTIONS: dict[str, Any] = {
    "calculator": calculator,
    "read_file": read_file,
    "run_bash": run_bash,
}


def execute_tool(tool_name: str, tool_input: dict[str, Any]) -> Any:
    """ツールを実行し、その結果を返す。"""
    if tool_name not in TOOL_FUNCTIONS:
        return {"error": f"Unknown tool: {tool_name}"}
    try:
        return TOOL_FUNCTIONS[tool_name](**tool_input)
    except TypeError as e:
        logger.error("Invalid arguments for tool %s: %s", tool_name, e)
        return {"error": f"Invalid arguments: {e}"}
    except Exception as e:
        logger.error("Tool execution error: %s", e)
        return {"error": str(e)}
