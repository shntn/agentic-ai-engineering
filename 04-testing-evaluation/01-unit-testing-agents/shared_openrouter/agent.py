"""
テスト容易性のための依存性注入を備えたツール使用エージェント（OpenRouter）。

コアとなるエージェントループをカプセル化する: LLMにメッセージを送信し、
tool_callsを解析し、ツールを実行し、結果を送り返し、テキスト応答または
最大反復回数に達するまで繰り返す。
"""

import json
from typing import Any

from common import OpenRouterTokenTracker, setup_logging

from shared_openrouter.tools import TOOLS, execute_tool

logger = setup_logging(__name__)


class ToolUseAgent:
    """依存性注入と反復回数の上限を備えたツール使用エージェント。"""

    def __init__(
        self,
        client: Any,
        model: str = "deepseek/deepseek-v4-flash-0731",
        max_iterations: int = 10,
        tools: list[dict[str, Any]] | None = None,
    ) -> None:
        self.client = client
        self.model = model
        self.max_iterations = max_iterations
        self.tools = tools if tools is not None else TOOLS
        self.messages: list[dict[str, Any]] = []
        self.token_tracker = OpenRouterTokenTracker()

    def send_message(self, user_message: str) -> str:
        """メッセージを送信し、テキスト応答が返るまでエージェントループを処理する。"""
        self.messages.append({"role": "user", "content": user_message})
        iterations = 0

        while iterations < self.max_iterations:
            iterations += 1
            logger.info(
                "Iteration %d/%d (messages: %d)",
                iterations,
                self.max_iterations,
                len(self.messages),
            )

            # 重要な概念: 注入されたクライアントがここで呼び出される——モックしやすい
            response = self.client.chat.send(
                model=self.model,
                max_tokens=4096,
                tools=self.tools,
                messages=self.messages,
            )

            self.token_tracker.track(response.usage)

            message = response.choices[0].message
            finish_reason = response.choices[0].finish_reason
            tool_calls = message.tool_calls or []

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

            if finish_reason != "tool_calls" or not tool_calls:
                return message.content or ""

            # 各ツールを実行し、結果をそれぞれ個別のtoolメッセージとして送り返す
            for tool_call in tool_calls:
                tool_input = json.loads(tool_call.function.arguments)
                result = execute_tool(tool_call.function.name, tool_input)
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

        # 契約: max_iterationsに達した——エージェントは停止しなければならない
        logger.warning("Max iterations (%d) reached, stopping agent", self.max_iterations)
        return "[Agent stopped: maximum iterations reached]"
