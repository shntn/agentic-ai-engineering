"""
フルエージェント — 「コンテンツライター」(OpenRouter)

このモジュールのすべてのパターンを、本番運用レベルのコンテンツ生成パイプラインに
組み合わせる:
- ルーティング (03): コンテンツタイプを分類 → タイプ別のプロンプト
- プロンプトチェイニング (02): リサーチ → タイプ別のトーンで執筆
- オーケストレーター・ワーカー (05): 動的なリサーチ計画 → 並列リサーチ
- 並列化 (04): ソーシャルメディアのファンアウト + SEOタイトル投票
- 評価者・最適化 (06): 品質ゲート付きの執筆・評価・修正ループ
- Human-in-the-Loop (07): 重要な意思決定における戦略的チェックポイント
"""

import asyncio
import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from common import OpenRouterTokenTracker, interactive_menu, setup_logging
from content_writer_openrouter import (
    ClassifyDoneEvent,
    ClassifyStartEvent,
    CompleteEvent,
    ContentWriterAgent,
    EvaluateDoneEvent,
    EvaluateStartEvent,
    EvaluationResult,
    HumanCheckpointEvent,
    PlanDoneEvent,
    PlanStartEvent,
    RefineStartEvent,
    ResearchDoneEvent,
    ResearchSectionDoneEvent,
    ResearchStartEvent,
    SeoCandidateEvent,
    SeoDoneEvent,
    SeoResult,
    SeoStartEvent,
    SocialContent,
    SocialDoneEvent,
    SocialStartEvent,
    SocialWriterDoneEvent,
    Source,
    WriteDoneEvent,
    WriteStartEvent,
    WritingResult,
)

load_dotenv(find_dotenv())
logger = setup_logging(__name__)

MODEL = "deepseek/deepseek-v4-flash"
RESEARCH_MODEL = "deepseek/deepseek-v4-flash"
OUTPUT_DIR = Path("output")
SCORE_THRESHOLD = 7.0
MAX_REFINEMENTS = 2

SUGGESTED_TOPICS = [
    "すべてのバックエンドチームがフィーチャーフラグを試すべき理由",
    "PythonとClickでCLIツールを作る方法",
    "ベクトルデータベースとは何か、なぜ重要なのか",
    "構造化並行性が非同期処理に対する考え方をどう変えたか",
]


# ─── ヘルパー関数 ─────────────────────────────────────────────────────────────


def _topic_dir(topic: str) -> Path:
    """トピックごとの出力ディレクトリを作成して返す。"""
    slug = topic.lower().replace(" ", "_")[:50]
    path = OUTPUT_DIR / slug
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save_artifact(topic: str, filename: str, content: str) -> Path:
    """単一の成果物ファイルをトピックディレクトリに保存する。"""
    path = _topic_dir(topic) / filename
    path.write_text(content, encoding="utf-8")
    logger.info("Saved: %s (%d chars)", path, len(content))
    return path


def _save_social(topic: str, social: SocialContent) -> list[Path]:
    """各ソーシャルメディアの成果物を個別のファイルとして保存する。"""
    paths: list[Path] = []
    for name, content in [
        ("linkedin.md", social.linkedin),
        ("twitter.md", social.twitter),
        ("newsletter.md", social.newsletter),
    ]:
        if content and not content.startswith("Error:"):
            paths.append(_save_artifact(topic, name, content))
    return paths


def _save_seo(topic: str, seo: SeoResult) -> Path:
    """SEO投票の結果を保存する。"""
    parts = [f"# SEO Title\n\n{seo.winning_title}\n\n"]
    if seo.candidates:
        parts.append("## Candidates\n\n")
        for i, c in enumerate(seo.candidates, 1):
            parts.append(f"{i}. {c}\n")
        parts.append(f"\n## Reasoning\n\n{seo.reasoning}\n")
    return _save_artifact(topic, "seo.md", "".join(parts))


def _display_evaluation(console: Console, evaluation: EvaluationResult, iteration: int) -> None:
    """5次元の評価スコアをRichのテーブルで表示する。"""
    dimensions = {
        "Clarity": evaluation.clarity,
        "Technical Accuracy": evaluation.technical_accuracy,
        "Structure": evaluation.structure,
        "Engagement": evaluation.engagement,
        "Human Voice": evaluation.human_voice,
    }

    table = Table(title=f"Iteration {iteration} — avg: {evaluation.avg_score:.1f}/10")
    table.add_column("Dimension", style="cyan")
    table.add_column("Score", justify="center")
    for dim, score in dimensions.items():
        color = "green" if score >= 8 else "yellow" if score >= 6 else "red"
        table.add_row(dim, f"[{color}]{score}/10[/{color}]")
    console.print(table)

    if evaluation.issues:
        console.print("[bold red]Issues:[/bold red]")
        for issue in evaluation.issues:
            console.print(f"  [red]•[/red] {issue}")

    if evaluation.suggestions:
        console.print("[bold yellow]Suggestions:[/bold yellow]")
        for suggestion in evaluation.suggestions:
            console.print(f"  [yellow]•[/yellow] {suggestion}")


