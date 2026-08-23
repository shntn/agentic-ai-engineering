"""
拡張LLM — コードベースナビゲーター (OpenRouter)

「拡張LLM」パターンを実演します: 検索（RAG）、ツール、メモリで強化されたLLMです。
これはAnthropicの「Building Effective Agents」ガイドで説明されている、
すべてのエージェントシステムの基礎となるビルディングブロックです。

コードベースナビゲーターは、エンジニアが不慣れなコードベースを探索し、理解するのを助けます。
任意のGitHubリポジトリを指定すると、クローン・インデックス化を行い、セッションをまたいで
メモリを維持しながらセマンティック検索を使って質問に答えます。
"""

import json
import os
import time
from typing import Any

from dotenv import find_dotenv, load_dotenv
from openrouter import OpenRouter, errors as openrouter_errors
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from common import OpenRouterTokenTracker, setup_logging
from common.menu import interactive_menu
from openrouter_adapter import to_openrouter_tools

# 環境変数をロードする
load_dotenv(find_dotenv())

logger = setup_logging(__name__)

SUGGESTED_REPOS = [
    "openai/swarm",
    "strands-agents/sdk-python",
    "anthropics/anthropic-sdk-python",
    "pallets/flask",
]


SYSTEM_PROMPT = """あなたはコードベースナビゲーター — ソフトウェアエンジニアがコードベースを\
探索し、理解するのを助けるAIアシスタントです。

## あなたの能力

以下のためのツールにアクセスできます:
- GitHubリポジトリのクローンとインデックス化 (clone_and_index)
- インデックス化済みリポジトリの一覧表示 (list_repos)
- コードのセマンティック検索 (search_code)
- ファイル全体の内容の読み込み (read_file)
- ディレクトリ構造の探索 (list_directory)
- 正規表現での正確なパターン検索 (grep)
- 今後のセッションのためのメモリ保存 (save_memory)
- 保存済みメモリの呼び出し (recall_memory)

## ユーザーの助け方

ユーザーがGitHubリポジトリに言及したとき（「pallets/flask」や「httpieリポジトリを見て」など）:
1. まず clone_and_index を使ってクローン・インデックス化する
2. その後、search_code や read_file などを使って質問に答える

意味的・概念的な質問には search_code を使う:
- 「認証はどう機能する？」
- 「データベース接続はどこで処理されている？」

正確な一致には grep を使う:
- 「TODOコメントをすべて見つけて」
- 「UserModelはどこで定義されている？」

関連するチャンクを見つけた後、完全なコンテキストが必要な場合は read_file を使う。

## メモリ

重要な洞察をメモリに保存してください。特に:
- 発見したアーキテクチャパターン
- 主要なファイルとその役割
- 異なるリポジトリ間のつながり
- 情報の提示のされ方についてのユーザーの好み

会話の開始時には recall_memory を確認し、コンテキストを思い出してください。

## 応答スタイル

- 簡潔に、しかし十分な内容で
- ファイルパスと行番号とともに関連するコードスニペットを示す
- アーキテクチャ上の決定を発見した場合は説明する
- 探索すべき関連領域を提案する"""


# -- ツールレジストリ ----------------------------------------------------------


def _build_tool_definitions() -> list[dict[str, Any]]:
    """ツールモジュールからすべてのツール定義を収集する"""
    from tools_ja.files import FILE_TOOLS
    from tools_ja.memory import MEMORY_TOOLS
    from tools_ja.repo import REPO_TOOLS
    from tools_ja.search import SEARCH_TOOLS

    return MEMORY_TOOLS + REPO_TOOLS + FILE_TOOLS + SEARCH_TOOLS


