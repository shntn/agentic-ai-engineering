"""
構造化出力とプロンプトスキャフォールディング (OpenRouter)

OpenRouter からパース可能な構造化出力を得るための手法を示します:
1. プロンプト指示による JSON — システムプロンプトで JSON を要求する
2. Markdown スキャフォールディング — 構造化されたセクションで出力を誘導する
3. JSON スキーマ強制 — OpenAI のネイティブ構造化出力機能

3つの手法はすべて同じ商品説明から同じ商品情報を抽出するため、手法間の信頼性を
比較しやすくなっています。
"""

import os
import json

from dotenv import find_dotenv, load_dotenv
from openrouter import OpenRouter
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax

from common import OpenRouterTokenTracker, interactive_menu, setup_logging

load_dotenv(find_dotenv())

logger = setup_logging(__name__)

# LLM が埋めるべきスキーマ（人間が読める形式）
PRODUCT_SCHEMA = {
    "name": "string — 商品名",
    "category": "string — 商品カテゴリ（例: Electronics, Clothing）",
    "price": "number — 価格（米ドル）",
    "features": "string のリスト — 商品の主な特徴",
    "in_stock": "boolean — 商品が現在入手可能かどうか",
}

# OpenRouter のネイティブ構造化出力強制のための JSON スキーマ
PRODUCT_JSON_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "product_extraction",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "商品名"},
                "category": {
                    "type": "string",
                    "description": "商品カテゴリ（例: Electronics, Clothing）",
                },
                "price": {"type": "number", "description": "価格（米ドル）"},
                "features": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "商品の主な特徴",
                },
                "in_stock": {
                    "type": "boolean",
                    "description": "商品が現在入手可能かどうか",
                },
            },
            "required": ["name", "category", "price", "features", "in_stock"],
            "additionalProperties": False,
        },
    },
}

# 単一の商品説明 — 3つの手法すべてがこの同じ入力から抽出します
PRODUCT_DESCRIPTION = (
    "UltraSound Pro X1 ワイヤレスノイズキャンセリングヘッドホンは、40mm カスタムドライバーと"
    "アダプティブ ANC により、スタジオ品質のオーディオを実現します。30時間のバッテリー駆動、"
    "2台のデバイスを同時に接続できるマルチポイント Bluetooth 5.3、プレミアムキャリングケース付きの"
    "折りたたみデザインを備えています。価格は $249.99 で現在発売中。在庫あり、24時間以内に発送します。"
)


class StructuredOutputClient:
    """OpenRouter の API を用いた構造化出力の手法を実演"""

    def __init__(self, model: str, token_tracker: OpenAITokenTracker):
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.model = model
        self.token_tracker = token_tracker

    def _call(self, system: str, user_input: str, **kwargs) -> str:
        """API 呼び出しを行い、トークンを追跡"""
        response = self.client.chat.send(
            model=self.model,
            temperature=0.0,
            max_tokens=512,
            reasoning={"effort": "none", "summary": "null"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_input}
            ],
            **kwargs,
        )
        self.token_tracker.track(response.usage)
        return str(response.choices[0].message.content).strip()

    def extract_json_prompted(self, description: str) -> str:
        """プロンプトで JSON を要求して構造化データを抽出する"""
        schema_str = json.dumps(PRODUCT_SCHEMA, indent=2, ensure_ascii=False)
        system = (
            "あなたは商品データ抽出アシスタントです。商品説明から構造化された情報を"
            "抽出してください。\n\n"
            f"次のスキーマに一致する有効な JSON のみを出力してください:\n{schema_str}\n\n"
            "マークダウンや説明は不要です — JSON オブジェクトのみを返してください。"
        )
        return self._call(system, description)

    def extract_with_scaffolding(self, description: str) -> str:
        """Markdown セクションを使って入力をスキャフォールディングし、出力を誘導する"""
        schema_str = json.dumps(PRODUCT_SCHEMA, indent=2, ensure_ascii=False)
        # OpenAI は markdown 構造化されたプロンプトとの相性が良い
        system = (
            "あなたは商品データ抽出アシスタントです。構造化された入力を受け取り、"
            "商品データを JSON として抽出します。\n\n"
            "提供されたスキーマに一致する有効な JSON のみを出力してください。"
            "markdown フェンスや説明は不要です。"
        )
        user_input = (
            f"## スキーマ\n```json\n{schema_str}\n```\n\n"
            f"## 商品説明\n{description}\n\n"
            "## 出力\n商品情報を JSON として抽出してください:"
        )
        return self._call(system, user_input)

    def extract_with_schema(self, description: str) -> str:
        """OpenAI のネイティブ JSON スキーマ強制を使用する — 有効な JSON が保証される"""
        system = (
            "あなたは商品データ抽出アシスタントです。商品説明から構造化された情報を"
            "抽出してください。説明文に基づいてすべてのフィールドを埋めてください。"
        )
        # response_format パラメータが API レベルでスキーマを強制します
        return self._call(
            system,
            description,
            response_format=PRODUCT_JSON_SCHEMA,
        )


def _try_parse_json(raw: str) -> dict | None:
    """JSON のパースを試み、markdown フェンスがあれば取り除く"""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        parsed: dict[str, object] = json.loads(text)
        return parsed
    except json.JSONDecodeError as e:
        logger.warning("JSON parse failed: %s", e)
        return None


