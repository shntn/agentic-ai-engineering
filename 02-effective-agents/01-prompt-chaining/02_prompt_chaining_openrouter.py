"""
プロンプトチェイニング — 「技術ブログの組立ライン」(OpenRouter)

タスクを固定された一連のステップに分解し、各LLM呼び出しが前のステップの出力を
処理する様子を実演します。トピックが Outliner → Writer → Editor と流れていきます。
"""

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv
from openrouter import OpenRouter
from openrouter.components import ChatResult
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from common import OpenRouterTokenTracker, interactive_menu, setup_logging

load_dotenv(find_dotenv())
logger = setup_logging(__name__)

OUTPUT_DIR = Path("output")
MODEL = "deepseek/deepseek-v4-flash"
LIGHT_MODEL = "deepseek/deepseek-v4-flash"

# OpenRouterのweb_searchサーバーツール — サーバー側で実行され、モデルが検索するかどうかを判断する。
# max_uses で検索回数の上限を指定しないと、モデルが納得いくまで何度も検索を繰り返し、
# 実行時間・コストが跳ね上がることがある（詳細はCLAUDE.ja-openrouter.md参照）
WEB_SEARCH_TOOL = {
    "type": "openrouter:web_search",
    "parameters": {"max_uses": 4},
}

# OpenRouterのweb_searchサーバーツールは、検索の完了まで数十秒かかることがある。
# SDKのデフォルトタイムアウトはこれより短く、正常な応答でも ReadTimeout →
# 自動リトライ → 再度 ReadTimeout... のループに陥り、体感で10分以上かかることがある。
# そのため、Web検索を使うステップでも安全に完了できるよう、余裕を持った値を明示する。
REQUEST_TIMEOUT_MS = 120_000  # 120秒

SUGGESTED_TOPICS = [
    "Pythonにおける実践的な非同期プログラミング",
    "本番環境におけるAIエージェント",
    "ブラウザを超えたWebAssembly",
    "スタートアップのためのゼロトラストセキュリティ",
]


# --- プロンプト ---

OUTLINER_SYSTEM_PROMPT = (
    "あなたはリサーチプランナーです。与えられたトピックについて、そのテーマの異なる側面"
    "（市場動向、技術的な深さ、導入パターン、パフォーマンス分析など）をカバーする"
    "3〜5個の広範なリサーチ領域を特定してください。各領域は独立して調査可能である"
    "必要があります。1行目にトピックのタイトルを出力し、その後に箇条書きでリサーチ領域を"
    "出力してください。領域名は短く、抽象度の高いものにしてください。余計な説明は不要です。"
)
OUTLINER_USER_PROMPT = "次のトピックのブログのアウトラインを作成してください: {topic}"

WRITER_SYSTEM_PROMPT = (
    "あなたは技術ブログのライターです。与えられたアウトライン（タイトル + 箇条書き）を基に、"
    "簡潔なブログ記事を書いてください。タイトルはH1見出しとして、各箇条書き項目はH2セクション"
    "として使用してください。各セクションは1〜2段落の短い文章にし、無駄な内容や冗長な表現は"
    "避けてください。専門的でありながら親しみやすいトーンを使用してください。全体で1000語"
    "未満を目指してください。最新の正確な情報で文章を裏付けるため、常にWeb検索を使用してください。"
)
WRITER_USER_PROMPT = "次のアウトラインから完全なブログ記事を書いてください:\n\n{outline}"

EDITOR_SYSTEM_PROMPT = (
    "あなたはプロの編集者です。与えられたブログ記事を、文法・明瞭さ・流れの観点で磨き上げて"
    "ください。最後に「## まとめ」セクションを追加し、主な要点を3〜5個の箇条書きで要約して"
    "ください。編集済みの記事全体を返してください。"
)
EDITOR_USER_PROMPT = "次のブログ記事を編集し、磨き上げてください:\n\n{draft}"

# コールバック型: エージェントが (event_name, event_data) を発行し、呼び出し側が表示方法を決める
ChainCallback = Callable[[str, dict[str, Any]], None]


