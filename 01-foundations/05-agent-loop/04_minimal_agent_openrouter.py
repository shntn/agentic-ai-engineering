import json
import os
import subprocess

from dotenv import find_dotenv, load_dotenv
from openrouter import OpenRouter

load_dotenv(find_dotenv())

client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "bash コマンドを実行する",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    }
]


def agent(goal: str) -> str:
    messages = [{"role": "user", "content": goal}]

    for _ in range(10):
        response = client.chat.send(  # type: ignore[call-overload]
            model="deepseek/deepseek-v4-flash",
            max_tokens=4096,
            reasoning={"effort": "none", "summary": "null"},
            messages=messages,
            tools=TOOLS,
        )
        message = response.choices[0].message
        finish_reason = response.choices[0].finish_reason
        tool_calls = message.tool_calls or []

        assistant_message: dict = {"role": "assistant", "content": message.content}
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
            return message.content or ""

        for tool_call in tool_calls:
            tool_input = json.loads(tool_call.function.arguments)
            print(f"  -> Tool: {tool_call.function.name}({tool_input})")
            # 実行前のヒューマンインザループ承認
            if input("  承認しますか？ (y/n): ").strip().lower() != "y":
                return "ユーザーによりキャンセルされました。"
            result = subprocess.run(
                tool_input["command"], shell=True, capture_output=True, text=True, timeout=30
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result.stdout or result.stderr,
                }
            )

    return "最大イテレーション回数に達しました"


if __name__ == "__main__":
    print("ミニマムエージェント（終了するには 'quit' と入力）")
    print(
        "試してみてください: 'カレントディレクトリのファイルの内容を要約して' や '使用しているOSを教えて'"
    )
    while True:
        task = input("\nYou: ").strip()
        if task.lower() in ("exit", "quit", "q", ""):
            break
        print(f"\nAgent: {agent(task)}")
