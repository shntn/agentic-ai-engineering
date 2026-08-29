"""
OpenRouter API とのやり取りをテストするためのモックレスポンスファクトリ。

実際のSDK型に依存せずに、OpenRouter APIレスポンスとtool_callを模した
モックを作成するヘルパーを提供する。
"""

import json
from typing import Any
from unittest.mock import MagicMock


def create_mock_response(
    content: str | None = None,
    tool_calls: list[Any] | None = None,
    finish_reason: str = "stop",
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
) -> MagicMock:
    """モックのOpenRouter APIレスポンス（ChatResult相当）を作成する。"""
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = finish_reason

    response = MagicMock()
    response.choices = [choice]
    # usageオブジェクトをOpenRouterの構造（prompt_tokens/completion_tokens）に合わせてモックする
    response.usage = MagicMock()
    response.usage.prompt_tokens = prompt_tokens
    response.usage.completion_tokens = completion_tokens
    return response


def make_tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> MagicMock:
    """モックのtool_callオブジェクトを作成する。"""
    tool_call = MagicMock()
    tool_call.id = call_id
    tool_call.type = "function"
    tool_call.function = MagicMock()
    tool_call.function.name = name
    # OpenAI/OpenRouter形式に合わせ、argumentsはJSON文字列として渡す
    tool_call.function.arguments = json.dumps(arguments)
    return tool_call
