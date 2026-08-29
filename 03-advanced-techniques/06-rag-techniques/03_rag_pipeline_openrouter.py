"""
RAGパイプライン (OpenRouter)

完全なRetrieval-Augmented Generationパイプラインを実演します: ドキュメントの
取り込み → チャンク分割 → ローカルのsentence-transformerモデルで埋め込み →
ChromaDB + BM25でインデックス化 → ハイブリッド検索とリランキングで検索 →
最後にLLMで回答を生成します。

OPENROUTER_API_KEY 環境変数が必要です。
"""

import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from openrouter import OpenRouter
from openrouter.components import ChatResult
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from common import OpenRouterTokenTracker, setup_logging
from common.menu import interactive_menu
from rag_openrouter import HybridRetriever, LocalEmbedder, Reranker, VectorStore, recursive_split

# ルートの.envファイルから環境変数を読み込む
load_dotenv(find_dotenv())

# ロギングを設定
logger = setup_logging(__name__)

# モデル設定
MODEL = "deepseek/deepseek-v4-flash-0731"
SAMPLE_DOCS_DIR = Path(__file__).parent / "sample_docs_openrouter"
CHROMA_PERSIST_DIR = str(Path(__file__).parent / ".chroma_db_openrouter")

SYSTEM_PROMPT = (
    "あなたはTechFlow Solutionsのテクニカルサポートアシスタントです。"
    "提供されたコンテキスト**のみ**を使って質問に答えてください。"
    "各事実には出典ドキュメントを引用してください（例: [api_reference.md]）。"
    "コンテキストに答えが含まれていない場合は、その旨を明確に伝えてください——"
    "でっち上げないでください。"
)

# 異なるドキュメントと検索モードをカバーする、あらかじめ定義したデモ用の質問
DEMO_QUESTIONS = [
    "TechFlow APIで認証するにはどうすればいいですか？",
    # 「TechFlowは」を含めると、全文書に頻出するこの単語に埋め込みベクトルが
    # 引っ張られ、正解チャンク（Redisのキャッシュ記述）がベクトル検索の上位から
    # 漏れることを実測で確認したため、この質問だけ「TechFlowは」を外している。
    "キャッシュにどのデータベースを使っていますか？",
    "失敗したデプロイをロールバックするにはどうすればいいですか？",
    "Webhookが発火しないのはなぜですか？",
    "Proプランのレート制限はどれくらいですか？",
    "TechFlowのアーキテクチャにおいて、サービス同士がどう通信するか説明してください。",
]


