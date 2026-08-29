"""
Agentic RAG (OpenRouter)

RAGを、エージェントループの中の1つのツールとして実演します。エージェントが
*いつ*検索するか、*どんなクエリ*を組み立てるか、*結果が十分かどうか*を自分で
判断します。初回の検索結果が不十分であれば、エージェントはクエリを再構成して
もう一度検索します。

スクリプト03（パイプラインRAG）との対比: あちらはすべての質問に対して検索が
トリガーされます。こちらはエージェントが判断力を発揮します——会話のコンテキスト
だけで答えられる質問もあり、エージェントは自分自身で検索クエリを選びます。

OPENROUTER_API_KEY 環境変数が必要です。
"""

import json
import os
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv
from openrouter import OpenRouter
from openrouter.components import ChatResult
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from common import OpenRouterTokenTracker, setup_logging
from openrouter_adapter import to_openrouter_tools
from rag_openrouter import HybridRetriever, LocalEmbedder, Reranker, VectorStore, recursive_split

# ルートの.envファイルから環境変数を読み込む
load_dotenv(find_dotenv())

# ロギングを設定
logger = setup_logging(__name__)

# モデル設定
MODEL = "deepseek/deepseek-v4-flash-0731"
SAMPLE_DOCS_DIR = Path(__file__).parent / "sample_docs_openrouter"
CHROMA_PERSIST_DIR = str(Path(__file__).parent / ".chroma_db_agentic_openrouter")

SYSTEM_PROMPT = (
    "あなたはTechFlow Solutionsのテクニカルサポートエージェントで、検索ツール経由で"
    "会社のドキュメントにアクセスできます。\n\n"
    "ガイドライン:\n"
    "- 具体的な技術的詳細が必要なときはドキュメントを検索してください\n"
    "- 広範なクエリではなく、的を絞った具体的な検索クエリを使ってください\n"
    "- 初回の結果が不十分であれば、クエリを再構成してもう一度検索してください\n"
    "- すべての質問で検索する必要はありません——自分の判断を使ってください\n"
    "- 情報の出典となるドキュメントを常に引用してください（例: [api_reference.md]）\n"
    "- ドキュメントがそのトピックをカバーしていない場合は、その旨を明確に伝えてください"
)

TOOLS = [
    {
        "name": "search_docs",
        "description": (
            "TechFlowのドキュメントを検索し、情報を取得する。"
            "最良の結果を得るには、具体的で的を絞ったクエリを使うこと。"
            "異なるクエリで複数回呼び出し、さらに情報を探すこともできる。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "検索クエリ——具体的に、専門用語を使うこと",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返す結果の件数（デフォルト5、最大10）",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    }
]


class AgenticRAG:
    """検索を推論ループの中のツールとして使うエージェント。"""

    def __init__(
        self,
        model: str,
        retriever: HybridRetriever,
        token_tracker: OpenRouterTokenTracker,
    ):
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.model = model
        self.retriever = retriever
        self.token_tracker = token_tracker
        self.messages: list[dict[str, Any]] = []

    def chat(self, user_input: str, console: Console) -> str:
        """エージェントループ: 送信 → ツール呼び出しを検知 → 検索を実行 → 継続。"""
        self.messages.append({"role": "user", "content": user_input})

        # エージェントループ——モデルがテキスト応答を生成するまで続く
        while True:
            response: ChatResult = self.client.chat.send(  # type: ignore[call-overload]
                model=self.model,
                max_tokens=1024,
                reasoning={"effort": "none"},
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, *self.messages],
                tools=to_openrouter_tools(TOOLS),
                tool_choice="auto",
            )

            self.token_tracker.track(response.usage)

            message = response.choices[0].message
            text = str(message.content or "")
            tool_calls = message.tool_calls

            # モデルがツールを使いたがっているか確認する
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

                for tool_call in tool_calls:
                    tool_input = self._parse_tool_input(tool_call.function.arguments)
                    query = tool_input.get("query", "")
                    top_k = min(tool_input.get("top_k", 5), 10)

                    console.print(f"  [dim]Searching:[/dim] [italic]{query}[/italic]")

                    result = self._execute_search(query, top_k)
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result,
                        }
                    )

                continue

            # モデルが最終的なテキスト応答を生成した
            self.messages.append({"role": "assistant", "content": text})
            return text

    def _parse_tool_input(self, arguments: str) -> dict[str, Any]:
        """ツール呼び出しの引数（JSON文字列）をパースする。"""
        parsed: dict[str, Any] = json.loads(arguments)
        return parsed

    def _execute_search(self, query: str, top_k: int) -> str:
        """検索を実行し、エージェント向けに結果を整形する。"""
        chunks = self.retriever.retrieve(query, top_k=top_k)

        if not chunks:
            return "No relevant documents found for this query."

        results = []
        for i, chunk in enumerate(chunks, 1):
            results.append(f"[{i}] Source: {chunk.source}\n{chunk.content}")

        return "\n\n---\n\n".join(results)


