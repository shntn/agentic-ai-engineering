"""
ストリーミングの基礎 (OpenRouter)

OpenRouterによるリアルタイムのトークン単位のストリーミングを実演します。2つの
アプローチを扱います: シンプルなテキスト差分の反復処理（手早く使える）と、
チャンク内のフィールドを見てブロックの種類を判断するイベントベースの
イテレーション（ストリーミングのライフサイクル全体を可視化）です。
"""

import os
from typing import Any

from dotenv import find_dotenv, load_dotenv
from openrouter import OpenRouter
from openrouter.components import ChatUsage
from openrouter.errors import OpenRouterError
from openrouter.utils.eventstreaming import EventStream
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel

from common import OpenRouterTokenTracker, setup_logging

# ルートの.envファイルから環境変数を読み込む
load_dotenv(find_dotenv())

# ロギングを設定
logger = setup_logging(__name__)

MODEL = "deepseek/deepseek-v4-flash-0731"

SYSTEM_PROMPT = (
    "あなたは役に立つアシスタントです。応答は簡潔かつ構造化された形で提供してください。"
    "読みやすさのため、見出し・箇条書き・太字などのMarkdown書式を使用してください。"
)


class StreamingChat:
    """リアルタイムでレンダリングされるストリーミング応答による対話型チャット。"""

    def __init__(self, model: str, token_tracker: OpenRouterTokenTracker) -> None:
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.model = model
        self.token_tracker = token_tracker
        self.messages: list[dict[str, str]] = []

    def _stream_llm(self, messages: list[dict[str, Any]], *, max_tokens: int = 2048) -> EventStream:
        """ストリーミングモードでLLM呼び出しを行う"""
        logger.info("Calling %s (stream)", self.model)
        response: EventStream = self.client.chat.send(  # type: ignore[call-overload]
            model=self.model,
            max_tokens=max_tokens,
            reasoning={"effort": "none"},
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, *messages],
            stream=True,
        )
        return response

    def stream_simple(self, user_input: str, console: Console) -> str:
        """テキスト差分をそのまま反復処理してストリーミングする。

        これはストリーミングを行う最も簡単な方法——各チャンクの`delta.content`を
        ただ連結していくだけです。イベントレベルの制御が不要なシンプルな
        ユースケースに最適です。
        """
        self.messages.append({"role": "user", "content": user_input})
        logger.info("Streaming response (simple mode, history: %d messages)", len(self.messages))

        accumulated = ""
        usage: ChatUsage | None = None

        with self._stream_llm(self.messages) as stream:
            with Live(Markdown(""), refresh_per_second=15, console=console) as live:
                for chunk in stream:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        accumulated += delta.content
                        live.update(Markdown(accumulated))

                    # usageはストリームの最後のチャンクにのみ入る
                    if chunk.usage is not None:
                        usage = chunk.usage

        # トークン使用量はストリーム完了後に取得できる
        if usage is not None:
            self.token_tracker.track(usage)
            logger.info(
                "Stream complete — input: %d, output: %d tokens",
                usage.prompt_tokens,
                usage.completion_tokens,
            )

        self.messages.append({"role": "assistant", "content": accumulated})
        return accumulated

    def stream_with_events(self, user_input: str, console: Console) -> str:
        """チャンク内のフィールドを見てブロックの種類を判断するイベントベースの
        イテレーションでストリーミングする。

        OpenRouterのストリームにはAnthropicのような個別のイベント型
        （content_block_start/deltaなど）は存在しません。代わりに、各チャンクの
        `delta`内のどのフィールドが値を持つかでブロックの種類を判断します。
        細かい制御が必要な場合（例: ツール呼び出しの検出、reasoningブロックと
        本文ブロックの境界の追跡）に使用してください。
        """
        self.messages.append({"role": "user", "content": user_input})
        logger.info("Streaming response (event mode, history: %d messages)", len(self.messages))

        accumulated = ""
        usage: ChatUsage | None = None
        in_reasoning_block = False
        in_content_block = False

        with self._stream_llm(self.messages) as stream:
            with Live(Markdown(""), refresh_per_second=15, console=console) as live:
                for chunk in stream:
                    choice = chunk.choices[0]
                    delta = choice.delta

                    # --- reasoningブロックの開始/継続 ---
                    # （Anthropicのcontent_block_start/delta相当。reasoningを
                    # 返さないモデルの場合、このブロックは常にスキップされる）
                    if delta.reasoning:
                        if not in_reasoning_block:
                            logger.debug("Reasoning block started")
                            in_reasoning_block = True

                    # --- 本文テキストブロックの開始/継続 ---
                    if delta.content:
                        if not in_content_block:
                            logger.debug("Content block started")
                            in_content_block = True
                        accumulated += delta.content
                        live.update(Markdown(accumulated))

                    # --- 終了理由（Anthropicのmessage_delta相当） ---
                    if choice.finish_reason:
                        logger.debug("Stop reason: %s", choice.finish_reason)

                    # --- usage（Anthropicのmessage_stop相当、最後のチャンクにのみ入る） ---
                    if chunk.usage is not None:
                        usage = chunk.usage

        if usage is not None:
            self.token_tracker.track(usage)
            logger.info(
                "Stream complete — input: %d, output: %d tokens",
                usage.prompt_tokens,
                usage.completion_tokens,
            )

        self.messages.append({"role": "assistant", "content": accumulated})
        return accumulated

    def reset(self) -> None:
        """新しく始めるために会話履歴をクリアする。"""
        self.messages.clear()
        logger.info("Conversation history cleared")