def _print_path(console: Console, label: str, path: Path) -> None:
    """クリック可能なファイルリンクを表示する。"""
    console.print(f"  [dim]{label}: [link=file://{path.resolve()}]{path}[/link][/dim]")


def _show_sources(console: Console, sources: list[Source]) -> None:
    """Web検索のソースをパネル内のクリック可能なリンクとして表示する。"""
    if not sources:
        return
    lines = [f"  [dim]•[/dim] [link={s.url}]{s.title}[/link]" for s in sources]
    console.print(Panel("\n".join(lines), title="Sources", border_style="dim"))


def _human_checkpoint(console: Console, event: HumanCheckpointEvent) -> tuple[bool, str]:
    """戦略的な意思決定ポイントで人間のレビューのため一時停止する。"""
    console.print(
        Panel(event.content, title=f"Checkpoint: {event.title}", border_style="bright_magenta")
    )
    console.print(f"\n[bold magenta]{event.question}[/bold magenta]")
    console.print("[dim](y)es / (n)o with feedback[/dim]")
    console.print("[bold magenta]> [/bold magenta]", end="")

    response = input().strip().lower()
    if response in ["y", "yes", ""]:
        return True, ""

    console.print("[dim]Feedback:[/dim] ", end="")
    feedback = input().strip() if response == "n" else response
    return False, feedback


# ─── イベントコンシューマー ──────────────────────────────────────────────────────


async def _run_with_events(
    agent: ContentWriterAgent,
    topic: str,
    console: Console,
    tracker: OpenRouterTokenTracker,
) -> WritingResult | None:
    """エージェントから型付きイベントを受け取り、Richでレンダリングする。"""
    state: dict[str, Path | None] = {"last_draft_path": None}

    def on_checkpoint(event: HumanCheckpointEvent) -> tuple[bool, str]:
        draft_path = state["last_draft_path"]
        if draft_path and event.checkpoint_id == "final_review":
            _print_path(console, "Latest draft", draft_path)
        return _human_checkpoint(console, event)

    result: WritingResult | None = None

    async for event in agent.run_stream(
        topic,
        score_threshold=SCORE_THRESHOLD,
        max_refinements=MAX_REFINEMENTS,
        on_human_checkpoint=on_checkpoint,
    ):
        match event:
            # フェーズ1: 分類
            case ClassifyStartEvent():
                console.print("\n[bold yellow]Phase 1:[/bold yellow] コンテンツタイプを分類中...")

            case ClassifyDoneEvent(classification=c):
                console.print(f"  [green]✓[/green] {c.content_type.value}: {c.topic}")
                tracker.report()

            # フェーズ2: リサーチ計画
            case PlanStartEvent():
                console.print("\n[bold yellow]Phase 2:[/bold yellow] リサーチを計画中...")

            case PlanDoneEvent(subtopics=subs):
                for i, s in enumerate(subs, 1):
                    console.print(f"  {i}. [bold]{s.title}[/bold]")
                tracker.report()

            # フェーズ3: 並列リサーチ
            case ResearchStartEvent(count=n):
                console.print(
                    f"\n[bold yellow]Phase 3:[/bold yellow] {n}個のサブトピックを並行リサーチ中..."
                )

            case ResearchSectionDoneEvent(title=t, sources=srcs):
                console.print(f"  [green]✓[/green] {t}")
                _show_sources(console, srcs)

            case ResearchDoneEvent():
                tracker.report()

            # フェーズ4: 執筆
            case WriteStartEvent(iteration=i):
                label = "執筆中" if i == 1 else f"書き直し中（{i - 1}回目）"
                console.print(f"\n[bold yellow]Phase 4:[/bold yellow] {label}...")

            case WriteDoneEvent(iteration=i, content_length=length, content=draft, sources=srcs):
                path = _save_artifact(topic, f"draft_v{i}.md", draft)
                state["last_draft_path"] = path
                console.print(
                    f"  [green]✓[/green] v{i}: {length:,} chars — "
                    f"[dim][link=file://{path.resolve()}]{path}[/link][/dim]"
                )
                _show_sources(console, srcs)

            # フェーズ5: 評価 + 修正
            case EvaluateStartEvent(iteration=i):
                console.print(f"\n[bold yellow]Phase 5:[/bold yellow] 評価中（{i}回目）...")

            case EvaluateDoneEvent(iteration=i, evaluation=e):
                _display_evaluation(console, e, i)
                tracker.report()

                if e.avg_score >= SCORE_THRESHOLD:
                    console.print(
                        f"\n[green]Score {e.avg_score:.1f} >= {SCORE_THRESHOLD}"
                        f" — quality met![/green]"
                    )
                else:
                    console.print(f"\n[yellow]Score {e.avg_score:.1f} < {SCORE_THRESHOLD}[/yellow]")

            case RefineStartEvent(iteration=i):
                console.print(f"\n[yellow]修正中（{i - 1}/{MAX_REFINEMENTS}回目）...[/yellow]")

            # フェーズ6: ソーシャルメディア
            case SocialStartEvent():
                console.print(
                    "\n[bold yellow]Phase 6:[/bold yellow] ソーシャルメディア一斉配信（ファンアウト）..."
                )

            case SocialWriterDoneEvent(name=n):
                console.print(f"  [green]✓[/green] {n}")

            case SocialDoneEvent(social=s):
                tracker.report()
                paths = _save_social(topic, s)
                for p in paths:
                    _print_path(console, p.stem, p)
                for key, content in [
                    ("LINKEDIN", s.linkedin),
                    ("TWITTER", s.twitter),
                    ("NEWSLETTER", s.newsletter),
                ]:
                    if content and not content.startswith("Error:"):
                        console.print(Panel(Markdown(content), title=key, border_style="cyan"))

            # フェーズ7: SEOタイトル投票
            case SeoStartEvent():
                console.print("\n[bold yellow]Phase 7:[/bold yellow] SEOタイトル投票...")

            case SeoCandidateEvent(title=t):
                console.print(f"  [dim]• {t}[/dim]")

            case SeoDoneEvent(seo=s):
                console.print(f"  [green]✓[/green] Winner: {s.winning_title}")
                console.print(f"  [dim]{s.reasoning}[/dim]")
                tracker.report()
                path = _save_seo(topic, s)
                _print_path(console, "seo", path)

            # パイプライン完了
            case CompleteEvent(result=r):
                result = r

    return result


