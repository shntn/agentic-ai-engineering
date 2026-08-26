"""
コンテキストエンジニアリング (OpenRouter)

トークンカウント・予算配分・要約による自動圧縮を用いたコンテキストウィンドウ管理を
実演します。人為的に低く設定したコンテキスト予算を使い、数回のやり取りだけで
圧縮がトリガーされるようにしています。
"""

import os
from dataclasses import dataclass
from typing import Any

import tiktoken
from dotenv import find_dotenv, load_dotenv
from openrouter import OpenRouter
from openrouter.components import ChatResult
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from common import OpenRouterTokenTracker, setup_logging

# ルートの.envファイルから環境変数を読み込む
load_dotenv(find_dotenv())

# ロギングを設定
logger = setup_logging(__name__)

# モデル設定
MODEL = "deepseek/deepseek-v4-flash"

SYSTEM_PROMPT = (
    "あなたは博識なリサーチアシスタントです。これまでの議論の内容を踏まえながら、"
    "ユーザーがトピックを深く掘り下げるのを手伝います。会話の以前の部分を参照する"
    "際は、話が継続していることを示すために具体的な詳細に言及してください。"
)

# デモですぐに圧縮がトリガーされるよう、人為的に低い予算を設定
MAX_CONTEXT_TOKENS = 4096
RESPONSE_RESERVE = 2048
RECENT_MESSAGES_TO_KEEP = 4

# OpenRouterにはAnthropicのmessages.count_tokens()に相当する専用のトークンカウント
# APIがないため、tiktokenのcl100k_baseエンコーディングで近似する。実際のモデル
# （DeepSeek等）のトークナイザーとは厳密には一致しないが、予算管理のデモとしては
# 十分な精度であり、APIを呼ばずに無料・即座にカウントできる利点もある。
_ENCODING = tiktoken.get_encoding("cl100k_base")


@dataclass
class ContextBudget:
    """コンテキストの各コンポーネントにまたがるトークン予算の配分。"""

    max_context: int
    system_tokens: int = 0
    response_reserve: int = RESPONSE_RESERVE

    @property
    def history_budget(self) -> int:
        """会話履歴に利用可能なトークン数。"""
        return self.max_context - self.system_tokens - self.response_reserve


@dataclass
class TokenSnapshot:
    """予算表示用のトークン使用量のスナップショット。"""

    system: int = 0
    history: int = 0
    history_budget: int = 0
    reserve: int = 0
    message_count: int = 0
    compression_count: int = 0


class ContextManager:
    """コンテキストウィンドウの配分と会話の圧縮を管理する。"""

    def __init__(self, model: str, max_context: int, token_tracker: OpenRouterTokenTracker):
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.model = model
        self.token_tracker = token_tracker
        self.messages: list[dict[str, Any]] = []
        self.budget = ContextBudget(max_context=max_context)
        self.compression_count = 0

        # 初期化時に一度だけシステムプロンプトのトークン数を計測する
        self.budget.system_tokens = self._count_tokens([])
        logger.info(
            "Context budget — system: %d, history: %d, reserve: %d",
            self.budget.system_tokens,
            self.budget.history_budget,
            self.budget.response_reserve,
        )

    def chat(self, user_input: str) -> str:
        """メッセージを送信し、必要なら圧縮し、応答を返す。"""
        self.messages.append({"role": "user", "content": user_input})

        # 履歴が予算を超えていれば送信前に圧縮する
        self._compress_if_needed()

        logger.info(
            "Sending request (messages: %d, history tokens: ~%d/%d)",
            len(self.messages),
            self._count_tokens(self.messages) - self.budget.system_tokens,
            self.budget.history_budget,
        )

        response: ChatResult = self.client.chat.send(  # type: ignore[call-overload]
            model=self.model,
            max_tokens=self.budget.response_reserve,
            reasoning={"effort": "none"},
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, *self.messages],
        )

        self.token_tracker.track(response.usage)

        assistant_message = str(response.choices[0].message.content or "")
        self.messages.append({"role": "assistant", "content": assistant_message})

        return assistant_message

    def _count_tokens(self, messages: list[dict[str, Any]]) -> int:
        """tiktokenによる近似値でトークン数をカウントする。"""
        text = SYSTEM_PROMPT + "".join(str(m["content"]) for m in messages)
        return len(_ENCODING.encode(text))

    def _compress_if_needed(self) -> None:
        """履歴が予算を超えていれば、古いメッセージを要約する。"""
        history_tokens = self._count_tokens(self.messages) - self.budget.system_tokens

        if history_tokens <= self.budget.history_budget:
            return

        logger.info(
            "History (%d tokens) exceeds budget (%d tokens) — compressing",
            history_tokens,
            self.budget.history_budget,
        )

        # 分割: 直近のメッセージはそのまま残し、それ以外を要約する
        keep_count = min(RECENT_MESSAGES_TO_KEEP, len(self.messages))
        old_messages = self.messages[:-keep_count] if keep_count > 0 else self.messages
        recent_messages = self.messages[-keep_count:] if keep_count > 0 else []

        if not old_messages:
            logger.warning("No messages to compress — budget may be too small")
            return

        old_tokens = self._count_tokens(old_messages) - self.budget.system_tokens

        # 古いメッセージを要約する
        summary = self._summarize_messages(old_messages)

        # 古いメッセージを要約に置き換える
        summary_message = {
            "role": "user",
            "content": (
                f"[これまでの会話の要約]\n{summary}\n[要約終わり — ここから会話を続けてください]"
            ),
        }

        # ロールが交互になるようにする: 要約（user）の後に直近のメッセージを続ける
        # recent_messagesがuserメッセージから始まる場合、assistantの受領応答が必要
        if recent_messages and recent_messages[0]["role"] == "user":
            self.messages = [
                summary_message,
                {"role": "assistant", "content": "了解しました。会話の文脈を把握しています。"},
                *recent_messages,
            ]
        else:
            self.messages = [summary_message, *recent_messages]

        new_tokens = self._count_tokens(self.messages) - self.budget.system_tokens
        self.compression_count += 1

        logger.info(
            "Compressed %d messages: %d → %d tokens (saved %d tokens)",
            len(old_messages),
            old_tokens,
            new_tokens,
            old_tokens - new_tokens,
        )

    def _summarize_messages(self, messages: list[dict[str, Any]]) -> str:
        """LLMを使ってメッセージのまとまりを要約する。"""
        # 要約者向けに読みやすいトランスクリプトを組み立てる
        transcript = "\n".join(
            f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}" for m in messages
        )

        response: ChatResult = self.client.chat.send(  # type: ignore[call-overload]
            model=self.model,
            max_tokens=1024,
            reasoning={"effort": "none"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "以下の会話を簡潔に要約してください。ユーザーが言及した重要な"
                        "事実・決定事項・具体的な詳細は保持してください。三人称過去形で"
                        "記述してください。簡潔かつ漏れなくまとめてください。"
                    ),
                },
                {"role": "user", "content": transcript},
            ],
        )

        self.token_tracker.track(response.usage)
        return str(response.choices[0].message.content or "")

    def get_token_snapshot(self) -> TokenSnapshot:
        """予算表示用に現在のトークン数を返す。"""
        history_tokens = 0
        if self.messages:
            history_tokens = self._count_tokens(self.messages) - self.budget.system_tokens

        return TokenSnapshot(
            system=self.budget.system_tokens,
            history=history_tokens,
            history_budget=self.budget.history_budget,
            reserve=self.budget.response_reserve,
            message_count=len(self.messages),
            compression_count=self.compression_count,
        )


