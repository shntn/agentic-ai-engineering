"""
共有トレース対象リサーチアシスタントエージェント（OpenRouter）。

ライブエージェントを必要とするチュートリアル（01、03）で使われる、完全な
実行トレースを備えたエージェントループをカプセル化する。
"""

import json
from typing import Any

from common import OpenRouterTokenTracker, setup_logging
from openrouter import OpenRouter
from openrouter.components import ChatResult

from shared_openrouter.knowledge_base import SYSTEM_PROMPT, TOOLS, execute_tool
from shared_openrouter.tracer import TraceCollector

logger = setup_logging(__name__)

MODEL = "deepseek/deepseek-v4-flash-0731"


class TracedResearchAssistant:
    """完全な実行トレースを備えたリサーチアシスタント。"""

    def __init__(
        self,
        client: OpenRouter,
        tracer: TraceCollector,
    ) -> None:
        self.client = client
        self.tracer = tracer
        self.token_tracker = OpenRouterTokenTracker()

    def answer(self, question: str) -> dict[str, Any]:
        """完全なトレースを取りながら質問に回答する。"""
        with self.tracer.span("answer_question", "agent_step", {"question": question}) as root:
            messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
            llm_call_count = 0

            while True:
                llm_call_count += 1
                with self.tracer.span(
                    f"llm_call_{llm_call_count}",
                    "llm_call",
                    {"message_count": len(messages)},
                ) as llm_span:
                    response: ChatResult = self.client.chat.send(  # type: ignore[call-overload]
                        model=MODEL,
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
                    llm_span.tokens = {
                        "input": response.usage.prompt_tokens,
                        "output": response.usage.completion_tokens,
                    }
                    finish_reason = response.choices[0].finish_reason
                    llm_span.outputs = {"finish_reason": finish_reason}

                # レスポンスを処理する
                message = response.choices[0].message
                tool_calls = message.tool_calls or []

                assistant_message: dict[str, Any] = {
                    "role": "assistant",
                    "content": message.content,
                }
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

                if finish_reason != "tool_calls" or not tool_calls:
                    answer_text = message.content or ""
                    root.outputs = {"answer": answer_text[:200], "llm_calls": llm_call_count}
                    return {
                        "answer": answer_text,
                        "llm_calls": llm_call_count,
                        "trace": self.tracer.to_dict(),
                    }

                # ツールをトレースしながら実行する
                for tool_call in tool_calls:
                    tool_input = json.loads(tool_call.function.arguments)
                    with self.tracer.span(
                        f"tool_{tool_call.function.name}",
                        "tool_call",
                        {"tool": tool_call.function.name, "input": tool_input},
                    ) as tool_span:
                        result = execute_tool(tool_call.function.name, tool_input)
                        tool_span.outputs = {"result": str(result)[:200]}

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": json.dumps(result, default=str, ensure_ascii=False),
                        }
                    )

                if llm_call_count >= 10:
                    root.error = "Max iterations reached"
                    return {"answer": "Max iterations reached", "llm_calls": llm_call_count}
