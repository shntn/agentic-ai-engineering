"""
オーケストレーター・ワーカー — 「深掘りリサーチャー」(OpenRouter)

中心となるLLMが動的にタスクを分解し、サブタスクをワーカーLLMに委任して、
その結果を最終的な記事として統合する様子を実演します。
"""

import json
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
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
MODEL = "deepseek/deepseek-v4-flash"
LIGHT_MODEL = "deepseek/deepseek-v4-flash"

# OpenRouterのweb_searchサーバーツール — サーバー側で実行され、モデルが検索するかどうかを判断する。
# max_uses で検索回数の上限を指定しないと、モデルが納得いくまで何度も検索を繰り返し、
# 実行時間・コストが跳ね上がることがある（詳細はCLAUDE.ja-openrouter.md参照）
WEB_SEARCH_TOOL = {
    "type": "openrouter:web_search",
    "parameters": {"max_uses": 1},
}

# OpenRouterのweb_searchサーバーツールは、検索の完了まで数十秒かかることがある。
# SDKのデフォルトタイムアウトはこれより短く、正常な応答でも ReadTimeout →
# 自動リトライ → 再度 ReadTimeout... のループに陥り、体感で10分以上かかることがある。
# そのため、Web検索を使うステップでも安全に完了できるよう、余裕を持った値を明示する。
REQUEST_TIMEOUT_MS = 120_000  # 120秒

SUGGESTED_TOPICS = [
    "バックエンド開発におけるBunとNode.jsの比較",
    "Python 3.13の新機能とパフォーマンス",
    "本番環境におけるWebAssembly: 現在地",
    "2025年のAIコードレビューツール事情",
]

# --- プロンプト ---

ORCHESTRATOR_SYSTEM_PROMPT = (
    "あなたはリサーチオーケストレーターです。与えられたトピックを、独立して調査可能な"
    "2〜4個の具体的なリサーチサブトピックに分解してください。各サブトピックは、"
    "トピックの異なる側面をカバーする必要があります。ジャーナリストが記事を書く前に、"
    "各角度を個別に調査するような考え方をしてください。"
)

WORKER_SYSTEM_PROMPT = (
    "あなたは徹底したテクニカルリサーチャーです。与えられたトピックを深く調査して"
    "ください。可能な限り、具体的な詳細・例・比較・データポイントを提供してください。"
    "3〜4段落の実質的な分析を書いてください。最新の情報が役立つトピックであれば"
    "Web検索を使用してください。"
)

SYNTHESIZER_SYSTEM_PROMPT = (
    "あなたはシニアテクニカルライターです。異なるサブトピックに関する複数のソースからの"
    "リサーチを基に、まとまりのある構造化された記事として統合してください。"
)

# オーケストレーターがタスクを分解するためのツール
PLANNING_TOOLS = [
    {
        "name": "create_research_plan",
        "description": "トピックをワーカー向けの具体的なリサーチサブトピックに分解する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "subtopics": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {
                                "type": "string",
                                "description": "サブトピックのタイトル",
                            },
                            "research_prompt": {
                                "type": "string",
                                "description": "ワーカー向けの具体的なリサーチ質問",
                            },
                        },
                        "required": ["title", "research_prompt"],
                    },
                    "description": "並行して調査するサブトピックのリスト",
                },
                "synthesis_instructions": {
                    "type": "string",
                    "description": "リサーチ結果を最終的な記事にまとめる際の指示",
                },
            },
            "required": ["subtopics", "synthesis_instructions"],
        },
    }
]

# コールバック型: エージェントが (event_name, event_data) を発行し、呼び出し側が表示方法を決める
OrchestratorCallback = Callable[[str, dict[str, Any]], None]


