"""
並列化 — 「ソーシャルメディア一斉配信」(OpenRouter)

独立した作業をファンアウトし、結果をファンインして統合する様子を実演します。
ブログ記事を受け取り、ソーシャルメディア向けコンテンツを並列生成するほか、
SEOタイトル選定のための投票パターンも示します。
"""

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

load_dotenv(find_dotenv())
logger = setup_logging(__name__)

MODEL = "deepseek/deepseek-v4-flash"
INPUT_DIR = Path("input-ja")
OUTPUT_DIR = Path("output")

# --- プロンプト ---

LINKEDIN_SYSTEM_PROMPT = (
    "あなたはLinkedInコンテンツの専門家です。与えられたブログ記事を基に、LinkedInに"
    "適したプロフェッショナルな要約を書いてください。関連するハッシュタグを含めて"
    "ください。300語未満に収めてください。"
)

TWITTER_SYSTEM_PROMPT = (
    "あなたはTwitter/Xコンテンツの専門家です。与えられたブログ記事から、ちょうど5件の"
    "ツイートからなるスレッドを作成してください。各ツイートは280文字未満にしてください。"
    "1/5から5/5まで番号を振ってください。最初のツイートは興味を引く内容にしてください。"
)

NEWSLETTER_SYSTEM_PROMPT = (
    "あなたはメールマーケティングの専門家です。与えられたブログ記事から、"
    "1) 魅力的なメール件名、2) 読者がクリックしたくなるような2〜3文のプレビュー/"
    "導入段落、を書いてください。「Subject: ...」に続けて導入文を出力する形式に"
    "してください。"
)

SEO_TITLE_SYSTEM_PROMPT = (
    "あなたはSEOの専門家です。このブログ記事に対して、魅力的なSEOタイトルを"
    "ちょうど1つ生成してください。タイトルは50〜60文字で、関連キーワードを含み、"
    "クリックしたくなるものにしてください。タイトルのみを出力し、他は何も"
    "出力しないでください。"
)

SEO_EVALUATOR_SYSTEM_PROMPT = (
    "あなたはSEO評価者です。候補タイトルとブログ記事の要約を基に、最も良いタイトルを"
    "選んでください。キーワードとの関連性、クリックの魅力、長さ、明瞭さを考慮して"
    "ください。番号と勝者のタイトルのみを出力してください。"
)

# コールバック型: ジェネレーターが (event_name, event_data) を発行し、呼び出し側が表示方法を決める
GeneratorCallback = Callable[[str, dict[str, Any]], None]


class ParallelContentGenerator:
    """複数の独立したLLM呼び出しにコンテンツ生成をファンアウトする。"""

    def __init__(self, model: str, token_tracker: OpenRouterTokenTracker):
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.model = model
        self.token_tracker = token_tracker
        self._notify: GeneratorCallback = lambda _e, _d: None

    def _call_llm(self, system: str, user_message: str, temperature: float = 1.0) -> str:
        """単一のLLM呼び出しを行う"""
        logger.info("Calling %s (temp=%.1f)", self.model, temperature)
        response: ChatResult = self.client.chat.send(  # type: ignore[call-overload]
            model=self.model,
            max_tokens=2048,
            temperature=temperature,
            reasoning={"effort": "none", "summary": "null"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_message},
            ],
        )
        self.token_tracker.track(response.usage)
        return cast(str, response.choices[0].message.content)

    def _write_linkedin(self, blog_post: str) -> str:
        """LinkedIn向けのプロフェッショナルな要約を生成する"""
        return self._call_llm(
            LINKEDIN_SYSTEM_PROMPT,
            f"次のブログ記事からLinkedIn投稿を作成してください:\n\n{blog_post}",
        )

    def _write_twitter(self, blog_post: str) -> str:
        """5件のツイートからなるTwitter/Xスレッドを生成する"""
        return self._call_llm(
            TWITTER_SYSTEM_PROMPT,
            f"次のブログ記事からツイートスレッドを作成してください:\n\n{blog_post}",
        )

    def _write_newsletter(self, blog_post: str) -> str:
        """ニュースレターの件名と導入段落を生成する"""
        return self._call_llm(
            NEWSLETTER_SYSTEM_PROMPT,
            f"次のブログ記事からニュースレターの導入文を作成してください:\n\n{blog_post}",
        )

    def _generate_seo_title(self, blog_post: str, temperature: float) -> str:
        """与えられたtemperatureで単一のSEOタイトル候補を生成する"""
        return self._call_llm(
            SEO_TITLE_SYSTEM_PROMPT,
            f"次に対するSEOタイトルを生成してください:\n\n{blog_post[:500]}",
            temperature=temperature,
        )

    def _vote_best_title(self, titles: list[str], blog_post: str) -> str:
        """評価者を使って候補の中から最も良いSEOタイトルを選ぶ"""
        titles_text = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(titles))
        return self._call_llm(
            SEO_EVALUATOR_SYSTEM_PROMPT,
            f"ブログ要約: {blog_post[:300]}\n\n候補タイトル:\n{titles_text}\n\n"
            "最も良いSEOタイトルはどれで、その理由は？",
        )

    def run(self, blog_post: str, on_event: GeneratorCallback | None = None) -> dict[str, str]:
        """並列化パイプライン全体を実行する"""
        self._notify = on_event or (lambda _e, _d: None)
        results: dict[str, str] = {}

        # ファンアウト: すべてのライターを並行実行する
        self._notify("fanout_start", {})
        writers = {
            "linkedin": self._write_linkedin,
            "twitter": self._write_twitter,
            "newsletter": self._write_newsletter,
        }

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(fn, blog_post): name for name, fn in writers.items()}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    results[name] = future.result()
                    self._notify("writer_complete", {"name": name})
                except Exception as e:
                    logger.error("Writer %s failed: %s", name, e)
                    results[name] = f"Error: {e}"
        self.token_tracker.report()

        # 投票パターン: 異なるtemperatureで3件のSEOタイトルを生成する
        self._notify("voting_start", {})
        temperatures = [0.3, 0.7, 1.0]
        titles: list[str] = []

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures_list = [
                executor.submit(self._generate_seo_title, blog_post, temp) for temp in temperatures
            ]
            for future in as_completed(futures_list):
                try:
                    title = future.result().strip()
                    titles.append(title)
                    self._notify("title_candidate", {"title": title})
                except Exception as e:
                    logger.error("Title generation failed: %s", e)
        self.token_tracker.report()

        # 最も良いタイトルを評価して選ぶ
        if titles:
            self._notify("evaluating_start", {})
            results["seo_vote"] = self._vote_best_title(titles, blog_post)
            self.token_tracker.report()

        self._notify("pipeline_complete", {})
        return results


