"""
Human-in-the-Loop — 「承認ゲート」(OpenRouter)

エージェントのワークフローを、戦略的なチェックポイントで一時停止して人間のレビューを
挟む様子を実演します。LLMがメールを下書きし、人間がフィードバック付きで承認・却下し、
LLMが修正する——人間の監督が最も価値を発揮する場所を示します。
"""

import os
from collections.abc import Callable
from typing import cast

from dotenv import find_dotenv, load_dotenv
from openrouter import OpenRouter
from rich.console import Console
from rich.panel import Panel

from common import OpenRouterTokenTracker, interactive_menu, setup_logging

load_dotenv(find_dotenv())
logger = setup_logging(__name__)

MODEL = "deepseek/deepseek-v4-flash"
MAX_REVISIONS = 2

SUGGESTED_SCENARIOS = [
    "内定を丁寧に辞退する — 感謝しつつも別の機会を選んだ",
    "今週末の残業をチームに依頼する — 重要な締め切りがあり、申し訳ない気持ちを伝える",
    "予算について話し合うためVPとの会議を依頼する — フォーマルでデータに基づいた内容",
    "未回答の提案書についてフォローアップする — 粘り強く、しかし丁重に",
]

# --- プロンプト ---

SYSTEM_PROMPT = (
    "あなたはプロのメールライターです。要求されたトーンに合った、明確で簡潔なメールを"
    "書いてください。メールのみを出力してください — 件名の後に本文を続けます。"
    "メタ的な説明は不要です。300語未満に収めてください。"
)

REVISE_SYSTEM_PROMPT = (
    "あなたはプロのメールライターです。提供されたフィードバックを基にメールを修正して"
    "ください。修正済みのメールのみを返してください — 件名の後に本文を続けます。"
    "変更点の説明は不要です。"
)

# チェックポイント関数の型: (title, content, question) -> (approved, feedback)
CheckpointFn = Callable[[str, str, str], tuple[bool, str]]


class EmailDrafter:
    """人間のチェックポイントを挟みながらメールを下書き・修正する。"""

    def __init__(self, model: str, token_tracker: OpenRouterTokenTracker) -> None:
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.model = model
        self.token_tracker = token_tracker

    def _call_llm(self, system: str, user_msg: str, *, max_tokens: int = 1024) -> str:
        """LLM呼び出しを行い、テキスト応答を返す"""
        logger.info("Calling %s", self.model)
        response = self.client.chat.send(  # type: ignore[call-overload]
            model=self.model,
            max_tokens=max_tokens,
            reasoning={"effort": "none"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
        )
        self.token_tracker.track(response.usage)
        return cast(str, response.choices[0].message.content)

    def _draft(self, scenario: str) -> str:
        """シナリオの説明から初回のメール下書きを生成する"""
        return self._call_llm(SYSTEM_PROMPT, f"次のシナリオのメールを書いてください: {scenario}")

    def _revise(self, draft: str, feedback: str) -> str:
        """人間からのフィードバックを基に下書きを修正する"""
        user_msg = f"元のメール:\n{draft}\n\n対応すべきフィードバック:\n{feedback}"
        return self._call_llm(REVISE_SYSTEM_PROMPT, user_msg)

    def run(self, scenario: str, *, checkpoint_fn: CheckpointFn | None = None) -> str:
        """人間によるレビューのチェックポイントを挟みながらメールを下書きする"""
        check = checkpoint_fn or (lambda _t, _c, _q: (True, ""))

        # ステップ1: 初回の下書きを生成
        logger.info("Generating draft for: %s", scenario)
        draft = self._draft(scenario)
        self.token_tracker.report()

        # === チェックポイント1: 下書きレビュー（見当違いな方向性を早期に検知する上で重要） ===
        approved, feedback = check(
            "Draft Review",
            draft,
            "このメールで良さそうですか？ 承認して確定するか、フィードバック付きで却下してください。",
        )

        if approved and not feedback:
            return draft
        # 編集モード: 人間が置き換え用のテキストを提供した
        if approved and feedback:
            return feedback

        # 却下された場合: 修正ループに入る
        for revision in range(1, MAX_REVISIONS + 1):
            logger.info("Revising draft (round %d/%d)", revision, MAX_REVISIONS)
            draft = self._revise(draft, feedback)
            self.token_tracker.report()

            # === チェックポイント2: 修正版レビュー ===
            approved, feedback = check(
                f"Revision Review ({revision}/{MAX_REVISIONS})",
                draft,
                "改善されましたか？ 承認して確定するか、さらにフィードバックを添えて却下してください。",
            )

            if approved and not feedback:
                return draft
            if approved and feedback:
                return feedback

        logger.info("Max revisions reached, returning last draft")
        return draft


def human_checkpoint(console: Console, title: str, content: str, question: str) -> tuple[bool, str]:
    """人間のレビューのため一時停止する。(approved, feedback) を返す"""
    console.print(Panel(content, title=f"Checkpoint: {title}", border_style="bright_magenta"))
    console.print(f"\n[bold magenta]{question}[/bold magenta]")
    console.print("[dim](y)はい / (n)いいえ + フィードバック / (e)編集して置き換え案を入力[/dim]")
    console.print("[bold magenta]> [/bold magenta]", end="")

    response = input().strip().lower()

    if response in ("y", "yes", ""):
        return True, ""
    elif response.startswith("e"):
        console.print("[dim]置き換え案を入力してください（Enterを2回で終了）:[/dim]")
        lines: list[str] = []
        empty = 0
        while empty < 1:
            line = input()
            if line.strip() == "":
                empty += 1
            else:
                empty = 0
                lines.append(line)
        return True, "\n".join(lines)
    else:
        console.print("[dim]フィードバックを入力してください:[/dim] ", end="")
        feedback = input().strip()
        return False, feedback


def main() -> None:
    """Human-in-the-Loopによるメール下書きデモを実行"""
    console = Console()
    token_tracker = OpenRouterTokenTracker()

    def checkpoint_fn(title: str, content: str, question: str) -> tuple[bool, str]:
        return human_checkpoint(console, title, content, question)

    header = Panel(
        "[bold cyan]Human-in-the-Loop — 承認ゲート[/bold cyan]\n\n"
        "LLMがメールを下書き → チェックポイントであなたがレビュー:\n"
        "1. 下書き後 — トーンと内容は適切か？\n"
        "2. 修正後 — フィードバックに対応できているか？\n\n"
        "選択肢: (y)はい 承認、(n)いいえ + フィードバック、(e)編集して置き換え\n"
        f"メールごとに最大 {MAX_REVISIONS} 回まで修正可能。",
        title="Human-in-the-Loop",
    )

    try:
        while True:
            scenario = interactive_menu(
                console,
                SUGGESTED_SCENARIOS,
                title="メールシナリオを選択",
                header=header,
                allow_custom=True,
                custom_prompt="メールのシナリオを説明してください",
            )
            if not scenario:
                break

            console.print(f"\n[bold green]Scenario:[/bold green] {scenario}")
            drafter = EmailDrafter(MODEL, token_tracker)

            try:
                result = drafter.run(scenario, checkpoint_fn=checkpoint_fn)

                console.print("\n[bold blue]Final Email:[/bold blue]")
                console.print(Panel(result, border_style="green"))

                console.print("\n[dim]Press Enter to continue...[/dim]")
                input()
            except Exception as e:
                logger.error("Email drafting failed: %s", e)
                console.print(f"\n[red]Error: {e}[/red]")
            finally:
                token_tracker.reset()

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")


if __name__ == "__main__":
    main()