def _build_retriever() -> HybridRetriever:
    """検索スタックを構築し、ドキュメントを取り込む。"""
    embedder = LocalEmbedder()
    store = VectorStore(embedder, persist_dir=CHROMA_PERSIST_DIR)
    reranker = Reranker()
    retriever = HybridRetriever(store, reranker)

    # サンプルドキュメントを取り込む
    all_chunks = []
    for doc_path in sorted(SAMPLE_DOCS_DIR.glob("*.md")):
        text = doc_path.read_text(encoding="utf-8")
        chunks = recursive_split(text, source=doc_path.name)
        all_chunks.extend(chunks)

    store.add_chunks(all_chunks)
    return retriever


def main() -> None:
    """Agentic RAGのデモ用メインオーケストレーション関数。"""
    console = Console()
    token_tracker = OpenRouterTokenTracker()

    console.print(
        Panel(
            "[bold cyan]Agentic RAG Demo[/bold cyan]\n\n"
            "パイプラインRAG（スクリプト03）と違い、このエージェントは[bold]いつ[/bold]検索するか、\n"
            "[bold]どんなクエリ[/bold]を使うか、[bold]結果が十分かどうか[/bold]を自分で判断します。\n\n"
            "エージェントは[cyan]search_docs[/cyan]ツールを呼び出せます——呼び出さないという選択も\n"
            "できます。注目してほしいポイント:\n"
            "  - エージェントが自分で検索クエリを選ぶ（あなたの質問とは異なることがある）\n"
            "  - 複雑な質問に対して複数回検索する\n"
            "  - 検索せずに会話のコンテキストだけで回答する\n\n"
            "[bold]試してみてください:[/bold]\n"
            "  1. APIで認証するにはどうすればいいですか？\n"
            "  2. デプロイが失敗するとどうなりますか？（続けて: データベースのロールバックは？）\n"
            "  3. なぜAPIリクエストが遅くなることがあるのですか？\n"
            "  4. 異なるプランティアを比較してください。\n\n"
            "[bold]'quit'[/bold] または [bold]'exit'[/bold] と入力すると終了します。",
            title="Agentic RAG",
        )
    )

    # 検索スタックを構築する（初回実行時に埋め込みモデルとリランカーがダウンロードされる）
    console.print("\n[bold]Loading models and ingesting documents...[/bold]")
    try:
        with console.status(
            "[bold]Loading models (first run downloads several hundred MB)...[/bold]"
        ):
            retriever = _build_retriever()
        doc_count = len(list(SAMPLE_DOCS_DIR.glob("*.md")))
        console.print(f"[green]Indexed documents from {doc_count} files[/green]\n")
    except Exception as e:
        logger.error("Ingestion failed: %s", e)
        console.print(f"[red]Ingestion failed: {e}[/red]")
        return

    agent = AgenticRAG(MODEL, retriever, token_tracker)

    while True:
        console.print("[bold green]You:[/bold green] ", end="")
        user_input = input().strip()

        if user_input.lower() in ["quit", "exit", ""]:
            console.print("\n[yellow]Ending session...[/yellow]")
            break

        try:
            response = agent.chat(user_input, console)

            console.print("\n[bold blue]Agent:[/bold blue]")
            console.print(Markdown(response))
            console.print()

        except Exception as e:
            logger.error("Error during chat: %s", e)
            console.print(f"\n[red]Error: {e}[/red]")
            break

    # 最終的なトークンレポート
    console.print()
    token_tracker.report()


if __name__ == "__main__":
    main()
