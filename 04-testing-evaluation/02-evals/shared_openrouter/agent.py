"""
evalチュートリアル向けのリサーチアシスタントエージェント（OpenRouter）。

ナレッジベースを検索し、出典引用付きで回答を合成するツール使用エージェントを
実装する。evalパイプラインにおけるテスト対象として使われる。
"""

import json
from typing import Any

from common import OpenRouterTokenTracker, setup_logging
from openrouter import OpenRouter
from openrouter.components import ChatResult

from shared_openrouter.knowledge_base import (
    KNOWLEDGE_BASE,
    SYSTEM_PROMPT,
    TOOLS,
    search_knowledge_base,
)

logger = setup_logging(__name__)


class ResearchAssistant:
    """ナレッジベースを検索し、回答を合成するリサーチアシスタント。"""

    def __init__(
        self,
        client: OpenRouter,
        knowledge_base: list[dict[str, Any]] | None = None,
        model: str = "deepseek/deepseek-v4-flash-0731",
    ) -> None:
        self.client = client
        self.knowledge_base = knowledge_base if knowledge_base is not None else KNOWLEDGE_BASE
        self.model = model
        self.token_tracker = OpenRouterTokenTracker()

    def answer(self, question: str) -> dict[str, Any]:
        """ナレッジベースを使って質問に回答する。"""
        messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
        tool_calls_made: list[dict[str, Any]] = []

        while True:
            response: ChatResult = self.client.chat.send(  # type: ignore[call-overload]
                model=self.model,
                max_tokens=1024,
                reasoning={"effort": "none"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    *messages,
                ],
                tools=TOOLS,
            )
            assert response.usage is not None
            self.token_tracker.track(response.usage)

            message = response.choices[0].message
            finish_reason = response.choices[0].finish_reason
            tool_calls = message.tool_calls or []

            if finish_reason != "tool_calls" or not tool_calls:
                return {
                    "answer": message.content or "",
                    "tool_calls": tool_calls_made,
                    "sources": [tc["results"] for tc in tool_calls_made],
                }

            # ツール呼び出しを処理する
            assistant_message: dict[str, Any] = {"role": "assistant", "content": message.content}
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

            for tool_call in tool_calls:
                tool_input = json.loads(tool_call.function.arguments)
                result = search_knowledge_base(**tool_input, corpus=self.knowledge_base)
                tool_calls_made.append(
                    {"name": tool_call.function.name, "input": tool_input, "results": result}
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