class PromptChain:
    """各ステップが次のステップに引き継がれる、LLM呼び出しの連続チェーン。"""

    def __init__(self, model: str, light_model: str, token_tracker: OpenRouterTokenTracker):
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.model = model
        self.light_model = light_model
        self.token_tracker = token_tracker
        self._notify: ChainCallback = lambda _e, _d: None

    def _call_llm(
        self,
        system: str,
        messages: list[dict[str, Any]],
        *,
        use_light: bool = False,
        max_tokens: int = 4096,
        tools: list[dict[str, Any]] | None = None,
        timeout_ms: int | None = None,                  # timeout_ms の設定を追加
    ) -> ChatResult:
        """単一のLLM呼び出しを行い、トークンを追跡"""
        model = self.light_model if use_light else self.model
        kwargs: dict[str, Any] = {}
        if tools:
            kwargs["tools"] = tools
        # timeout_ms の設定を追加
        if timeout_ms:
            kwargs["timeout_ms"] = timeout_ms
        tool_names = [t.get("name", t.get("type", "unknown")) for t in tools or []]
        logger.info("Calling %s, tools=%s", model, tool_names)

        response: ChatResult = self.client.chat.send(  # type: ignore[call-overload]
            model=model,
            max_tokens=max_tokens,
            reasoning={"effort": "none", "summary": "null"},
            messages=[{"role": "system", "content": system}, *messages],
            **kwargs,
        )
        self.token_tracker.track(response.usage)
        return response

    def _call_llm_text(self, system: str, user_message: str) -> str:
        """LLMを呼び出し、テキストの内容を返す"""
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        response = self._call_llm(system, messages)
        return str(response.choices[0].message.content or "")

    def _run_agentic_loop(
        self,
        system: str,
        user_message: str,
        *,
        use_light: bool = False,
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[str, list[dict[str, str]]]:
        """ツールを使ってLLMを実行する。OpenRouterのWeb検索はサーバー側で解決されるため、
        Anthropic版と異なり複数ターンにわたって継続する必要はない。"""
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        # OpenRouterのWeb検索はサーバー側で実行される組み込みツールのため、
        # Anthropic版のようなクライアント側でのtool_use/tool_resultの往復は不要
        # デフォルトのタイムアウト設定は短すぎるため、timeout_ms の設定を追加
        response = self._call_llm(system, messages, use_light=use_light, tools=tools, timeout_ms=REQUEST_TIMEOUT_MS)
        text = str(response.choices[0].message.content or "")
        searches: list[dict[str, str]] = []
        return text, searches

    def _step_outline(self, topic: str) -> str:
        """ステップ1: タイトルと箇条書きを持つ構造化されたアウトラインを生成する"""
        return self._call_llm_text(OUTLINER_SYSTEM_PROMPT, OUTLINER_USER_PROMPT.format(topic=topic))

    def _step_write(self, outline: str) -> tuple[str, list[dict[str, str]]]:
        """ステップ2: アウトラインを完全なブログ記事に展開する（Web検索を使う場合もある）"""
        return self._run_agentic_loop(
            WRITER_SYSTEM_PROMPT,
            WRITER_USER_PROMPT.format(outline=outline),
            use_light=True,
            tools=[WEB_SEARCH_TOOL],
        )

    def _step_edit(self, draft: str) -> str:
        """ステップ3: 下書きを磨き上げ、まとめセクションを追加する"""
        return self._call_llm_text(EDITOR_SYSTEM_PROMPT, EDITOR_USER_PROMPT.format(draft=draft))

    def run(self, topic: str, on_event: ChainCallback | None = None) -> str:
        """チェーン全体を実行する: アウトライン → 執筆 → 編集"""
        self._notify = on_event or (lambda _e, _d: None)

        # ステップ1: アウトライン
        self._notify("step_start", {"name": "Outline"})
        outline = self._step_outline(topic)
        if not outline.strip():
            raise ValueError("Outliner produced empty output — aborting chain.")
        self.token_tracker.report()
        self._notify("step_complete", {"name": "Outline", "result": outline})

        # ステップ2: 執筆
        self._notify("step_start", {"name": "Write"})
        logger.info("[Write] Calling %s", self.light_model)
        draft, searches = self._step_write(outline)
        self.token_tracker.report()
        self._notify("step_complete", {"name": "Write", "searches": searches})

        # ステップ3: 編集
        self._notify("step_start", {"name": "Edit"})
        final = self._step_edit(draft)
        self.token_tracker.report()
        self._notify("step_complete", {"name": "Edit"})

        self._notify("chain_complete", {})
        return final


def main() -> None:
    """プロンプトチェイニングのデモを実行"""
    console = Console()
    token_tracker = OpenRouterTokenTracker()

    def on_chain_event(event: str, data: dict[str, Any]) -> None:
        """ステップの進捗をコンソールに表示"""
        if event == "step_start":
            console.print(f"  [cyan]{data['name']}...[/cyan]")
        elif event == "step_complete":
            console.print("  [green]✓[/green] Done")
            if data["name"] == "Outline" and data.get("result"):
                console.print(Panel(data["result"], title="Outline", border_style="dim"))
            if data["name"] == "Write" and data.get("searches"):
                lines = [
                    f"  [dim]•[/dim] [link={s['url']}]{s['title']}[/link]" for s in data["searches"]
                ]
                console.print(Panel("\n".join(lines), title="Sources", border_style="dim"))

    header = Panel(
        "[bold cyan]プロンプトチェイニング — 技術ブログの組立ライン[/bold cyan]\n\n"
        "トピック → [Outliner] → [Writer] → [Editor] → 完成した記事\n\n"
        "各ステップは、その出力を次のステップに引き継ぎます。",
        title="プロンプトチェイニング",
    )

    try:
        while True:
            topic = interactive_menu(
                console,
                SUGGESTED_TOPICS,
                title="トピックを選択",
                header=header,
                allow_custom=True,
                custom_prompt="トピックを入力してください",
            )
            if not topic:
                break

            console.print(f"\n[bold green]Topic:[/bold green] {topic}")
            chain = PromptChain(MODEL, LIGHT_MODEL, token_tracker)

            try:
                result = chain.run(topic, on_event=on_chain_event)

                # 記事をoutputディレクトリに保存
                OUTPUT_DIR.mkdir(exist_ok=True)
                slug = topic.lower().replace(" ", "_")[:50]
                path = OUTPUT_DIR / f"{slug}.md"
                path.write_text(result, encoding="utf-8")

                console.print("\n[bold blue]Final Article:[/bold blue]")
                console.print(Markdown(result))
                abs_path = path.resolve()
                console.print(f"\n[dim]Saved to [link=file://{abs_path}]{path}[/link][/dim]")

                console.print("\n[dim]Press Enter to continue...[/dim]")
                input()
            except Exception as e:
                logger.error("Chain failed: %s", e)
                console.print(f"\n[red]Error: {e}[/red]")
            finally:
                token_tracker.reset()

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")


if __name__ == "__main__":
    main()
