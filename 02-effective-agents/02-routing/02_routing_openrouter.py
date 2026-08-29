"""
ルーティング — 「コンテンツストラテジスト」(OpenRouter)

コンテンツの分類結果に基づいて専門ハンドラーへルーティングする様子を実演します。
LLM分類器がコンテンツタイプを判定し、それに応じて適切な専門チェーン
（Tutorial、News、Concept Explainer）へディスパッチします。
"""

import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from dotenv import find_dotenv, load_dotenv
from openrouter import OpenRouter
from openrouter.components import ChatResult
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from common import OpenRouterTokenTracker, interactive_menu, setup_logging
from openrouter_adapter import to_openrouter_tools

load_dotenv(find_dotenv())
logger = setup_logging(__name__)

OUTPUT_DIR = Path("output")
MODEL = "deepseek/deepseek-v4-flash-0731"
LIGHT_MODEL = "deepseek/deepseek-v4-flash-0731"

SUGGESTED_TOPICS = [
    "FastAPIアプリをAWS Lambdaにデプロイする方法",
    "Python 3.13でGILが削除された",
    "検索拡張生成（RAG）とは何か",
    "GitHub ActionsでCI/CDを構築する方法",
]

# --- 構造化出力のための分類スキーマ ---

CLASSIFY_TOOLS = [
    {
        "name": "classify_content",
        "description": "与えられたトピックのコンテンツタイプを分類する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "content_type": {
                    "type": "string",
                    "enum": ["tutorial", "news", "concept"],
                    "description": (
                        "tutorial: ハウツーガイド（例: 'Dockerのインストール方法'）。"
                        "news: 発表や変更（例: 'Dockerがライセンスを変更した'）。"
                        "concept: 概念の説明（例: 'コンテナ化とは何か'）。"
                    ),
                },
                "reasoning": {
                    "type": "string",
                    "description": "この分類を選んだ理由の簡潔な説明。",
                },
            },
            "required": ["content_type", "reasoning"],
        },
    }
]

# --- プロンプト ---

CLASSIFY_SYSTEM_PROMPT = "次のトピックを tutorial、news、concept のいずれかに分類してください。"

# ルートチェーンの各ステップ

# Tutorialルート
TUTORIAL_PREREQS_PROMPT = (
    "あなたはテクニカルライターです。このチュートリアルを始める前に必要な前提条件を"
    "列挙してください。バージョンやツールについて具体的に記載してください。箇条書きで"
    "出力してください。"
)
TUTORIAL_STEPS_PROMPT = (
    "あなたはテクニカルライターです。与えられた前提条件とトピックを基に、明確な"
    "ステップバイステップガイドを書いてください。各ステップに番号を付けてください。"
    "関連する箇所にはコード例を含めてください。"
)
TUTORIAL_TROUBLESHOOTING_PROMPT = (
    "あなたはテクニカルサポートライターです。与えられたチュートリアルに、よくある"
    "問題とその解決策を3〜5個含むトラブルシューティングセクションを追加してください。"
    "「### 問題」/「**解決策**」の形式でフォーマットしてください。"
)

# Newsルート
NEWS_SUMMARY_PROMPT = (
    "あなたはテクノロジージャーナリストです。主要な変更点やニュースを要約して"
    "ください。事実に基づき、簡潔にまとめてください。それぞれの変更点は箇条書きで"
    "示してください。"
)
NEWS_IMPACT_PROMPT = (
    "あなたはテクノロジーアナリストです。この変更点の要約を基に、開発者やチームへの"
    "影響を分析してください。誰が影響を受けるか、何が変わるか、移行時の考慮事項を"
    "カバーしてください。"
)
NEWS_CTA_PROMPT = (
    "あなたはテクノロジー編集者です。ニュースと影響分析を基に、読者が次に何を"
    "すべきかを伝える簡潔な行動喚起（CTA）セクションを書いてください。具体的で"
    "実行可能な内容にしてください。"
)

# Conceptルート
CONCEPT_ANALOGY_PROMPT = (
    "あなたはテクノロジー教育者です。与えられた概念を、明確で身近な例え話を使って"
    "説明してください。まず例え話から始め、そこから技術的な概念へとつなげてください。"
)
CONCEPT_ARCHITECTURE_PROMPT = (
    "あなたはソフトウェアアーキテクトです。与えられた概念の導入を基に、技術的な"
    "アーキテクチャを詳しく説明してください。コンポーネント同士がどう連携するか、"
    "一般的な実装例を含めてください。"
)
CONCEPT_PROS_CONS_PROMPT = (
    "あなたは実務的なエンジニアです。与えられた概念とアーキテクチャを基に、"
    "メリットとデメリットを列挙してください。トレードオフについて正直に述べて"
    "ください。2つの箇条書きリストの形式でフォーマットしてください。"
)

# コールバック型: ルーターが (event_name, event_data) を発行し、呼び出し側が表示方法を決める
RouterCallback = Callable[[str, dict[str, Any]], None]