def _load_input_files() -> dict[str, Path]:
    """inputディレクトリからブログ記事を検出し、表示名をキーにする"""
    if not INPUT_DIR.exists():
        return {}
    posts: dict[str, Path] = {}
    for path in sorted(INPUT_DIR.glob("*.md")):
        label = f"{path.stem.replace('_', ' ').title()}  [grey50]({path})[/grey50]"
        posts[label] = path
    return posts


def _clean_label(label: str) -> str:
    """メニューラベルからRichのマークアップを取り除き、記事名のみを取得する"""
    return label.split("  [grey50]")[0]


def main() -> None:
    """並列化のデモを実行"""
    console = Console()
    token_tracker = OpenRouterTokenTracker()
    generator = ParallelContentGenerator(MODEL, token_tracker)

    def on_event(event: str, data: dict[str, Any]) -> None:
        """パイプラインの進捗をコンソールに表示"""
        if event == "fanout_start":
            console.print(
                "\n[bold yellow]Fan-out:[/bold yellow] ソーシャルコンテンツを並列生成中..."
            )
        elif event == "writer_complete":
            console.print(f"  [green]✓[/green] {data['name']} 完了")
        elif event == "voting_start":
            console.print("\n[bold yellow]Voting:[/bold yellow] SEOタイトル候補を生成中...")
        elif event == "title_candidate":
            console.print(f"  [dim]• {data['title']}[/dim]")
        elif event == "evaluating_start":
            console.print("\n[bold yellow]Evaluating:[/bold yellow] 最も良いSEOタイトルを選択中...")

    # input/ から作成済みのブログ記事を読み込む
    input_files = _load_input_files()
    labels = list(input_files.keys())

    header = Panel(
        "[bold cyan]並列化 — ソーシャルメディア一斉配信[/bold cyan]\n\n"
        "ブログ記事 → [LinkedIn Writer] + [Twitter Writer] + [Newsletter Writer]\n"
        "         → [Aggregator] → プロモーションパック\n\n"
        "さらに: 投票パターン — 異なるtemperatureで3つのSEOタイトルを生成 → "
        "評価者が最良のものを選ぶ",
        title="並列化",
    )

    try:
        while True:
            choice = interactive_menu(
                console,
                labels,
                title="ブログ記事を選択",
                header=header,
                allow_custom=True,
                custom_label="✏️  自分で入力する...",
                custom_prompt="短いブログトピックを入力してください（またはテキストを貼り付け）",
            )
            if not choice:
                break

            # ブログ記事の内容を解決する
            name = _clean_label(choice) if choice in input_files else choice
            if choice in input_files:
                blog_post = input_files[choice].read_text(encoding="utf-8")
                console.print(f"\n[bold green]Blog Post:[/bold green] {name}")
            elif len(choice) < 200:
                # 短いカスタム入力 — そのまま使用する（トピックまたは短い記事）
                blog_post = choice
                console.print(f"\n[bold green]Topic:[/bold green] {name}")
            else:
                blog_post = choice
                console.print(f"\n[bold green]Custom post:[/bold green] ({len(choice)} chars)")

            try:
                results = generator.run(blog_post, on_event=on_event)

                # プロモーションパックをoutputディレクトリに保存
                OUTPUT_DIR.mkdir(exist_ok=True)
                slug = name.lower().replace(" ", "_")[:50]
                path = OUTPUT_DIR / f"{slug}_promo.md"
                output_parts = [f"# プロモーションパック: {name}\n"]
                for key, value in results.items():
                    output_parts.append(f"## {key.upper()}\n\n{value}\n")
                path.write_text("\n".join(output_parts), encoding="utf-8")

                console.print("\n[bold blue]Promo Pack:[/bold blue]")
                for key, value in results.items():
                    console.print(Panel(Markdown(value), title=key.upper(), border_style="cyan"))

                abs_path = path.resolve()
                console.print(f"\n[dim]Saved to [link=file://{abs_path}]{path}[/link][/dim]")

                console.print("\n[dim]Press Enter to continue...[/dim]")
                input()
            except Exception as e:
                logger.error("Parallelization failed: %s", e)
                console.print(f"\n[red]Error: {e}[/red]")
            finally:
                token_tracker.report()
                token_tracker.reset()

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")


if __name__ == "__main__":
    main()