class OrchestratorWorkers:
    """オーケストレーターが動的にタスクを分解し、ワーカーが並行実行する。"""

    def __init__(self, model: str, light_model: str, token_tracker: OpenRouterTokenTracker):
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.model = model
        self.light_model = light_model
        self.token_tracker = token_tracker
        self._notify: OrchestratorCallback = lambda _e, _d: None

    def _call_llm(
        self,
        system: str,
        messages: list[dict[str, Any]],
        *,
        use_light: bool = False,
        max_tokens: int = 4096,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        timeout_ms: int | None = None,
    ) -> ChatResult:
        """単一のLLM呼び出しを行い、トークンを追跡"""
        model = self.light_model if use_light else self.model
        kwargs: dict[str, Any] = {}
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
        # デフォルトのタイムアウト設定は短すぎるため、timeout_ms の設定を追加
        if timeout_ms:
            kwargs["timeout_ms"] = timeout_ms
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

    def _call_llm_text(self, system: str, user_message: str, **kwargs: Any) -> str:
        """LLMを呼び出し、テキストの内容を返す"""
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        response = self._call_llm(system, messages, **kwargs)
        return str(response.choices[0].message.content or "")

    def _plan(self, topic: str) -> dict[str, Any]:
        """オーケストレーター: トピックを動的にサブトピックへ分解する"""
        logger.info("Orchestrator planning: %s", topic)
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": f"次のトピックのリサーチ計画を立ててください: {topic}"}
        ]
        response = self._call_llm(
            ORCHESTRATOR_SYSTEM_PROMPT,
            messages,
            max_tokens=1024,
            tools=to_openrouter_tools(PLANNING_TOOLS),
            tool_choice={"type": "function", "function": {"name": "create_research_plan"}},
        )

        tool_calls = response.choices[0].message.tool_calls or []
        if tool_calls:
            return cast(dict[str, Any], json.loads(tool_calls[0].function.arguments))

        raise ValueError("Orchestrator did not produce a research plan")

    def _research_subtopic(self, subtopic: dict[str, str]) -> dict[str, str]:
        """ワーカー: 単一のサブトピックを深く調査する（必要に応じてWeb検索を使用）"""
        title = subtopic["title"]
        logger.info("Worker researching: %s", title)

        messages: list[dict[str, Any]] = [{"role": "user", "content": subtopic["research_prompt"]}]
        response = self._call_llm(
            WORKER_SYSTEM_PROMPT,
            messages,
            use_light=True,
            tools=[WEB_SEARCH_TOOL],
            timeout_ms=REQUEST_TIMEOUT_MS,
        )
        content = str(response.choices[0].message.content or "")
        return {"title": title, "content": content}

    def _synthesize(self, topic: str, research: list[dict[str, str]], instructions: str) -> str:
        """シンセサイザー: すべてのワーカーのリサーチを、まとまりのある最終記事に統合する"""
        logger.info("Synthesizing %d research sections", len(research))
        sections = "\n\n---\n\n".join(f"## {r['title']}\n\n{r['content']}" for r in research)
        system = f"{SYNTHESIZER_SYSTEM_PROMPT} 統合の指示: {instructions}"
        user_msg = (
            f"# {topic}\n\nリサーチセクション:\n\n{sections}\n\n"
            "完全でまとまりのある記事に統合してください。"
        )
        return self._call_llm_text(system, user_msg)

    def run(self, topic: str, on_event: OrchestratorCallback | None = None) -> str:
        """オーケストレーター・ワーカーパイプライン全体を実行する"""
        self._notify = on_event or (lambda _e, _d: None)

        # ステップ1: オーケストレーターが計画する
        self._notify("plan_start", {})
        plan = self._plan(topic)
        subtopics = plan["subtopics"]
        instructions = plan["synthesis_instructions"]
        self.token_tracker.report()
        self._notify("plan_complete", {"subtopics": subtopics})

        # ステップ2: ワーカーが並行してリサーチする
        self._notify("workers_start", {"count": len(subtopics)})
        research_results: list[dict[str, str]] = []

        with ThreadPoolExecutor(max_workers=len(subtopics)) as executor:
            futures = {
                executor.submit(self._research_subtopic, sub): sub["title"] for sub in subtopics
            }
            for future in as_completed(futures):
                title = futures[future]
                try:
                    result = future.result()
                    research_results.append(result)
                    self._notify("worker_complete", {"title": title})
                except Exception as e:
                    logger.error("Worker failed on %s: %s", title, e)

        self.token_tracker.report()

        # ステップ3: 統合する
        self._notify("synthesize_start", {})
        final = self._synthesize(topic, research_results, instructions)
        self.token_tracker.report()
        self._notify("synthesize_complete", {})

        return final


def main() -> None:
    """オーケストレーター・ワーカーのデモを実行"""
    console = Console()
    token_tracker = OpenRouterTokenTracker()

    def on_event(event: str, data: dict[str, Any]) -> None:
        """パイプラインのイベントをコンソール表示用に処理する"""
        if event == "plan_start":
            console.print("\n[bold yellow]Orchestrator:[/bold yellow] リサーチを計画中...")
        elif event == "plan_complete":
            subtopics = data["subtopics"]
            console.print(
                Panel(
                    "\n".join(f"• {s['title']}" for s in subtopics),
                    title=f"Research Plan ({len(subtopics)} subtopics)",
                    border_style="cyan",
                )
            )
        elif event == "workers_start":
            console.print(
                f"\n[bold yellow]Workers:[/bold yellow] "
                f"{data['count']}個のサブトピックを並行リサーチ中..."
            )
        elif event == "worker_complete":
            console.print(f"  [green]✓[/green] {data['title']}")
        elif event == "synthesize_start":
            console.print("\n[bold yellow]Synthesizer:[/bold yellow] リサーチ結果を統合中...")
        elif event == "synthesize_complete":
            console.print("  [green]✓[/green] Done")

    header = Panel(
        "[bold cyan]オーケストレーター・ワーカー — 深掘りリサーチャー[/bold cyan]\n\n"
        "トピック → [Orchestrator] → 動的なサブトピック一覧\n"
        "         → [Worker 1] + [Worker 2] + [Worker N]（並行実行）\n"
        "         → [Synthesizer] → 完成した記事\n\n"
        "何を調査するかはLLMが決めます — あなたが定義するのはタスクではなく"
        "ワーカーの能力です。",
        title="Orchestrator-Workers",
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
            orch = OrchestratorWorkers(MODEL, LIGHT_MODEL, token_tracker)

            try:
                result = orch.run(topic, on_event=on_event)

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
                logger.error("Orchestration failed: %s", e)
                console.print(f"\n[red]Error: {e}[/red]")
            finally:
                token_tracker.reset()

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")


if __name__ == "__main__":
    main()