def main() -> None:
    """モード選択付きの対話型ストリーミングチャット。"""
    console = Console()
    token_tracker = OpenRouterTokenTracker()
    chat = StreamingChat(MODEL, token_tracker)

    console.print(
        Panel(
            "[bold cyan]Streaming Chat[/bold cyan]\n\n"
            "OpenRouterによるリアルタイムのトークン単位ストリーミングを体験してください。\n\n"
            "[bold]2つのストリーミングモード:[/bold]\n"
            "  [green]simple[/green]   — テキスト差分の反復処理（最も簡単、テキストのみ）\n"
            "  [green]events[/green]   — イベントベースのイテレーション（ライフサイクルを完全に制御）\n\n"
            "[bold]mode simple[/bold] または [bold]mode events[/bold] と入力して切り替えます。\n"
            "[bold]clear[/bold] と入力すると会話履歴がリセットされます。\n"
            "[bold]quit[/bold] または [bold]exit[/bold] と入力すると終了します。",
            title="02-streaming / 01 — Streaming Fundamentals",
        )
    )

    mode = "simple"
    console.print(f"\n[dim]Current mode: {mode}[/dim]")

    while True:
        console.print("\n[bold green]You:[/bold green] ", end="")
        try:
            user_input = input().strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Interrupted.[/yellow]")
            break

        if not user_input or user_input.lower() in ("quit", "exit"):
            console.print("[yellow]Ending session...[/yellow]")
            break

        if user_input.lower() == "clear":
            chat.reset()
            console.print("[dim]Conversation cleared.[/dim]")
            continue

        if user_input.lower().startswith("mode "):
            new_mode = user_input.split(" ", 1)[1].strip().lower()
            if new_mode in ("simple", "events"):
                mode = new_mode
                console.print(f"[dim]Switched to {mode} mode.[/dim]")
            else:
                console.print("[red]Unknown mode. Use 'simple' or 'events'.[/red]")
            continue

        try:
            console.print("\n[bold blue]Assistant:[/bold blue]")
            if mode == "simple":
                chat.stream_simple(user_input, console)
            else:
                chat.stream_with_events(user_input, console)
        except OpenRouterError as e:
            logger.error("API error: %s", e)
            console.print(f"\n[red]API error: {e}[/red]")

    console.print()
    token_tracker.report()
    console.print(f"[dim]Messages exchanged: {len(chat.messages)}[/dim]")


if __name__ == "__main__":
    main()
