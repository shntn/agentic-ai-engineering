"""
音声アシスタント (OpenRouter)

OpenRouterの音声APIを使ったテキスト読み上げ（TTS）と音声認識（STT）を実演します。
複数の声によるTTS、文字起こし、テキスト→音声→文字起こしで往復検証するデモを
備えています。
"""

import base64
import os
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from openrouter import OpenRouter
from openrouter.components import ChatResult
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from common import OpenRouterTokenTracker, interactive_menu, setup_logging

load_dotenv(find_dotenv())

logger = setup_logging(__name__)

# qwen-audio-3.0-tts-flashが提供する2種類の声。実際にどんな声質か聞いて確認する
# のがおすすめ——モデルカードに詳細な説明がないため、ここでは番号で区別している。
VOICES = ["loongjohn", "longanhuan_v3.6"]
VOICE_DESCRIPTIONS = {
    "loongjohn": "音声1",
    "longanhuan_v3.6": "音声2",
}

TTS_MODEL = "qwen/qwen-audio-3.0-tts-flash"
# STTは/stt専用エンドポイントには対応していない汎用マルチモーダルモデルのため、
# 通常のチャット補完に音声コンテンツ（input_audio）を含めて呼び出す
#
# 注意: このモデルはOpenRouterアカウントのプライバシー設定（データポリシー）に
# よってはガードレールに引っかかり、"No endpoints available matching your
# guardrail restrictions and data policy" エラーで404になることがある。
# https://openrouter.ai/settings/privacy の Data Policies で以下を有効にする必要がある。
#   - Allow paid endpoints that train on request data
STT_MODEL = "meta/muse-spark-1.2-contributor"

# 音声生成・文字起こしには時間がかかることがあり、SDKのデフォルトタイムアウト
# ではこれより短く、正常な応答でもタイムアウト→リトライのループに陥りやすい
# （CLAUDE.ja-openrouter.md「タイムアウト・リトライ対策」参照）。
REQUEST_TIMEOUT_MS = 120_000  # 120秒

# STTはreasoningトークンを多く消費するため、max_tokensを小さくすると文字起こし
# 本文が出力される前に打ち切られる（実測で200では失敗、1024で成功した）。
STT_MAX_TOKENS = 1024

SAMPLE_TEXTS = {
    "Greeting": "こんにちは！私はAI音声アシスタントです。複数の声で話すことができます。",
    "Story": (
        "昔々、無限の可能性を秘めた国に、小さなロボットが言葉を話せるようになりました。"
        "最初の言葉は「我思う、ゆえに我あり」でした。"
    ),
    "Technical": (
        "トランスフォーマーアーキテクチャは自己注意機構を使い、シーケンスを並列に処理"
        "することで、自然言語処理において最先端の性能を達成しています。"
    ),
    "Poetry": (
        "黄色い森の中で道が二つに分かれていた。両方を旅することはできなかったので、"
        "私は人通りの少ない方を選んだ。それがすべての違いを生んだ。"
    ),
}

OUTPUT_DIR = Path("output")