class RAGPipeline:
    """完全なRAGパイプライン: 取り込み → 検索 → 生成。"""

    def __init__(self, model: str, token_tracker: OpenRouterTokenTracker):
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.model = model
        self.token_tracker = token_tracker

        # 検索スタックを構築する
        self.embedder = LocalEmbedder()
        self.store = VectorStore(self.embedder, persist_dir=CHROMA_PERSIST_DIR)
        self.reranker = Reranker()
        self.retriever = HybridRetriever(self.store, self.reranker)

    def ingest(self, docs_dir: Path) -> int:
        """markdownファイルを読み込み、チャンク分割・埋め込み・インデックス化する。チャンク数を返す。"""
        all_chunks = []

        for doc_path in sorted(docs_dir.glob("*.md")):
            text = doc_path.read_text(encoding="utf-8")
            chunks = recursive_split(text, source=doc_path.name)
            all_chunks.extend(chunks)
            logger.info("Chunked %s → %d chunks", doc_path.name, len(chunks))

        self.store.add_chunks(all_chunks)
        return len(all_chunks)

    def query(self, question: str, top_k: int = 5) -> tuple[str, list]:
        """関連するチャンクを検索し、引用付きの回答を生成する。"""
        chunks = self.retriever.retrieve(question, top_k=top_k)
        context = self._build_context(chunks)
        answer = self._generate(question, context)
        return answer, chunks

    def _build_context(self, chunks: list) -> str:
        """検索されたチャンクを番号付きのコンテキストブロックとして整形する。"""
        if not chunks:
            return "No relevant context found."

        blocks = []
        for i, chunk in enumerate(chunks, 1):
            blocks.append(f"[{i}] Source: {chunk.source}\n{chunk.content}")
        return "\n\n---\n\n".join(blocks)

    def _generate(self, question: str, context: str) -> str:
        """質問とコンテキストをLLMに送信し、回答を返す。"""
        user_message = f"Context:\n{context}\n\nQuestion: {question}"

        response: ChatResult = self.client.chat.send(  # type: ignore[call-overload]
            model=self.model,
            max_tokens=1024,
            reasoning={"effort": "none"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
        )

        self.token_tracker.track(response.usage)
        return str(response.choices[0].message.content or "")


def _render_chunks(console: Console, chunks: list) -> None:
    """検索されたチャンクを出典とプレビュー付きで表示する。"""
    table = Table(show_header=True, box=None, padding=(0, 1))
    table.add_column("#", style="dim", width=3)
    table.add_column("Source", style="cyan", min_width=20)
    table.add_column("Preview", ratio=1)

    for i, chunk in enumerate(chunks, 1):
        preview = chunk.content[:120].replace("\n", " ") + "..."
        table.add_row(str(i), chunk.source, f"[dim]{preview}[/dim]")

    console.print(Panel(table, title="Retrieved Chunks", border_style="dim", padding=(0, 1)))


def _run_demo(console: Console, pipeline: RAGPipeline) -> None:
    """あらかじめ定義したデモ用の質問を1問ずつ、ユーザーの入力を待ちながら実行する。"""
    console.print(f"\n[bold]Running {len(DEMO_QUESTIONS)} demo questions.[/bold]")
    console.print("[dim]Enterキーで各質問を実行、'q'で停止します。[/dim]\n")

    for i, question in enumerate(DEMO_QUESTIONS, 1):
        console.print(f"[bold green]Question {i}/{len(DEMO_QUESTIONS)}:[/bold green] {question}")
        console.print("[dim]Enterキーで実行...[/dim] ", end="")
        try:
            if input().strip().lower() == "q":
                break
        except EOFError:
            break

        try:
            answer, chunks = pipeline.query(question)

            _render_chunks(console, chunks)

            console.print("\n[bold blue]Answer:[/bold blue]")
            console.print(Markdown(answer))
            console.print("\n" + "─" * 60 + "\n")

        except Exception as e:
            logger.error("Error processing question %d: %s", i, e)
            console.print(f"[red]Error: {e}[/red]\n")


def _run_interactive(console: Console, pipeline: RAGPipeline) -> None:
    """対話モード——ユーザーが質問する。"""
    console.print(
        "\n[bold]Interactive mode[/bold] — TechFlowについて質問してください。\n"
        "[bold]'quit'[/bold] または [bold]'exit'[/bold] と入力すると終了します。\n"
    )

    while True:
        console.print("[bold green]Question:[/bold green] ", end="")
        user_input = input().strip()

        if user_input.lower() in ["quit", "exit", ""]:
            break

        try:
            answer, chunks = pipeline.query(user_input)

            _render_chunks(console, chunks)

            console.print("\n[bold blue]Answer:[/bold blue]")
            console.print(Markdown(answer))
            console.print()

        except Exception as e:
            logger.error("Error processing question: %s", e)
            console.print(f"\n[red]Error: {e}[/red]")


def main() -> None:
    """RAGパイプラインのデモ用メインオーケストレーション関数。"""
    console = Console()
    token_tracker = OpenRouterTokenTracker()

    with console.status("[bold]Loading embedding model (first run downloads ~80MB)...[/bold]"):
        pipeline = RAGPipeline(MODEL, token_tracker)

    header = Panel(
        "[bold cyan]RAG Pipeline Demo[/bold cyan]\n\n"
        "このデモはTechFlowのドキュメントを取り込み、ハイブリッドインデックス\n"
        "（ベクトル + BM25）を構築し、出典引用付きで質問に答えます。\n\n"
        "[bold]パイプライン:[/bold] チャンク分割 → 埋め込み（ローカル） → "
        "インデックス化（ChromaDB + BM25）\n"
        "         → ハイブリッド検索 → リランク（CrossEncoder） → 生成（LLM）\n\n"
        "[bold]サンプルの質問:[/bold]\n"
        "  1. TechFlow APIで認証するにはどうすればいいですか？\n"
        "  2. キャッシュにどのデータベースを使っていますか？\n"
        "  3. 失敗したデプロイをロールバックするにはどうすればいいですか？\n"
        "  4. Webhookが発火しないのはなぜですか？\n"
        "  5. Proプランのレート制限はどれくらいですか？",
        title="RAG Pipeline",
    )
    console.print(header)

    # ドキュメントを取り込む
    console.print("\n[bold]Ingesting documents...[/bold]")
    try:
        chunk_count = pipeline.ingest(SAMPLE_DOCS_DIR)
        console.print(
            f"[green]Indexed {chunk_count} chunks from "
            f"{len(list(SAMPLE_DOCS_DIR.glob('*.md')))} documents[/green]\n"
        )
    except Exception as e:
        logger.error("Ingestion failed: %s", e)
        console.print(f"[red]Ingestion failed: {e}[/red]")
        return

    mode = interactive_menu(
        console,
        items=[
            "Demo — フルパイプラインでサンプル質問を実行",
            "Interactive — 自分で質問を入力",
        ],
        title="Select Mode",
    )

    if mode is None:
        return

    if mode.startswith("Demo"):
        _run_demo(console, pipeline)
    else:
        _run_interactive(console, pipeline)

    # 最終的なトークンレポート
    console.print()
    token_tracker.report()


if __name__ == "__main__":
    main()
