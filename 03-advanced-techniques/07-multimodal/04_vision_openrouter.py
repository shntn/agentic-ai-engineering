"""
LLMによる画像解析 (OpenRouter)

画像をLLMに送信して視覚的に理解させる方法を実演します——マルチモーダルの
最も基本的なスキルです。URLベースの画像・ローカルファイルの解析・複数画像の
比較に対応しています。
"""

import base64
import mimetypes
import os
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv
from openrouter import OpenRouter
from openrouter.components import ChatResult
from openrouter.errors import OpenRouterError
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from common import OpenRouterTokenTracker, interactive_menu, setup_logging

load_dotenv(find_dotenv())

logger = setup_logging(__name__)

# 画像入力に対応したモデルを使用する必要がある。他レッスンのデフォルトである
# deepseek-v4-flash-0731は画像未対応（テキストのみ）のため、ここでは画像対応の
# モデルを使用する。
MODEL = "z-ai/glm-5.3-flash"

# 画像のダウンロード・処理には数十秒かかることがあり、SDKのデフォルトタイムアウト
# ではこれより短く、正常な応答でもタイムアウト→リトライのループに陥りやすい
# そのため、画像処理でも安全に完了できるよう、余裕を持った値を明示する。
REQUEST_TIMEOUT_MS = 120_000  # 120秒

# Wikimedia Commonsの公開サンプル画像
SAMPLE_IMAGES = {
    "Architecture — Colosseum": (
        "https://upload.wikimedia.org/wikipedia/commons/thumb/"
        "d/de/Colosseo_2020.jpg/1280px-Colosseo_2020.jpg"
    ),
    "Chart — World Population": (
        "https://upload.wikimedia.org/wikipedia/commons/thumb/"
        "b/b7/Population_curve.svg/1280px-Population_curve.svg.png"
    ),
    "Nature — Aurora Borealis": (
        "https://upload.wikimedia.org/wikipedia/commons/thumb/"
        "a/a9/Polarlicht.jpg/1280px-Polarlicht.jpg"
    ),
}

ANALYSIS_TYPES = {
    "Describe": "この画像を詳しく説明してください。何が写っていますか？",
    "OCR / Text Extraction": (
        "この画像に写っているテキストをすべて抽出してください。レイアウトもできる限り保持してください。"
    ),
    "Detailed Analysis": (
        "この画像について、構図・色使い・被写体・雰囲気・注目すべき詳細を含めた詳細な分析を"
        "行ってください。グラフやドキュメントであれば、そのデータや内容を説明してください。"
    ),
}