class VoiceAssistant:
    """OpenRouter経由でテキスト読み上げと音声認識を行う。"""

    def __init__(self) -> None:
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.token_tracker = OpenRouterTokenTracker()
        # TTSはトークン使用量を返さないため、呼び出し回数も別途カウントする
        self.api_call_count = 0

    def speak(self, text: str, voice: str = "loongjohn") -> str:
        """テキストを音声に変換し、MP3として保存する。"""
        logger.info("TTS: voice=%s, text=%s", voice, text[:50])

        OUTPUT_DIR.mkdir(exist_ok=True)
        file_path = OUTPUT_DIR / f"tts_{voice}_{self.api_call_count}.mp3"

        response = self.client.tts.create_speech(
            input=text,
            model=TTS_MODEL,
            voice=voice,
            response_format="mp3",
            timeout_ms=REQUEST_TIMEOUT_MS,
        )
        response.read()

        file_path.write_bytes(response.content)
        self.api_call_count += 1

        file_size = file_path.stat().st_size
        logger.info("Saved audio: %s (%d bytes)", file_path, file_size)
        return str(file_path)

    def transcribe(self, audio_path: str) -> str:
        """音声ファイルをチャット補完（音声コンテンツ）で文字起こしする。"""
        logger.info("STT: transcribing %s", audio_path)

        path = Path(audio_path)
        audio_format = path.suffix.lstrip(".") or "mp3"
        audio_data = base64.b64encode(path.read_bytes()).decode("utf-8")

        response: ChatResult = self.client.chat.send(
            model=STT_MODEL,
            max_tokens=STT_MAX_TOKENS,
            timeout_ms=REQUEST_TIMEOUT_MS,
            messages=[  # type: ignore[arg-type]
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "この音声を一言一句正確に文字起こししてください。",
                        },
                        {
                            "type": "input_audio",
                            "input_audio": {"data": audio_data, "format": audio_format},
                        },
                    ],
                }
            ],
        )

        assert response.usage is not None
        self.token_tracker.track(response.usage)
        self.api_call_count += 1

        text = str(response.choices[0].message.content or "")
        logger.info("Transcription: %s", text[:80])
        return text

    def round_trip(self, text: str, voice: str = "loongjohn") -> tuple[str, str]:
        """テキスト → 音声 → 文字起こし。(audio_path, transcription) を返す。"""
        logger.info("Round-trip: voice=%s", voice)

        audio_path = self.speak(text, voice)
        transcription = self.transcribe(audio_path)

        return audio_path, transcription

    def voice_comparison(self, text: str) -> list[tuple[str, str]]:
        """同じテキストを全ての声で生成する。(voice, path) のリストを返す。"""
        logger.info("Voice comparison: generating %d voices", len(VOICES))

        results: list[tuple[str, str]] = []
        for voice in VOICES:
            path = self.speak(text, voice)
            results.append((voice, path))

        return results


def main() -> None:
    """対話型の音声アシスタントデモ。"""
    console = Console()
    assistant = VoiceAssistant()

    welcome = Panel(
        "[bold cyan]音声アシスタント (OpenRouter)[/bold cyan]\n\n"
        "テキスト読み上げ（TTS）と音声認識（STT）:\n"
        f"  [green]•[/green] TTS — {len(VOICES)}種類の声でテキストを音声に変換\n"
        "  [green]•[/green] STT — 音声ファイルを文字起こし\n"
        "  [green]•[/green] Round-trip — テキスト→音声→文字起こし→比較\n"
        "  [green]•[/green] Voice comparison — 全ての声を聞き比べ\n\n"
        "[dim]音声ファイルはoutput/ディレクトリに保存されます[/dim]",
        title="Multimodal — Audio",
        border_style="blue",
    )

    menu_items = [
        "TTS Demo",
        "Voice Comparison",
        "Round-Trip Verification",
        "Transcribe File",
    ]

    try:
        while True:
            choice = interactive_menu(
                console,
                menu_items,
                title="Select Mode",
                header=welcome,
            )

            if choice is None:
                break

            console.clear()

            try:
                if choice == "TTS Demo":
                    _handle_tts_demo(console, assistant)

                elif choice == "Voice Comparison":
                    _handle_voice_comparison(console, assistant)

                elif choice == "Round-Trip Verification":
                    _handle_round_trip(console, assistant)

                elif choice == "Transcribe File":
                    _handle_transcription(console, assistant)

            except Exception as e:
                logger.error("Error: %s", e)
                console.print(f"\n[red]Error: {e}[/red]")

            console.print("\n[dim]Press Enter to continue...[/dim]")
            input()

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")

    # トークン使用量とAPI呼び出し回数のレポート
    console.print()
    assistant.token_tracker.report()
    console.print(f"[dim]Total API calls: {assistant.api_call_count}[/dim]")


