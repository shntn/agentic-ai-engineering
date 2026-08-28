"""メモリシステム — 3階層のメモリ永続化を持つパーソナルアシスタント (OpenRouter)

エージェント的なチャットループの中で、working memory（セッションバッファ）、
episodic memory（JSONに永続化されるイベント）、semantic memory（ChromaDBベクトル
ストア）を実演します。エージェントはツールを使い、セッションをまたいで情報を
記憶・想起・忘却します。
"""

import json
import os
import time
from typing import Any

from dotenv import find_dotenv, load_dotenv
from memory_openrouter import MemoryManager
from openrouter import OpenRouter
from openrouter.components import ChatResult
from openrouter.errors import OpenRouterError, TooManyRequestsResponseError
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from common import OpenRouterTokenTracker, setup_logging
from openrouter_adapter import to_openrouter_tools

load_dotenv(find_dotenv())

logger = setup_logging(__name__)

MODEL = "deepseek/deepseek-v4-flash"

SYSTEM_PROMPT = """\
あなたは永続的なメモリを持つパーソナルアシスタントです。3階層のメモリシステムを\
使って、セッションをまたいでユーザーに関する情報を記憶します:

1. **Working memory** — 一時的なセッションメモ（自動でクリアされる）
2. **Episodic memory** — タイムスタンプ付きのイベントとやり取り（JSONに永続化）
3. **Semantic memory** — 事実・好み・知識（ベクトルデータベースに永続化）

## メモリのガイドライン

- ユーザーが個人情報（名前・好み・事実）を共有したら、適切な重要度で**semantic**\
メモリに保存してください
- 注目すべき出来事やイベントが起きたら、**episodic**メモリに保存してください
- 何かを尋ねる前に、すでに知っているかどうかを確認するため積極的に**recall**を\
使ってください
- 重要度スコアの目安: 日常的な情報 = 0.3〜0.5、個人的な詳細 = 0.6〜0.8、\
重要な情報 = 0.9〜1.0
- 何を覚えているかについて透明性を保ってください——何かを思い出したらユーザーに\
伝えてください

{memory_context}"""

# エージェントがメモリを管理するために使う3つのツール
MEMORY_TOOLS = [
    {
        "name": "remember",
        "description": (
            "情報をメモリに保存する。事実・好み・知識には'semantic'を使う。"
            "出来事やイベントには'episodic'を使う。一時的なセッションメモには"
            "'working'を使う。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "記憶する情報",
                },
                "memory_type": {
                    "type": "string",
                    "enum": ["working", "episodic", "semantic"],
                    "description": "どのメモリ階層に保存するか",
                },
                "importance": {
                    "type": "number",
                    "description": "0.0から1.0までの重要度スコア",
                    "default": 0.5,
                },
            },
            "required": ["content", "memory_type"],
        },
    },
    {
        "name": "recall",
        "description": (
            "すべてのメモリ階層を横断して関連情報を検索する。"
            "ユーザーに尋ねる前に、何を知っているかを確認するために使う。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "メモリ内で検索する内容",
                },
                "limit": {
                    "type": "integer",
                    "description": "結果の最大件数（デフォルト5）",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "forget",
        "description": "IDと種類を指定して特定のメモリを削除する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "削除するメモリのID",
                },
                "memory_type": {
                    "type": "string",
                    "enum": ["episodic", "semantic"],
                    "description": "どのメモリ階層から削除するか",
                },
            },
            "required": ["memory_id", "memory_type"],
        },
    },
]


