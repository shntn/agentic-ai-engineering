"""
システム プロンプトとロール エンジニアリング (OpenRouter)

3 つの構成を比較することにより、システム プロンプトが LLM の動作を制御する方法を示します。
- 汎用アシスタント (ベースライン)
- 役割を割り当てられた専門家
- ロール制約の出力形式

3 つすべてが同じサポートチケットを選別しており、迅速なエンジニアリングの効果を示しています。
"""

import os
from openrouter import OpenRouter
from dotenv import find_dotenv, load_dotenv
from rich.console import Console
from rich.panel import Panel

from common import OpenRouterTokenTracker, interactive_menu, setup_logging

load_dotenv(find_dotenv())

logger = setup_logging(__name__)

# システムプロンプトに解釈を強制するあいまいなサポートチケット
SUPPORT_TICKETS = [
    {
        "label": "Ticket 1 — パフォーマンスに関する苦情",
        "text": (
            "件名: アップデート後、アプリが非常に遅くなりました\n\n"
            "こんにちは、最新のアップデート以来、アプリは何かをロードするのに永遠に時間がかかります。"
            "以前は即時だったページが 10 秒以上ハングアップします。Wi-Fi に接続しているのですが、"
            "他のものはすべて正常に動作します。これは本当にイライラします。仕事でこれが必要なのです。"
            "できるだけ早く直してもらえますか？"
        ),
    },
    {
        "label": "Ticket 2 — 機能が動作しない",
        "text": (
            "件名: エクスポート ボタンが機能しません\n\n"
            "レポートをエクスポートしようとしましたが、エクスポートボタンをクリックしても何も起こりません。"
            "何度も試しました。Windows で Chrome を使用しています。"
            "同僚は効果があると言っていますが、自分の何が間違っているのかわかりません。"
            "これは既知の問題ですか？"
        ),
    },
]

TICKET_LABELS = [t["label"] for t in SUPPORT_TICKETS]

# 段階的な改良を示す 3 つのシステム プロンプト構成
PROMPT_CONFIGS = [
    {
        "label": "A: 一般アシスタント",
        "system": "あなたは頼りになるアシスタントです。このサポートチケットの分析にご協力ください。",
    },
    {
        "label": "B: 役割を割り当てられたエキスパート",
        "system": (
            "あなたは SaaS 企業のシニア・サポートエンジニアです。これまで何千件ものチケットを"
            "優先順位付けしてしました。チケットを分析する際は、最も可能性の高い根本原因を特定し、"
            "重大度を推定した上で、次のステップを推奨します。曖昧な態度は取らず、経験に基づいて"
            "的確な判断を下します。"
        ),
    },
    {
        "label": "C: 役割 + 制約 + フォーマット",
        "system": (
            "あなたは SaaS 会社のシニア・サポートエンジニアです。何千ものチケットを優先順位"
            "付けしてきました。\n\n"
            "次のセクションに正確に回答してください:\n\n"
            "カテゴリ: バグ / ユーザー エラー / 機能リクエスト / 構成\n\n"
            "根本原因: 一文\n\n"
            "重大度: P1-P4\n\n"
            "次のアクション: サポートチームの具体的なステップ\n\n"
            "簡潔に。求められたこと以外の説明は不要です。"
        ),
    },
]

CONFIG_LABELS = [c["label"] for c in PROMPT_CONFIGS]


class PromptEngineer:
    """システムプロンプトがLLMの応答をどのように形成するかを示す"""

    def __init__(self, model: str, token_tracker: OpenRouterTokenTracker):
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.model = model
        self.token_tracker = token_tracker

    def run(self, system_prompt: str, user_prompt: str) -> str:
        """指定されたシステムおよびユーザープロンプトを使用して 1 つの LLM 呼び出しを実行"""
        logger.info("Calling model: %s", self.model)

        response = self.client.chat.send(  # type: ignore[call-overload]
            model=self.model,
            temperature=0.1,
            max_tokens=200,
            reasoning={"effort": "none"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )

        # print(json.dumps(response.model_dump(), indent=2, ensure_ascii=False))

        self.token_tracker.track(response.usage)
        logger.info(
            "Tokens - Input: %d, Output: %d",
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
        )

        return str(response.choices[0].message.content)


def main() -> None:
    """3 つの異なるシステムプロンプトを使用してサポートチケットの選別を実行"""
    console = Console()
    token_tracker = OpenRouterTokenTracker()
    engineer = PromptEngineer("deepseek/deepseek-v4-flash-0731", token_tracker)

    header = Panel(
        "[bold cyan]システムプロンプト & ロールエンジニアリング[/bold cyan]\n\n"
        "サポートチケットの選別における 3 つのシステムプロンプト設定の比較。\n"
        "より適切なプロンプト使うことで、応答のスタイルや実用性がどのように変化するかをご覧ください。",
        title="プロンプトエンジニアリング — OpenRouter",
    )

    try:
        while True:
            # Step 1: サポートチケットを選択
            selection = interactive_menu(
                console,
                TICKET_LABELS,
                title="サポートチケットを選択",
                header=header,
                allow_custom=True,
                custom_prompt="カスタム・サポートチケットを入力してください",
            )
            if not selection:
                break

            ticket = next((t for t in SUPPORT_TICKETS if t["label"] == selection), None)
            ticket_text = ticket["text"] if ticket else selection
            ticket_label = ticket["label"] if ticket else "Custom Ticket"
            user_prompt = f"Analyze this support ticket:\n\n{ticket_text}"

            # Step 2: チケットに対して実行するプロンプト構成を選択
            ticket_header = Panel(
                f"[bold magenta]{ticket_label}[/bold magenta]\n[dim]{ticket_text}[/dim]",
                title="選択したチケット",
                border_style="magenta",
            )

            while True:
                config_selection = interactive_menu(
                    console,
                    CONFIG_LABELS,
                    title="プロンプト構成を選択",
                    header=ticket_header,
                )
                if not config_selection:
                    break

                config = next(c for c in PROMPT_CONFIGS if c["label"] == config_selection)

                console.print(f"\n[bold yellow]━━━ {config['label']} ━━━[/bold yellow]")
                console.print(
                    Panel(config["system"], title="システムプロンプト", border_style="dim")
                )

                try:
                    response = engineer.run(config["system"], user_prompt)
                    console.print(Panel(response, title=config["label"], border_style="green"))
                except Exception as e:
                    logger.error("Error with config %s: %s", config["label"], e)

                token_tracker.report()
                token_tracker.reset()

                console.print("\n[dim]Press Enter to continue...[/dim]")
                input()

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")


if __name__ == "__main__":
    main()