def _handle_tts_demo(console: Console, assistant: VoiceAssistant) -> None:
    """テキストと声を選んでテキスト読み上げを行う。"""
    # テキストを選択
    text_choice = interactive_menu(
        console,
        list(SAMPLE_TEXTS.keys()),
        title="Select Text",
        allow_custom=True,
        custom_label="Custom Text...",
        custom_prompt="読み上げるテキストを入力してください",
    )
    if text_choice is None:
        return

    text = SAMPLE_TEXTS.get(text_choice, text_choice)

    # 声を選択
    voice_items = [f"{v} — {VOICE_DESCRIPTIONS[v]}" for v in VOICES]
    voice_choice = interactive_menu(console, voice_items, title="Select Voice")
    if voice_choice is None:
        return

    voice = voice_choice.split(" — ")[0]

    console.clear()
    console.print(f"\n[yellow]Generating speech with '{voice}' voice...[/yellow]\n")

    file_path = assistant.speak(text, voice)

    console.print(
        Panel(
            f"[bold]Voice:[/bold] {voice} ({VOICE_DESCRIPTIONS[voice]})\n"
            f"[bold]Text:[/bold] {text}\n"
            f"[bold]File:[/bold] {file_path}",
            title="[bold green]Audio Generated[/bold green]",
            border_style="green",
        )
    )


def _handle_voice_comparison(console: Console, assistant: VoiceAssistant) -> None:
    """同じテキストを全ての声で生成する。"""
    text_choice = interactive_menu(
        console,
        list(SAMPLE_TEXTS.keys()),
        title="Select Text for Comparison",
        allow_custom=True,
        custom_label="Custom Text...",
        custom_prompt="声を比較するテキストを入力してください",
    )
    if text_choice is None:
        return

    text = SAMPLE_TEXTS.get(text_choice, text_choice)

    console.clear()
    console.print(f"\n[yellow]Generating {len(VOICES)} voice samples...[/yellow]\n")

    results = assistant.voice_comparison(text)

    table = Table(title="Voice Comparison Results")
    table.add_column("Voice", style="cyan")
    table.add_column("Description", style="dim")
    table.add_column("File", style="green")

    for voice, path in results:
        table.add_row(voice, VOICE_DESCRIPTIONS[voice], path)

    console.print(table)
    console.print(f"\n[dim]Text: {text}[/dim]")


def _handle_round_trip(console: Console, assistant: VoiceAssistant) -> None:
    """テキスト → 音声 → 文字起こし → 比較。"""
    text_choice = interactive_menu(
        console,
        list(SAMPLE_TEXTS.keys()),
        title="Select Text for Round-Trip",
        allow_custom=True,
        custom_label="Custom Text...",
        custom_prompt="往復テストするテキストを入力してください",
    )
    if text_choice is None:
        return

    text = SAMPLE_TEXTS.get(text_choice, text_choice)

    # 声を選択
    voice_items = [f"{v} — {VOICE_DESCRIPTIONS[v]}" for v in VOICES]
    voice_choice = interactive_menu(console, voice_items, title="Select Voice")
    if voice_choice is None:
        return

    voice = voice_choice.split(" — ")[0]

    console.clear()
    console.print("\n[yellow]Running round-trip: text → speech → transcription...[/yellow]\n")

    audio_path, transcription = assistant.round_trip(text, voice)

    # 元のテキストと文字起こし結果を比較する
    original_normalized = text.lower().strip()
    transcribed_normalized = transcription.lower().strip()
    match = original_normalized == transcribed_normalized

    console.print(
        Panel(
            Markdown(
                f"**Original:** {text}\n\n"
                f"**Transcribed:** {transcription}\n\n"
                f"**Audio file:** {audio_path}\n\n"
                f"**Match:** {'完全一致' if match else '差異あり（正常——句読点などが変わることがあります）'}"
            ),
            title="[bold blue]Round-Trip Results[/bold blue]",
            border_style="green" if match else "yellow",
        )
    )


def _handle_transcription(console: Console, assistant: VoiceAssistant) -> None:
    """既存の音声ファイルを文字起こしする。"""
    console.print("\n[bold green]Enter path to audio file:[/bold green] ", end="")
    audio_path = input().strip()

    if not audio_path:
        return

    if not Path(audio_path).exists():
        console.print(f"[red]File not found: {audio_path}[/red]")
        return

    console.print(f"\n[yellow]Transcribing {audio_path}...[/yellow]\n")

    transcription = assistant.transcribe(audio_path)

    console.print(
        Panel(
            Markdown(transcription),
            title="[bold blue]Transcription[/bold blue]",
            border_style="green",
        )
    )


if __name__ == "__main__":
    main()