def _display_result(console: Console, method_name: str, raw: str) -> None:
    """構造化出力手法からの JSON 結果をパースして表示"""
    parsed = _try_parse_json(raw)
    if parsed:
        formatted = json.dumps(parsed, indent=2, ensure_ascii=False)
        syntax = Syntax(formatted, "json", theme="monokai")
        console.print(Panel(syntax, title=f"{method_name} [green]VALID JSON[/green]"))
    else:
        console.print(Panel(raw[:300], title=f"{method_name} [red]PARSE FAILED[/red]"))


METHOD_LABELS = [
    "1: プロンプトによる JSON",
    "2: Markdown スキャフォールディング",
    "3: スキーマ強制",
]


def _run_method_1(console: Console, client: StructuredOutputClient) -> None:
    """プロンプトによる JSON 抽出手法を実行"""
    schema_str = json.dumps(PRODUCT_SCHEMA, indent=2, ensure_ascii=False)
    console.print("[dim]スキーマをシステムプロンプトに埋め込み、JSON 出力を要求します。[/dim]\n")
    prompt_1 = (
        "**システムプロンプト:**\n"
        "```\n"
        "あなたは商品データ抽出アシスタントです...\n"
        f"次のスキーマに一致する有効な JSON のみを出力してください:\n{schema_str}\n"
        "マークダウンや説明は不要です — JSON オブジェクトのみを返してください。\n"
        "```\n\n"
        "**入力:** _(商品説明の生テキスト)_\n"
    )
    console.print(Markdown(prompt_1))

    try:
        raw = client.extract_json_prompted(PRODUCT_DESCRIPTION)
        _display_result(console, "Prompted JSON", raw)
    except Exception as e:
        logger.error("Error in method 1: %s", e)


def _run_method_2(console: Console, client: StructuredOutputClient) -> None:
    """Markdown スキャフォールディング抽出手法を実行"""
    schema_str = json.dumps(PRODUCT_SCHEMA, indent=2, ensure_ascii=False)
    console.print("[dim]Markdown セクションで入力を構造化し、出力を誘導します。[/dim]\n")
    prompt_2 = (
        "**システムプロンプト:**\n"
        "```\n"
        "あなたは商品データ抽出アシスタントです。\n"
        "提供されたスキーマに一致する有効な JSON のみを出力してください。\n"
        "markdown フェンスや説明は不要です。\n"
        "```\n\n"
        "**入力（Markdown構造化）:**\n"
        "```markdown\n"
        f"## スキーマ\n```json\n{schema_str}\n\\`\\`\\`\n\n"
        "## 商品説明\n(ここに商品説明が入ります)\n\n"
        "## 出力\n商品情報を JSON として抽出してください:\n"
        "```\n"
    )
    console.print(Markdown(prompt_2))

    try:
        raw = client.extract_with_scaffolding(PRODUCT_DESCRIPTION)
        _display_result(console, "Markdown Scaffolding", raw)
    except Exception as e:
        logger.error("Error in method 2: %s", e)


def _run_method_3(console: Console, client: StructuredOutputClient) -> None:
    """スキーマ強制抽出手法を実行"""
    console.print("[dim]response_format による API レベルの強制 — 有効な JSON が保証されます。[/dim]\n")
    schema_preview = json.dumps(PRODUCT_JSON_SCHEMA, indent=2, ensure_ascii=False)
    prompt_3 = (
        "**システムプロンプト:**\n"
        "```\n"
        "あなたは商品データ抽出アシスタントです...\n"
        "説明文に基づいてすべてのフィールドを埋めてください。\n"
        "```\n\n"
        "**入力:** _(商品説明の生テキスト)_\n\n"
        "**response_format (JSON スキーマ):**\n"
        f"```json\n{schema_preview}\n```\n\n"
        "_API はレスポンスがこのスキーマに準拠することを保証します — パースは不要です。_\n"
    )
    console.print(Markdown(prompt_3))

    try:
        raw = client.extract_with_schema(PRODUCT_DESCRIPTION)
        _display_result(console, "Schema Enforcement", raw)
    except Exception as e:
        logger.error("Error in method 3: %s", e)


def main() -> None:
    """1つの商品説明を3つの構造化出力手法にかけて実行"""
    console = Console()
    token_tracker = OpenRouterTokenTracker()
    client = StructuredOutputClient("deepseek/deepseek-v4-flash", token_tracker)

    header = Panel(
        "[bold cyan]構造化出力とプロンプトスキャフォールディング[/bold cyan]\n\n"
        "自由形式のテキストから構造化された JSON を抽出する3つの手法を比較します:\n"
        "  1. プロンプト指示による JSON\n"
        "  2. Markdown スキャフォールディング\n"
        "  3. JSON スキーマ強制（OpenAI 固有）\n\n"
        f"[bold]商品説明:[/bold]\n{PRODUCT_DESCRIPTION}",
        title="プロンプトエンジニアリング — OpenRouter",
    )

    methods = {
        METHOD_LABELS[0]: _run_method_1,
        METHOD_LABELS[1]: _run_method_2,
        METHOD_LABELS[2]: _run_method_3,
    }

    try:
        while True:
            selection = interactive_menu(
                console,
                METHOD_LABELS,
                title="手法を選択",
                header=header,
            )
            if not selection:
                break

            console.print(f"\n[bold yellow]━━━ {selection} ━━━[/bold yellow]")

            try:
                methods[selection](console, client)
            except Exception as e:
                logger.error("Method error: %s", e)

            token_tracker.report()
            token_tracker.reset()

            console.print("\n[dim]Press Enter to continue...[/dim]")
            input()

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")


if __name__ == "__main__":
    main()