def _render_budget_display(console: Console, snapshot: TokenSnapshot) -> None:
    """コンテキスト予算の可視化を描画する。"""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Component", style="dim")
    table.add_column("Tokens", justify="right")
    table.add_column("Bar", min_width=30)

    # 履歴使用量のバー
    usage_ratio = snapshot.history / snapshot.history_budget if snapshot.history_budget > 0 else 0
    bar_width = 25
    filled = int(usage_ratio * bar_width)
    bar_color = "green" if usage_ratio < 0.7 else "yellow" if usage_ratio < 0.9 else "red"
    bar = f"[{bar_color}]{'█' * filled}[/{bar_color}][dim]{'░' * (bar_width - filled)}[/dim]"

    table.add_row("System", f"[cyan]{snapshot.system:,}[/cyan]", "[dim]fixed[/dim]")
    table.add_row(
        "History",
        f"[{bar_color}]{snapshot.history:,}[/{bar_color}] / {snapshot.history_budget:,}",
        bar,
    )
    table.add_row("Response Reserve", f"[cyan]{snapshot.reserve:,}[/cyan]", "[dim]max_tokens[/dim]")
    table.add_row("Messages", f"[cyan]{snapshot.message_count}[/cyan]", "")

    footer = f"Messages: {snapshot.message_count}"
    if snapshot.compression_count > 0:
        footer += f" │ Compressions: {snapshot.compression_count}"

    console.print(
        Panel(table, title="Context Budget", subtitle=footer, border_style="dim", padding=(0, 1))
    )


def main() -> None:
    """コンテキストエンジニアリングのデモ用メインオーケストレーション関数。"""
    console = Console()
    token_tracker = OpenRouterTokenTracker()
    manager = ContextManager(MODEL, MAX_CONTEXT_TOKENS, token_tracker)

    console.print(
        Panel(
            "[bold cyan]Context Engineering Demo[/bold cyan]\n\n"
            "このチャットは人為的に低いコンテキスト予算を使用しています "
            f"（合計{MAX_CONTEXT_TOKENS:,}トークン、"
            f"うち履歴用は約{manager.budget.history_budget:,}トークン）。\n"
            "数回のやり取りの後、自動圧縮が動作するのを確認できます——\n"
            "予算内に収めるため、古いメッセージが要約されます。\n\n"
            "トピックについて深く議論してみて、予算表示を観察してください。\n"
            "[bold]'quit'[/bold] または [bold]'exit'[/bold] と入力すると終了します。",
            title="Research Assistant",
        )
    )

    # 初期の予算を表示
    _render_budget_display(console, manager.get_token_snapshot())

    while True:
        console.print("\n[bold green]You:[/bold green] ", end="")
        user_input = input().strip()

        if user_input.lower() in ["quit", "exit", ""]:
            console.print("\n[yellow]Ending session...[/yellow]")
            break

        try:
            response = manager.chat(user_input)

            console.print("\n[bold blue]Assistant:[/bold blue]")
            console.print(Markdown(response))

            # 各ターンの後に予算を表示
            console.print()
            _render_budget_display(console, manager.get_token_snapshot())

        except Exception as e:
            logger.error("Error during chat: %s", e)
            console.print(f"\n[red]Error: {e}[/red]")
            break

    # 最終レポート
    console.print()
    token_tracker.report()
    console.print(
        f"\n[dim]Messages: {len(manager.messages)} │ "
        f"Compressions: {manager.compression_count}[/dim]"
    )


if __name__ == "__main__":
    main()
