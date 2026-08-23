"""
Few-Shot および Chain-of-Thought プロンプティング（OpenRouter）

3つのプロンプティング手法を、それぞれが真価を発揮するタスクを用いて紹介します：
1. Zero-shot — 感情分析（よく理解されているタスクで、例は必要ありません）
2. Few-Shot — カスタムドメインラベルを用いた分類（ユーザー独自の分類体系を学習させます）
3. Chain-of-Thought — 根本原因分析（多段階の推論が必要です）

各デモでは、なぜ他の手法ではなくその手法を選ぶべきなのかを解説しています。
"""

import os
import json
import time
from openrouter import OpenRouter
from dotenv import find_dotenv, load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from common import OpenRouterTokenTracker, interactive_menu, setup_logging

load_dotenv(find_dotenv())

logger = setup_logging(__name__)

# 無料モデルのレート制限を避けるため、リクエスト間隔をこの秒数以上空ける
MIN_REQUEST_INTERVAL_SECONDS = 3.0

# --- デモ A: Zero-Shot (感情分析) ---
# モデルがタスクを十分に理解している場合、Zero-shot 学習は非常に効果的です
REVIEWS = [
    "このノートパソコンは最高だ。高速で軽量、しかもバッテリーは一日中持つ。",
    "2週間で充電ケーブルが壊れた。まったくの無駄遣いだ。",
    "この価格なら悪くない。特別に優れた点はないが、仕事はちゃんとこなせる。",
]

# --- デモ B: Few-Shot (カスタムドメインラベル) ---
# Few-shot は、本来なら知らないはずの「あなた独自のカテゴリ」をモデルに学習させます
FEW_SHOT_EXAMPLES = [
    ("同じサブスクリプションで二重に請求された", "請求トラブル"),
    ("パスワードを3回リセットしてもログインできない", "アカウント認証"),
    ("レポートが1000行を超えると、エクスポート機能がクラッシュする", "バグ"),
    ("レポートの自動実行をスケジュール設定できれば素晴らしい", "機能要望"),
]

FEW_SHOT_TEST_INPUTS = [
    "請求書に先月の請求が記載されていますが、既に異議申し立て済みです",
    "ダッシュボードにずっと回転するアイコンが表示されたままで、グラフが読み込まれません",
    "チーム用にカスタムラベルでチケットにタグ付けできる機能が欲しいです",
]

# --- デモ C: Chain-of-Thought (根本原因分析) ---
# CoTは、タスクに多段階の推論が求められる場合に真価を発揮します。
BUG_REPORT = (
    "ユーザーからの報告によると、このアプリは午前中は正常に動作しますが、昼食後には"
    "極端に動作が遅くなります。この遅延は特定のセッションだけでなく、すべてのユーザー"
    "に同時に発生します。アプリサーバーを再起動すると一時的に解消しますが、数時間以内"
    "に再発します。サーバーのメモリ使用量は正常な範囲内にあるようです"
)


class PromptingClient:
    """zero-shot, few-shot, chain-of-thought プロンプティングを実演"""

    def __init__(self, model: str, token_tracker: OpenRouterTokenTracker):
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.model = model
        self.token_tracker = token_tracker
        self._last_request_time: float | None = None

    def _call(self, system_prompt: str, user_content: str, max_tokens: int = 256) -> str:
        """単一のAPI呼び出しを行い、トークンを追跡"""
        response = self.client.chat.send(
            model=self.model,
            temperature=0.0,
            max_tokens=max_tokens,
            reasoning={"effort": "none", "summary": "null"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content}
            ],
        )

        self.token_tracker.track(response.usage)
        return str(response.choices[0].message.content).strip()

    # --- Zero-Shot ---
    def classify_sentiment(self, review: str) -> str:
        """例示なしで感情を分類する — モデルはこのタスクをすでに理解しています。"""
        system = (
            "以下の製品レビューの感情を分類してください。\n"
            "単語一つだけで答えてください: ポジティブ, ネガティブ, ニュートラル"
        )
        return self._call(system, review)

    # --- Few-Shot ---
    def classify_ticket_few_shot(self, ticket: str) -> str:
        """例示なしではモデルが認識できないような、ドメイン固有のラベルを用いて分類を行います。"""
        examples = "\n".join(
            f'チケット: "{text}"\nカテゴリ: {label}' for text, label in FEW_SHOT_EXAMPLES
        )
        system = (
            "サポートチケットをこれらのカテゴリのいずれかに分類してください: "
            "請求トラブル, アカウント認証, バグ, 機能要望\n\n"
            f"例:\n\n{examples}\n\n"
            "カテゴリ名のみを回答してください。"
        )
        return self._call(system, f'Ticket: "{ticket}"\nCategory:')

    # --- Chain-of-Thought ---
    def analyze_zero_shot(self, bug_report: str) -> str:
        """推論のガイダンスなしでバグ報告を分析する — ベースライン"""
        system = (
            "あなたはシニアエンジニアです。このバグの最も可能性の高い根本原因を特定してください。\n"
            "簡潔に、1〜2文で述べてください。"
        )
        return self._call(system, bug_report)

    def analyze_cot(self, bug_report: str) -> str:
        """chain-of-thought を用いて分析し、段階を追って問題を論理的に検討してください。"""
        system = (
            "あなたはシニアエンジニアです。このバグ報告を段階的に分析してください:\n"
            "1. どのようなパターンが見られますか？（タイミング、範囲、トリガー）\n"
            "2. それぞれの手がかりから、何が可能性として残り、何が除外されますか？\n"
            "3. 最も可能性の高い根本原因は何ですか？\n"
            "4. それを確認するために、まず何を確認しますか？\n\n"
            "結論を出す前に、各ステップをよく検討してください。"
        )
        return self._call(system, bug_report, max_tokens=512)


