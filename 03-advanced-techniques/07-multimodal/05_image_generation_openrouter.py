"""
画像生成 (OpenRouter)

OpenRouterの専用画像生成エンドポイント（`client.images.generate()`）を使った
画像生成を実演します。Geminiのようにチャット補完に`response_modalities`を
混ぜる方式ではなく、テキスト生成とは独立したエンドポイントとして呼び出します。
"""

import base64
import os
from datetime import datetime
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from openrouter import OpenRouter
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from common import OpenRouterTokenTracker, interactive_menu, setup_logging

load_dotenv(find_dotenv())

logger = setup_logging(__name__)

# OpenRouterの画像生成専用モデル
MODEL = "meta/muse-image"

# 画像生成には数十秒かかることがあり、SDKのデフォルトタイムアウトではこれより
# 短く、正常な応答でもタイムアウト→リトライのループに陥りやすい
# （CLAUDE.ja-openrouter.md「タイムアウト・リトライ対策」参照）。
REQUEST_TIMEOUT_MS = 120_000  # 120秒

SAMPLE_PROMPTS = {
    "Landscape": (
        "夜明けの静かな山湖、水面から立ち上る霧、松の木が鏡のような水面に映り込む、"
        "写真のようにリアルなスタイル"
    ),
    "Portrait": (
        "サックスを演奏するジャズミュージシャンの水彩画による肖像画、"
        "暖色系のトーン、表現力豊かな筆致、芸術的なスタイル"
    ),
    "Abstract": (
        "大胆な原色を使った抽象的な幾何学アート、重なり合う円と三角形、"
        "すっきりとした線、モダンでミニマルなスタイル"
    ),
    "Product": (
        "木製のテーブルに置かれた陶器のコーヒーマグのミニマルな商品写真、"
        "柔らかい自然光、浅い被写界深度、スタジオ品質"
    ),
    "Architecture": (
        "垂直庭園とガラスファサードを持つ未来的な建物、周囲に木々、青空、"
        "建築ビジュアライゼーションのスタイル"
    ),
}

OUTPUT_DIR = Path("output")


class ImageGenerator:
    """OpenRouterの画像生成エンドポイントを使って画像を生成する。"""

    def __init__(self, model: str, token_tracker: OpenRouterTokenTracker) -> None:
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.model = model
        self.token_tracker = token_tracker

    def generate(self, prompt: str) -> bytes | None:
        """テキストプロンプトから画像を生成する。

        画像バイト列を返す——生成に失敗した場合はNone。
        """
        logger.info("Generating image for prompt: %s", prompt[:60])

        response = self.client.images.generate(
            model=self.model,
            prompt=prompt,
            n=1,
            timeout_ms=REQUEST_TIMEOUT_MS,
        )

        # usageが利用可能であればトークン使用量を記録する
        if response.usage:
            self.token_tracker.track(response.usage)
            logger.info(
                "Tokens — input: %d, output: %d",
                response.usage.prompt_tokens,
                response.usage.completion_tokens,
            )

        if not response.data:
            return None

        image_bytes = base64.b64decode(response.data[0].b64_json)
        logger.info(
            "Received image: %s, %d bytes",
            response.data[0].media_type or "unknown",
            len(image_bytes),
        )
        return image_bytes

    def save_image(self, image_bytes: bytes, filename: str | None = None) -> str:
        """生成した画像をoutputディレクトリに保存する。"""
        OUTPUT_DIR.mkdir(exist_ok=True)

        if filename is None:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"generated_{timestamp}.webp"

        file_path = OUTPUT_DIR / filename
        file_path.write_bytes(image_bytes)
        logger.info("Saved image to %s (%d bytes)", file_path, len(image_bytes))
        return str(file_path)


def main() -> None:
    """対話型の画像生成デモ。"""
    console = Console()
    token_tracker = OpenRouterTokenTracker()
    generator = ImageGenerator(MODEL, token_tracker)

    welcome = Panel(
        "[bold cyan]画像生成 (OpenRouter)[/bold cyan]\n\n"
        "OpenRouterの専用画像生成エンドポイントを使ってテキストプロンプトから"
        "画像を生成します:\n"
        "  [green]•[/green] チャット補完とは独立した専用エンドポイント\n"
        "  [green]•[/green] client.images.generate() を使用\n"
        "  [green]•[/green] 画像はoutput/ディレクトリに保存\n\n"
        "[dim].envにOPENROUTER_API_KEYが必要です[/dim]",
        title="Multimodal — Image Generation",
        border_style="blue",
    )

    prompt_items = list(SAMPLE_PROMPTS.keys())

    try:
        while True:
            # プロンプトを選択または入力
            choice = interactive_menu(
                console,
                prompt_items,
                title="Select Prompt",
                header=welcome,
                allow_custom=True,
                custom_label="Custom Prompt...",
                custom_prompt="生成したい画像の説明を入力してください",
            )

            if choice is None:
                break

            prompt = SAMPLE_PROMPTS.get(choice, choice)

            console.clear()
            console.print(Panel(prompt, title="[bold]Prompt[/bold]", border_style="cyan"))
            console.print("\n[yellow]Generating image... (this may take a moment)[/yellow]\n")

            try:
                image_bytes = generator.generate(prompt)

                if image_bytes:
                    file_path = generator.save_image(image_bytes)
                    console.print(
                        Panel(
                            Markdown("画像を生成しました。"),
                            title="[bold blue]Result[/bold blue]",
                            border_style="green",
                        )
                    )
                    console.print(f"\n[bold green]Image saved:[/bold green] {file_path}")
                    console.print(f"[dim]Size: {len(image_bytes):,} bytes[/dim]")
                else:
                    console.print(
                        Panel(
                            Markdown("画像は生成されませんでした。"),
                            title="[bold blue]Result[/bold blue]",
                            border_style="yellow",
                        )
                    )
                    console.print("[yellow]No image was generated in the response.[/yellow]")

                # トークンサマリー
                table = Table(show_header=False, box=None)
                table.add_column(style="dim")
                table.add_column(style="dim")
                table.add_row("Input tokens", f"{token_tracker.get_input_tokens():,}")
                table.add_row("Output tokens", f"{token_tracker.get_output_tokens():,}")
                table.add_row("Total tokens", f"{token_tracker.get_total_tokens():,}")
                console.print(table)

            except Exception as e:
                logger.error("Generation error: %s", e)
                console.print(f"\n[red]Error: {e}[/red]")

            console.print("\n[dim]Press Enter to continue...[/dim]")
            input()

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")

    # 最終的なトークンレポート
    console.print()
    token_tracker.report()


if __name__ == "__main__":
    main()
