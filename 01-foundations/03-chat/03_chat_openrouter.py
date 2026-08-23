"""
インタラクティブ チャット (OpenRouter)

シンプルなメッセージ履歴管理を備えたインタラクティブなチャットループを示します。
"""

import os

from dotenv import find_dotenv, load_dotenv
from openrouter import OpenRouter
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from common import OpenRouterTokenTracker, setup_logging

# ルートの .env ファイルから環境変数をロードする
load_dotenv(find_dotenv())

# ロギングの構成
logger = setup_logging(__name__)


class ChatSession:
    """
    会話履歴を保持し、メッセージ管理と API 呼び出しを含む
    すべてのチャットロジックをカプセル化するチャットエージェント。
    """

    def __init__(self, model: str, token_callback: OpenRouterTokenTracker):
        """
        チャットセッションを初期化
        """
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.token_callback = token_callback
        self.messages: list[dict[str, str]] = []
        self.model = model

    def send_message(self, user_message: str) -> str:
        """
        メッセージを送信し、応答を取得
        """
        # ユーザーメッセージを履歴に追加
        self.messages.append({"role": "user", "content": user_message})

        logger.info("Agent processing message (history length: %d)", len(self.messages))

        # メッセージ履歴全体を含めて API 呼び出しを行う
        response = self.client.chat.send(  # type: ignore[call-overload]
            model=self.model,
            temperature=0.1,
            max_tokens=2048,
            reasoning={"effort": "none", "summary": "null"},
            messages=self.messages,
        )

        # トークン使用量を追跡
        self.token_callback.track(response.usage)

        # アシスタントの応答を抽出
        assistant_message = str(response.choices[0].message.content)

        # アシスタントの応答を履歴に追加
        self.messages.append({"role": "assistant", "content": assistant_message})

        return assistant_message

    def get_message_count(self) -> int:
        """会話内のメッセージの総数を取得"""
        return len(self.messages)


def main() -> None:
    """
    ユーザーとのやり取りを処理し、チャットフローを調整するメインのオーケストレーション関数。
    """
    # 美しい出力のための Rich コンソール
    console = Console()
    # トークントラッカーとチャットセッションを作成
    token_tracker = OpenRouterTokenTracker()
    agent = ChatSession("deepseek/deepseek-v4-flash", token_tracker)

    # ウェルカムメッセージを表示
    console.print(
        Panel(
            "[bold cyan]OpenRouter Chat へようこそ！[/bold cyan]\n\n"
            "メッセージを入力して Enter を押してください。\n"
            "会話を終了するには 'quit' または 'exit' と入力してください。",
            title="チャットセッション",
        )
    )

    # インタラクティブなチャットループ
    while True:
        # ユーザー入力を取得
        console.print("\n[bold green]You:[/bold green] ", end="")
        user_input = input().strip()

        if user_input.lower() in ["quit", "exit", ""]:
            console.print("\n[yellow]チャットセッションを終了します...[/yellow]")
            break

        # エージェントを通してメッセージを処理
        try:
            response = agent.send_message(user_input)

            # 応答を表示
            console.print("\n[bold blue]Assistant:[/bold blue]")
            console.print(Markdown(response))

        except Exception as e:
            logger.error("Error during chat: %s", e)
            console.print(f"\n[red]Error: {e}[/red]")
            break

    # 最終的な統計情報を表示
    console.print()
    token_tracker.report()
    console.print(f"\n[dim]Total messages exchanged: {agent.get_message_count()}[/dim]")


if __name__ == "__main__":
    main()