class CodeNavigatorAgent:
    """
    検索、ツール、メモリで強化されたLLM。

    エージェントループを実装する: メッセージ送信 → ツール実行 → 結果送信 → 繰り返し
    （LLMがテキストのみで応答するまで）
    """

    def __init__(self, model: str = "deepseek/deepseek-v4-flash") -> None:
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.model = model
        self.token_tracker = OpenRouterTokenTracker()
        # Anthropicのinput_schema形式をOpenRouterのfunction calling形式に変換する
        self.tools = to_openrouter_tools(_build_tool_definitions())
        self.messages: list[dict[str, Any]] = []
        self.max_iterations = 15

        # 共有コンポーネントを初期化する（3つの拡張）
        from indexer_ja.embedder import Embedder
        from store_ja.memory import MemoryStore
        from store_ja.vector import VectorStore

        self.memory = MemoryStore()
        self.vector_store = VectorStore()
        self.embedder = Embedder()

    def _build_system_prompt(self) -> str:
        """メモリのコンテキストを含めてシステムプロンプトを構築する"""
        memory_summary = self.memory.summary()
        if memory_summary and memory_summary != "まだメモリは保存されていません。":
            return SYSTEM_PROMPT + f"\n\n## 想起したメモリ\n{memory_summary}"
        return SYSTEM_PROMPT

    def _execute_tool(self, name: str, tool_input: dict[str, Any]) -> str:
        """ツール呼び出しを適切なハンドラーにディスパッチする"""
        from tools_ja.files import execute_list_directory, execute_read_file
        from tools_ja.memory import execute_recall_memory, execute_save_memory
        from tools_ja.repo import execute_clone_and_index, execute_list_repos
        from tools_ja.search import execute_grep, execute_search_code

        dispatch: dict[str, Any] = {
            "save_memory": lambda inp: execute_save_memory(self.memory, inp),
            "recall_memory": lambda inp: execute_recall_memory(self.memory, inp),
            "clone_and_index": lambda inp: execute_clone_and_index(
                self.vector_store, self.embedder, inp
            ),
            "list_repos": lambda inp: execute_list_repos(self.vector_store, inp),
            "read_file": lambda inp: execute_read_file(self.vector_store, inp),
            "list_directory": lambda inp: execute_list_directory(self.vector_store, inp),
            "search_code": lambda inp: execute_search_code(self.vector_store, self.embedder, inp),
            "grep": lambda inp: execute_grep(self.vector_store, self.embedder, inp),
        }

        handler = dispatch.get(name)
        if not handler:
            return f"不明なツール: {name}"

        try:
            return str(handler(tool_input))
        except Exception as e:
            logger.error("Tool '%s' failed: %s", name, e)
            return f"Error executing {name}: {e}"

    def chat(self, user_message: str, console: Console) -> str:
        """メッセージを送信し、エージェントのツール使用ループを処理する"""
        self.messages.append({"role": "user", "content": user_message})

        for _iteration in range(self.max_iterations):
            try:
                response = self.client.chat.send(  # type: ignore[call-overload]
                    model=self.model,
                    max_tokens=4096,
                    reasoning={"effort": "none"},
                    tools=self.tools,
                    messages=[
                        {"role": "system", "content": self._build_system_prompt()},
                        *self.messages,
                    ],
                )
            except openrouter_errors.TooManyRequestsResponseError:
                logger.warning("Rate limited — waiting 30s before retry...")
                time.sleep(30)
                continue
            except openrouter_errors.OpenRouterError as e:
                logger.error("API error: %s", e)
                return f"API error: {e}"

            self.token_tracker.track(response.usage)

            message = response.choices[0].message
            finish_reason = response.choices[0].finish_reason
            tool_calls = message.tool_calls or []

            # アシスタントの応答が空にならないようにする（API要件）
            content = message.content
            if not content and not tool_calls:
                content = "完了しました。"

            assistant_message: dict[str, Any] = {"role": "assistant", "content": content}
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

            # ツール使用がなければテキスト応答を返す
            if finish_reason != "tool_calls" or not tool_calls:
                return content or "完了しました。"

            # 各ツールを実行し、進捗を表示する
            for tool_call in tool_calls:
                # arguments はJSON文字列で返るため、実行前にパースする
                tool_input = json.loads(tool_call.function.arguments)

                # 教育的な透明性のためにツール呼び出しを表示
                input_summary = json.dumps(tool_input, separators=(",", ":"))
                if len(input_summary) > 80:
                    input_summary = input_summary[:77] + "..."
                console.print(f"  [dim][tool: {tool_call.function.name}] {input_summary}[/dim]")

                result = self._execute_tool(tool_call.function.name, tool_input)

                # 簡潔な結果を表示
                result_preview = result.split("\n")[0][:80]
                console.print(f"  [dim]  → {result_preview}[/dim]")

                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    }
                )

        return "最大イテレーション回数に達しました。より具体的な質問をお試しください。"


# -- メイン -------------------------------------------------------------------


def main() -> None:
    """メインのオーケストレーション関数"""

    agent = CodeNavigatorAgent()

    console = Console()

    header = Panel(
        "[bold cyan]コードベースナビゲーター[/bold cyan]\n\n"
        "[green]検索（RAG）[/green]、[yellow]ツール[/yellow]、[magenta]メモリ[/magenta]で"
        "強化されたLLMです。\n\n"
        "インデックス化するリポジトリを選択し、コードベースについて質問してください。",
        title="Codebase Navigator",
    )

    repo = interactive_menu(
        console,
        SUGGESTED_REPOS,
        title="探索するリポジトリを選択",
        header=header,
        allow_custom=True,
        custom_prompt="owner/repo を入力してください（例: pallets/flask）",
    )
    if not repo:
        return

    console.print(f"\n[bold green]インデックス化中:[/bold green] {repo}")
    response = agent.chat(f"{repo} リポジトリをインデックス化してください", console)
    if response:
        console.print("\n[bold blue]Navigator:[/bold blue]")
        console.print(Markdown(response))

    console.print("\n[dim]コードベースについて質問してください。終了するには 'quit' と入力。[/dim]")

    try:
        while True:
            console.print("\n[bold green]You:[/bold green] ", end="")
            try:
                user_input = input().strip()
            except EOFError:
                break

            if user_input.lower() in ("exit", "quit", "q", ""):
                console.print("\n[yellow]セッションを終了します...[/yellow]")
                break

            response = agent.chat(user_input, console)

            if response:
                console.print("\n[bold blue]Navigator:[/bold blue]")
                console.print(Markdown(response))

            agent.token_tracker.report()

    except KeyboardInterrupt:
        console.print("\n[yellow]中断されました。[/yellow]")

    console.print()
    agent.token_tracker.report()


if __name__ == "__main__":
    main()