# ─── メイン ────────────────────────────────────────────────────────────────────


def main() -> None:
    """フルエージェントのコンテンツライターを実行する"""
    console = Console()
    token_tracker = OpenRouterTokenTracker()
    agent = ContentWriterAgent(MODEL, RESEARCH_MODEL, token_tracker)

    header = Panel(
        "[bold cyan]フルエージェント — コンテンツライター[/bold cyan]\n\n"
        "このモジュールのすべてのパターンを1つのパイプラインに組み合わせる:\n"
        "  [Classify] → [Plan] → [Research] → [Write] → [Evaluate] → [Refine]\n"
        "  → [Human Review] → [Social Media Blast] → [SEO Title Voting]\n\n"
        "パターン:\n"
        "  Routing (03) | Prompt Chaining (02) | Parallelization (04)\n"
        "  Orchestrator-Workers (05) | Evaluator-Optimizer (06) | Human-in-the-Loop (07)",
        title="Content Writer",
    )

    async def async_main() -> None:
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

            try:
                result = await _run_with_events(agent, topic, console, token_tracker)

                if result:
                    # 最終記事を保存
                    article_path = _save_artifact(topic, "article.md", result.content)
                    _print_path(console, "Final article", article_path)

                    # 最終記事を表示
                    console.print("\n[bold blue]Final Article:[/bold blue]")
                    console.print(Markdown(result.content))

                    # 出力ディレクトリを表示
                    topic_dir = _topic_dir(topic)
                    console.print(
                        f"\n[dim]All artifacts: "
                        f"[link=file://{topic_dir.resolve()}]{topic_dir}/[/link][/dim]"
                    )

                console.print("\n[dim]Press Enter to continue...[/dim]")
                input()
            except KeyboardInterrupt:
                raise
            except Exception as e:
                logger.error("Pipeline failed: %s", e)
                console.print(f"\n[red]Error: {e}[/red]")
            finally:
                token_tracker.report()
                token_tracker.reset()

    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        os._exit(130)


if __name__ == "__main__":
    main()