class ContentRouter:
    """分類結果に基づいて、トピックを専門のコンテンツ生成チェーンにルーティングする。"""

    def __init__(self, model: str, light_model: str, token_tracker: OpenRouterTokenTracker):
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.model = model
        self.light_model = light_model
        self.token_tracker = token_tracker
        self._notify: RouterCallback = lambda _e, _d: None

    def _call_llm(
        self,
        system: str,
        messages: list[dict[str, Any]],
        *,
        use_light: bool = False,
        max_tokens: int = 4096,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> ChatResult:
        """単一のLLM呼び出しを行い、トークンを追跡"""
        model = self.light_model if use_light else self.model
        kwargs: dict[str, Any] = {}
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
        tool_names = [t.get("name", t.get("type", "unknown")) for t in tools or []]
        logger.info("Calling %s, tools=%s", model, tool_names)
        response: ChatResult = self.client.chat.send(  # type: ignore[call-overload]
            model=model,
            max_tokens=max_tokens,
            reasoning={"effort": "none"},
            messages=[{"role": "system", "content": system}, *messages],
            **kwargs,
        )
        self.token_tracker.track(response.usage)
        return response

    def _call_llm_text(self, system: str, user_message: str, *, use_light: bool = False) -> str:
        """LLMを呼び出し、テキストの内容を返す"""
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        response = self._call_llm(system, messages, use_light=use_light)
        return str(response.choices[0].message.content or "")

    def _classify(self, topic: str) -> dict[str, str]:
        """ツールベースの構造化出力でトピックを分類する（軽量モデル使用）。"""
        messages: list[dict[str, Any]] = [{"role": "user", "content": topic}]
        response = self._call_llm(
            CLASSIFY_SYSTEM_PROMPT,
            messages,
            use_light=True,
            max_tokens=256,
            tools=to_openrouter_tools(CLASSIFY_TOOLS),
            tool_choice={"type": "function", "function": {"name": "classify_content"}},
        )

        tool_calls = response.choices[0].message.tool_calls or []
        if tool_calls:
            return cast(dict[str, str], json.loads(tool_calls[0].function.arguments))

        raise ValueError("Classifier did not return a tool call")

    def _chain_tutorial(self, topic: str) -> str:
        """Tutorialチェーン: 前提条件 → ステップバイステップ → トラブルシューティング"""
        self._notify("step_start", {"name": "Prerequisites"})
        prerequisites = self._call_llm_text(
            TUTORIAL_PREREQS_PROMPT,
            f"次のために必要な前提条件は何ですか: {topic}",
        )
        self._notify("step_complete", {"name": "Prerequisites"})

        self._notify("step_start", {"name": "Steps"})
        steps = self._call_llm_text(
            TUTORIAL_STEPS_PROMPT,
            f"トピック: {topic}\n\n前提条件:\n{prerequisites}\n\nステップバイステップガイドを書いてください。",
            use_light=True,
        )
        self._notify("step_complete", {"name": "Steps"})

        self._notify("step_start", {"name": "Troubleshooting"})
        troubleshooting = self._call_llm_text(
            TUTORIAL_TROUBLESHOOTING_PROMPT,
            f"このチュートリアルにトラブルシューティングを追加してください:\n\n{steps}",
        )
        self._notify("step_complete", {"name": "Troubleshooting"})
        return (
            f"# {topic}\n\n"
            f"## 前提条件\n\n{prerequisites}\n\n"
            f"## 手順\n\n{steps}\n\n"
            f"## トラブルシューティング\n\n{troubleshooting}"
        )

    def _chain_news(self, topic: str) -> str:
        """Newsチェーン: 変更点の要約 → 影響分析 → 行動喚起"""
        self._notify("step_start", {"name": "Summary"})
        summary = self._call_llm_text(
            NEWS_SUMMARY_PROMPT,
            f"次の変更点を要約してください: {topic}",
        )
        self._notify("step_complete", {"name": "Summary"})

        # 中間ステップ: 構造化されたコンテキストからの単純な展開
        self._notify("step_start", {"name": "Impact"})
        impact = self._call_llm_text(
            NEWS_IMPACT_PROMPT,
            f"次の変更の影響を分析してください:\n\n{summary}",
            use_light=True,
        )
        self._notify("step_complete", {"name": "Impact"})

        self._notify("step_start", {"name": "Call to Action"})
        cta = self._call_llm_text(
            NEWS_CTA_PROMPT,
            f"ニュース: {summary}\n\n影響: {impact}\n\n行動喚起を書いてください。",
        )
        self._notify("step_complete", {"name": "Call to Action"})

        return (
            f"# {topic}\n\n"
            f"## 変更点\n\n{summary}\n\n"
            f"## 影響分析\n\n{impact}\n\n"
            f"## 次にすべきこと\n\n{cta}"
        )

    def _chain_concept(self, topic: str) -> str:
        """Conceptチェーン: 例え話 → アーキテクチャの説明 → メリット/デメリット"""
        self._notify("step_start", {"name": "Analogy"})
        analogy = self._call_llm_text(
            CONCEPT_ANALOGY_PROMPT,
            f"例え話を使って説明してください: {topic}",
        )
        self._notify("step_complete", {"name": "Analogy"})

        # 中間ステップ: 構造化されたコンテキストからの単純な展開
        self._notify("step_start", {"name": "Architecture"})
        architecture = self._call_llm_text(
            CONCEPT_ARCHITECTURE_PROMPT,
            f"概念の導入: {analogy}\n\n次のアーキテクチャを説明してください: {topic}",
            use_light=True,
        )
        self._notify("step_complete", {"name": "Architecture"})

        self._notify("step_start", {"name": "Pros/Cons"})
        pros_cons = self._call_llm_text(
            CONCEPT_PROS_CONS_PROMPT,
            f"アーキテクチャ: {architecture}\n\n次のメリットとデメリットを列挙してください: {topic}",
        )
        self._notify("step_complete", {"name": "Pros/Cons"})

        return (
            f"# {topic}\n\n"
            f"## 概念の理解\n\n{analogy}\n\n"
            f"## アーキテクチャ\n\n{architecture}\n\n"
            f"## メリットとデメリット\n\n{pros_cons}"
        )

    def run(self, topic: str, on_event: RouterCallback | None = None) -> str:
        """トピックを分類し、適切なチェーンにルーティングして結果を返す"""
        self._notify = on_event or (lambda _e, _d: None)

        # ステップ1: 分類
        self._notify("classify_start", {})
        classification = self._classify(topic)
        content_type = classification["content_type"]
        reasoning = classification["reasoning"]
        self.token_tracker.report()
        self._notify("classify_complete", {"content_type": content_type, "reasoning": reasoning})

        # ステップ2: 専門チェーンにルーティング
        routes: dict[str, Callable[[str], str]] = {
            "tutorial": self._chain_tutorial,
            "news": self._chain_news,
            "concept": self._chain_concept,
        }

        chain_fn = routes.get(content_type)
        if not chain_fn:
            raise ValueError(f"Unknown content type: {content_type}")

        self._notify("chain_start", {"content_type": content_type})
        result = chain_fn(topic)
        self.token_tracker.report()
        self._notify("chain_complete", {"content_type": content_type})

        return result


def main() -> None:
    """ルーティングのデモを実行"""
    console = Console()
    token_tracker = OpenRouterTokenTracker()

    def on_router_event(event: str, data: dict[str, Any]) -> None:
        """ルーターのイベントをコンソールに表示"""
        if event == "classify_start":
            console.print("\n[bold yellow]Step 1:[/bold yellow] トピックを分類中...")
        elif event == "classify_complete":
            console.print(
                Panel(
                    f"[bold]{data['content_type'].upper()}[/bold]\n{data['reasoning']}",
                    title="Classification",
                    border_style="cyan",
                )
            )
        elif event == "chain_start":
            console.print(
                f"\n[bold yellow]Step 2:[/bold yellow] {data['content_type']} チェーンを実行中..."
            )
        elif event == "step_start":
            console.print(f"  [cyan]{data['name']}...[/cyan]")
        elif event == "step_complete":
            console.print("  [green]✓[/green] Done")

    header = Panel(
        "[bold cyan]ルーティング — コンテンツストラテジスト[/bold cyan]\n\n"
        "トピック → [Classifier] → ルート [bold]A[/bold]、[bold]B[/bold]、"
        "[bold]C[/bold] → [専門チェーン] → 記事\n\n"
        "[bold]A.[/bold] チュートリアル（ハウツー）: 前提条件 → 手順 → トラブルシューティング\n"
        "[bold]B.[/bold] ニュース/発表: 変更点 → 影響 → 行動喚起\n"
        "[bold]C.[/bold] コンセプト解説: 例え話 → アーキテクチャ → メリット/デメリット",
        title="ルーティングデモ",
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
            router = ContentRouter(MODEL, LIGHT_MODEL, token_tracker)

            try:
                result = router.run(topic, on_event=on_router_event)

                # 記事をoutputディレクトリに保存
                OUTPUT_DIR.mkdir(exist_ok=True)
                # "/"等のパス区切り文字がファイル名に混入しないよう、単語構成文字以外は "_" に置換する
                slug = re.sub(r"[^\w\-]", "_", topic.lower())[:50]
                path = OUTPUT_DIR / f"{slug}.md"
                path.write_text(result, encoding="utf-8")

                console.print("\n[bold blue]Final Article:[/bold blue]")
                console.print(Markdown(result))
                abs_path = path.resolve()
                console.print(f"\n[dim]Saved to [link=file://{abs_path}]{path}[/link][/dim]")

                console.print("\n[dim]Press Enter to continue...[/dim]")
                input()
            except Exception as e:
                logger.error("Routing failed: %s", e)
                console.print(f"\n[red]Error: {e}[/red]")
            finally:
                token_tracker.reset()

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")


if __name__ == "__main__":
    main()