DEMO_LABELS = [
    "A: Zero-Shot — 感情分類",
    "B: Few-Shot — カスタムラベル分類",
    "C: Chain-of-Thought — 根本原因分析",
]


ZERO_SHOT_SYSTEM = (
    "以下の製品レビューの感情を分類してください。"
    "ポジティブ、ネガティブ、ニュートラルのいずれか1語のみで回答してください。"
)

FEW_SHOT_SYSTEM_TEMPLATE = (
    "サポートチケットをこれらのカテゴリのいずれかに分類してください: "
    "請求トラブル, アカウント認証, バグ, 機能要望\n\n"
    "例:\n\n{examples}\n\n"
    "カテゴリ名のみを回答してください。"
)

COT_SYSTEM = (
    "あなたはシニアエンジニアです。このバグ報告を段階的に分析してください:\n"
    "1. どのようなパターンが見られますか？（タイミング、範囲、トリガー）\n"
    "2. それぞれの手がかりから、何が可能性として残り、何が除外されますか？\n"
    "3. 最も可能性の高い根本原因は何ですか？\n"
    "4. それを確認するために、まず何を確認しますか？\n\n"
    "結論を出す前に、各ステップをよく検討してください。"
)


def _run_zero_shot(console: Console, client: PromptingClient) -> None:
    """zero-shot 感情分析のデモを実行"""
    console.print("[dim]例は不要です — モデルはすでに感情を理解しています。[/dim]\n")
    console.print(Panel(ZERO_SHOT_SYSTEM, title="システムプロンプト", border_style="dim"))

    sentiment_table = Table(show_lines=True)
    sentiment_table.add_column("レビュー", style="cyan", max_width=55)
    sentiment_table.add_column("感情", style="green", max_width=12)

    for review in REVIEWS:
        try:
            result = client.classify_sentiment(review)
            sentiment_table.add_row(review, result)
        except Exception as e:
            logger.error("Sentiment error: %s", e)
            sentiment_table.add_row(review, "ERROR")

    console.print(sentiment_table)


def _run_few_shot(console: Console, client: PromptingClient) -> None:
    """Few-shot カスタムラベル分類のデモを実行"""
    console.print(
        "[dim]モデルは「請求トラブル」のようなラベルを認識していません — "
        "具体例を通じて、あなたの分類を教えることができます。[/dim]\n"
    )
    examples = "\n".join(
        f'チケット: "{text}"\nカテゴリ: {label}' for text, label in FEW_SHOT_EXAMPLES
    )
    system_prompt = FEW_SHOT_SYSTEM_TEMPLATE.format(examples=examples)
    console.print(Panel(system_prompt, title="システムプロンプト", border_style="dim"))

    ticket_table = Table(show_lines=True)
    ticket_table.add_column("サポートチケット", style="cyan", max_width=55)
    ticket_table.add_column("カテゴリ", style="green", max_width=18)

    for ticket in FEW_SHOT_TEST_INPUTS:
        try:
            result = client.classify_ticket_few_shot(ticket)
            ticket_table.add_row(ticket, result)
        except Exception as e:
            logger.error("Few-shot error: %s", e)
            ticket_table.add_row(ticket, "ERROR")

    console.print(ticket_table)


def _run_cot(console: Console, client: PromptingClient) -> None:
    """Chain-of-Thought を用いた根本原因分析のデモを実行"""
    console.print("[dim]多段階の推論を要するバグ報告に対する CoT[/dim]\n")
    console.print(Panel(COT_SYSTEM, title="システムプロンプト", border_style="dim"))
    console.print(Panel(BUG_REPORT, title="ユーザープロンプト", border_style="dim"))

    try:
        cot = client.analyze_cot(BUG_REPORT)
        console.print(Panel(cot, title="Chain-of-Thought 分析", border_style="green"))
    except Exception as e:
        logger.error("CoT analysis error: %s", e)


def main() -> None:
    """各プロンプト手法をいつ使用すべきかを示す、3つのデモを実行"""
    console = Console()
    token_tracker = OpenRouterTokenTracker()
    client = PromptingClient("deepseek/deepseek-v4-flash", token_tracker)

    header = Panel(
        "[bold cyan]Few-Shot & Chain-of-Thought プロンプト[/bold cyan]\n\n"
        "それぞれの手法が最も活きる場面で活用した3つのデモ:\n"
        "  A. Zero-shot — 感情分析（モデルがすでに知っているタスク）\n"
        "  B. Few-shot — カスタムラベル分類（独自の分類体系を学習させる）\n"
        "  C. Chain-of-thought — 根本原因分析（多段階の推論）",
        title="プロンプトエンジニアリング — OpenRouter",
    )

    demos = {
        DEMO_LABELS[0]: _run_zero_shot,
        DEMO_LABELS[1]: _run_few_shot,
        DEMO_LABELS[2]: _run_cot,
    }

    try:
        while True:
            selection = interactive_menu(
                console,
                DEMO_LABELS,
                title="デモを選択",
                header=header,
            )
            if not selection:
                break

            console.print(f"\n[bold yellow]━━━ {selection} ━━━[/bold yellow]")

            try:
                demos[selection](console, client)
            except Exception as e:
                logger.error("Demo error: %s", e)

            token_tracker.report()
            token_tracker.reset()

            console.print("\n[dim]Press Enter to continue...[/dim]")
            input()

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")


if __name__ == "__main__":
    main()