class VisionAnalyst:
    """LLMの画像理解機能を使って画像を解析する。"""

    def __init__(self, model: str, token_tracker: OpenRouterTokenTracker) -> None:
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.model = model
        self.token_tracker = token_tracker

    def analyze_url(self, image_url: str, prompt: str) -> str:
        """URLから画像を解析する。"""
        logger.info("Analyzing image URL: %s", image_url[:80])

        # URLソースの画像コンテンツブロック——LLMが直接画像を取得する
        response: ChatResult = self.client.chat.send(
            model=self.model,
            max_tokens=2048,
            timeout_ms=REQUEST_TIMEOUT_MS,
            messages=[  # type: ignore[arg-type]
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_url}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )

        assert response.usage is not None
        self.token_tracker.track(response.usage)
        logger.info(
            "Tokens — input: %d, output: %d",
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
        )
        return str(response.choices[0].message.content or "")

    def analyze_file(self, image_path: str, prompt: str) -> str:
        """base64エンコードでローカルの画像ファイルを解析する。"""
        logger.info("Analyzing local file: %s", image_path)

        image_data, media_type = self._encode_image(image_path)

        # base64ソースの画像コンテンツブロック——画像データをdata URIとしてインラインで送信する
        response: ChatResult = self.client.chat.send(
            model=self.model,
            max_tokens=2048,
            timeout_ms=REQUEST_TIMEOUT_MS,
            messages=[  # type: ignore[arg-type]
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{image_data}"},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )

        assert response.usage is not None
        self.token_tracker.track(response.usage)
        logger.info(
            "Tokens — input: %d, output: %d",
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
        )
        return str(response.choices[0].message.content or "")

    def compare_images(self, image_urls: list[str], prompt: str) -> str:
        """複数の画像を1回のリクエストで比較する。"""
        logger.info("Comparing %d images", len(image_urls))

        # コンテンツブロックを組み立てる: 画像ブロックとテキストラベルを交互に並べる
        content: list[dict[str, Any]] = []
        for i, url in enumerate(image_urls, 1):
            content.append({"type": "text", "text": f"Image {i}:"})
            content.append({"type": "image_url", "image_url": {"url": url}})

        content.append({"type": "text", "text": prompt})

        response: ChatResult = self.client.chat.send(
            model=self.model,
            max_tokens=2048,
            timeout_ms=REQUEST_TIMEOUT_MS,
            messages=[{"role": "user", "content": content}],  # type: ignore[arg-type]
        )

        assert response.usage is not None
        self.token_tracker.track(response.usage)
        logger.info(
            "Tokens — input: %d, output: %d",
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
        )
        return str(response.choices[0].message.content or "")

    def _encode_image(self, image_path: str) -> tuple[str, str]:
        """ローカルの画像ファイルを読み込み、(base64_data, media_type) を返す。"""
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        # 拡張子からMIMEタイプを判定する
        mime_type, _ = mimetypes.guess_type(str(path))
        if mime_type not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
            raise ValueError(f"Unsupported image type: {mime_type}. Use JPEG, PNG, GIF, or WebP.")

        data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
        logger.info("Encoded %s (%s, %d bytes)", path.name, mime_type, path.stat().st_size)
        return data, mime_type


def main() -> None:
    """対話型の画像解析デモ。"""
    console = Console()
    token_tracker = OpenRouterTokenTracker()
    analyst = VisionAnalyst(MODEL, token_tracker)

    welcome = Panel(
        "[bold cyan]LLMによる画像解析[/bold cyan]\n\n"
        "画像をLLMに送信して視覚的に理解させます:\n"
        "  [green]•[/green] URLからサンプル画像を解析\n"
        "  [green]•[/green] ローカルの画像ファイルを解析（base64）\n"
        "  [green]•[/green] 複数の画像を並べて比較\n\n"
        "[dim]画像は1568×1568pxあたり約1,600トークンを消費します[/dim]",
        title="Multimodal — Vision",
        border_style="blue",
    )

    image_menu_items = [
        *list(SAMPLE_IMAGES.keys()),
        "Compare All Samples",
        "Local File...",
    ]

    analysis_menu_items = list(ANALYSIS_TYPES.keys())

    try:
        while True:
            # ステップ1: 画像ソースを選択
            image_choice = interactive_menu(
                console,
                image_menu_items,
                title="Select Image",
                header=welcome,
                allow_custom=True,
                custom_label="Custom URL...",
                custom_prompt="Enter image URL",
            )

            if image_choice is None:
                break

            # ステップ2: 分析タイプを選択
            analysis_choice = interactive_menu(
                console,
                analysis_menu_items,
                title="Select Analysis Type",
                allow_custom=True,
                custom_label="Custom Prompt...",
                custom_prompt="Enter your analysis prompt",
            )

            if analysis_choice is None:
                continue

            prompt = ANALYSIS_TYPES.get(analysis_choice, analysis_choice)

            # ステップ3: 分析を実行
            console.clear()
            console.print("\n[yellow]Analyzing...[/yellow]\n")

            try:
                if image_choice == "Compare All Samples":
                    urls = list(SAMPLE_IMAGES.values())
                    result = analyst.compare_images(urls, prompt)
                elif image_choice == "Local File...":
                    console.print("[bold green]Enter file path:[/bold green] ", end="")
                    file_path = input().strip()
                    if not file_path:
                        continue
                    result = analyst.analyze_file(file_path, prompt)
                elif image_choice in SAMPLE_IMAGES:
                    url = SAMPLE_IMAGES[image_choice]
                    result = analyst.analyze_url(url, prompt)
                else:
                    # ユーザーが入力したカスタムURL
                    result = analyst.analyze_url(image_choice, prompt)

                # 結果を表示
                console.print(
                    Panel(
                        Markdown(result),
                        title=f"[bold blue]Analysis: {analysis_choice}[/bold blue]",
                        border_style="green",
                    )
                )

                # 今回の呼び出し分のトークンサマリー
                table = Table(show_header=False, box=None)
                table.add_column(style="dim")
                table.add_column(style="dim")
                table.add_row("Input tokens", f"{token_tracker.get_input_tokens():,}")
                table.add_row("Output tokens", f"{token_tracker.get_output_tokens():,}")
                table.add_row("Total tokens", f"{token_tracker.get_total_tokens():,}")
                console.print(table)

            except FileNotFoundError as e:
                console.print(f"\n[red]Error: {e}[/red]")
            except ValueError as e:
                console.print(f"\n[red]Error: {e}[/red]")
            except OpenRouterError as e:
                logger.error("API error: %s", e)
                console.print(f"\n[red]API Error: {e}[/red]")

            console.print("\n[dim]Press Enter to continue...[/dim]")
            input()

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")

    # 最終的なトークンレポート
    console.print()
    token_tracker.report()


if __name__ == "__main__":
    main()