class MemoryAgent:
    """3階層のメモリとツール呼び出しループを持つパーソナルアシスタント。"""

    def __init__(self) -> None:
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.token_tracker = OpenRouterTokenTracker()
        self.memory = MemoryManager()
        self.messages: list[dict[str, Any]] = []
        self.max_iterations = 10

    def _build_system_prompt(self) -> str:
        """想起したメモリをシステムプロンプトに注入する。"""
        memory_context = self.memory.build_memory_context()
        return SYSTEM_PROMPT.format(memory_context=memory_context)

    def _execute_tool(self, name: str, tool_input: dict[str, Any]) -> str:
        """ツール呼び出しを適切なMemoryManagerのメソッドにディスパッチする。"""
        try:
            if name == "remember":
                return self.memory.remember(
                    content=tool_input["content"],
                    memory_type=tool_input["memory_type"],
                    importance=tool_input.get("importance", 0.5),
                )
            elif name == "recall":
                return self.memory.recall(
                    query=tool_input["query"],
                    limit=tool_input.get("limit", 5),
                )
            elif name == "forget":
                return self.memory.forget(
                    memory_id=tool_input["memory_id"],
                    memory_type=tool_input["memory_type"],
                )
            else:
                return f"Unknown tool: {name}"
        except Exception as e:
            logger.error("Tool '%s' failed: %s", name, e)
            return f"Error executing {name}: {e}"

    def chat(self, user_message: str, console: Console) -> str:
        """メッセージを送信し、エージェント的なツール呼び出しループを処理する。"""
        self.messages.append({"role": "user", "content": user_message})

        for _iteration in range(self.max_iterations):
            try:
                response: ChatResult = self.client.chat.send(  # type: ignore[call-overload]
                    model=MODEL,
                    max_tokens=4096,
                    reasoning={"effort": "none"},
                    messages=[
                        {"role": "system", "content": self._build_system_prompt()},
                        *self.messages,
                    ],
                    tools=to_openrouter_tools(MEMORY_TOOLS),
                    tool_choice="auto",
                )
            except TooManyRequestsResponseError:
                logger.warning("Rate limited — waiting 30s before retry...")
                time.sleep(30)
                continue
            except OpenRouterError as e:
                logger.error("API error: %s", e)
                return f"API error: {e}"

            self.token_tracker.track(response.usage)

            message = response.choices[0].message
            text = str(message.content or "")
            tool_calls = message.tool_calls

            if not text and not tool_calls:
                text = "Done."

            # アシスタントの応答を履歴に追加する
            if tool_calls:
                self.messages.append(
                    {
                        "role": "assistant",
                        "content": text or None,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in tool_calls
                        ],
                    }
                )
            else:
                self.messages.append({"role": "assistant", "content": text})

            # ツール呼び出しがなければテキスト応答を返す
            if not tool_calls:
                return text

            # 各ツールを実行し、進捗を表示する
            for tool_call in tool_calls:
                tool_input = json.loads(tool_call.function.arguments)
                input_summary = json.dumps(tool_input, separators=(",", ":"), ensure_ascii=False)
                if len(input_summary) > 80:
                    input_summary = input_summary[:77] + "..."
                console.print(f"  [dim][tool: {tool_call.function.name}] {input_summary}[/dim]")

                result = self._execute_tool(tool_call.function.name, tool_input)

                result_preview = result.split("\n")[0][:80]
                console.print(f"  [dim]  → {result_preview}[/dim]")

                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    }
                )

        return "Reached maximum iterations."

    def loaded_memory_count(self) -> int:
        """前回までのセッションから読み込まれた永続メモリの件数。"""
        stats = self.memory.get_stats()
        total: int = stats["episodic"]["count"] + stats["semantic"]["count"]
        return total


def main() -> None:
    """メモリ拡張パーソナルアシスタントを実行する。"""
    console = Console()

    agent = MemoryAgent()
    loaded = agent.loaded_memory_count()

    # ウェルカムパネル
    status_line = (
        f"[green]Loaded {loaded} memories from previous sessions[/green]"
        if loaded
        else ("[dim]No previous memories — this is a fresh start[/dim]")
    )

    header = Panel(
        "[bold cyan]Memory Systems — Personal Assistant[/bold cyan]\n\n"
        "3階層のメモリでセッションをまたいで記憶するパーソナルアシスタント:\n"
        "  [bold]Working[/bold]  — 一時的なセッションバッファ（自動でクリア）\n"
        "  [bold]Episodic[/bold] — タイムスタンプ付きイベント（JSONに永続化）\n"
        "  [bold]Semantic[/bold] — 事実と知識（ChromaDBに永続化）\n\n"
        f"{status_line}\n\n"
        "[bold]試してみてください:[/bold]\n"
        '  • "こんにちは、Alexです。Acme Corpで働いています"\n'
        '  • "JavaScriptよりPythonの方が好きです"\n'
        '  • "私について何を覚えていますか？"（再起動後）\n\n'
        '[dim]"exit"または"quit"と入力するとセッションを終了します[/dim]',
        title="Tutorial 05 — Memory Systems",
    )
    console.print(header)

    try:
        while True:
            console.print("\n[bold green]You:[/bold green] ", end="")
            try:
                user_input = input().strip()
            except EOFError:
                break

            if user_input.lower() in ("exit", "quit", "q", ""):
                break

            response = agent.chat(user_input, console)
            if response:
                console.print("\n[bold blue]Assistant:[/bold blue]")
                console.print(Markdown(response))

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")

    # セッションを長期メモリに統合する
    console.print("\n[dim]Consolidating session memories...[/dim]")
    saved = agent.memory.consolidate(agent.messages, agent.client, MODEL)
    if saved:
        console.print(f"[green]Saved {len(saved)} memories for next session:[/green]")
        for item in saved:
            console.print(f"  [dim]{item}[/dim]")
    else:
        console.print("[dim]No new memories to consolidate.[/dim]")

    console.print()
    agent.token_tracker.report()


if __name__ == "__main__":
    main()
