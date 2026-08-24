"""
構造化出力とバリデーション (OpenRouter)

信頼できる構造化データをLLMから抽出するための4つの本番運用テクニックを、基本から
応用へと段階的に実演します:

1. ツール呼び出しによる構造化出力 — tool_choiceで構造化された応答を強制する（単純 + 複雑）
2. ネイティブな構造化出力 — API側での制約付きデコーディング（有効性が保証される）
3. バリデーション + リトライ — エラーフィードバックループによる自己修復的な抽出
4. バッチ抽出 — 複数のアイテムを1回の呼び出しで処理する

すべてのテクニックは同じ実世界のドメイン——サポートチケット分析——を使うため、
手法間で結果を直接比較できます。
"""

import json
import os
from typing import Any, Literal

from dotenv import find_dotenv, load_dotenv
from openrouter import OpenRouter
from openrouter.components import ChatResult
from pydantic import BaseModel, Field, ValidationError, model_validator
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax

from common import OpenRouterTokenTracker, interactive_menu, setup_logging
from openrouter_adapter import to_openrouter_tool

load_dotenv(find_dotenv())

logger = setup_logging(__name__)

MODEL = "deepseek/deepseek-v4-flash"

# ---------------------------------------------------------------------------
# Pydanticモデル — 段階的に複雑さを増す
# ---------------------------------------------------------------------------


# 単純なスキーマ: フラットな分類
class TicketClassification(BaseModel):
    """カテゴリ・優先度・感情によるチケットの基本分類。"""

    category: Literal["請求", "技術", "アカウント", "機能リクエスト", "一般"]
    priority: Literal["緊急", "高", "中", "低"]
    sentiment: Literal["ポジティブ", "中立", "ネガティブ", "不満"]
    summary: str = Field(description="チケットの1文サマリー")


# 複雑なスキーマ: ネストした抽出
class Entity(BaseModel):
    """チケット内で言及されたエンティティ。"""

    name: str = Field(description="チケット内で言及された通りのエンティティ名")
    type: Literal["product", "feature", "error_code", "account_id", "person"]
    context: str = Field(description="どのように言及されたかの簡潔な文脈")


class ActionItem(BaseModel):
    """チケットを解決するための推奨アクション。"""

    action: str = Field(description="実施すべき具体的なアクション")
    assignee: Literal["support", "engineering", "billing", "account_manager"]
    urgency: Literal["immediate", "next_business_day", "backlog"]


class TicketAnalysis(BaseModel):
    """分類・エンティティ・アクションアイテムを含む完全なチケット分析。"""

    classification: TicketClassification
    entities: list[Entity]
    action_items: list[ActionItem]
    requires_escalation: bool
    escalation_reason: str | None = None
    customer_tier: Literal["free", "pro", "enterprise"] | None = None

    # JSON Schemaだけでは表現できないビジネスルールのカスタムバリデーション
    @model_validator(mode="after")
    def check_escalation_consistency(self) -> "TicketAnalysis":
        """requires_escalationがTrueの場合、理由が提供されている必要がある。"""
        if self.requires_escalation and not self.escalation_reason:
            raise ValueError("requires_escalationがTrueの場合、escalation_reasonが必要です")
        return self


class TicketBatch(BaseModel):
    """複数チケットのバッチ分析。"""

    analyses: list[TicketAnalysis]
    batch_summary: str = Field(description="バッチ全体の要約")
    priority_distribution: dict[str, int] = Field(description="優先度ごとのチケット数")


# ---------------------------------------------------------------------------
# サンプルサポートチケット（易 → 中 → 難）
# ---------------------------------------------------------------------------

SAMPLE_TICKETS = [
    (
        "件名: Proサブスクリプションが二重に請求された\n"
        "こんにちは、今月Proサブスクリプションが2回請求されました——1月3日に$49.99、"
        "1月5日にもう一度です。私のアカウントIDはACC-78234です。これで3回目で、"
        "本当にうんざりしています。重複した請求を至急返金してください。今日中に"
        "解決されなければ、サブスクリプションを解約します。"
    ),
    (
        "件名: EnterpriseプランでAPIレート制限の問題\n"
        "50件以上のアイテムをバッチ処理すると、APIが429エラーを返し続けます。"
        "Enterpriseプランに加入していて、ドキュメントによるとレート制限は1000/分の"
        "はずです。レスポンスにretry-afterヘッダーを追加していただけないでしょうか？"
        "それがあるとかなり助かります。Python SDK v3.2.1を使用しています。"
    ),
    (
        "件名: エンタープライズ評価中にSSOがブロッカーに\n"
        "200人のエンジニアからなる私たちのチーム向けに貴社の製品を評価しています。"
        "OktaとのSSO連携はうまくいきましたが、ブロッカーに直面しました——50人を超える"
        "メンバーを持つグループを同期する際、SCIMプロビジョニングエンドポイントが"
        "500エラーを返します（エラー: SCIM-ERR-4012）。また、ボリュームプライシングを"
        "利用する方法はありますか？現在のAcme Corp契約は来月更新期限を迎えます。"
        "連絡先: Sarah Chen, VP Engineering。"
    ),
]

SYSTEM_PROMPT = (
    "あなたはサポートチケット分析システムです。カスタマーサポートチケットを分析し、"
    "構造化データを抽出してください。分類は正確に行い、関連するすべてのエンティティと"
    "アクションアイテムを抽出してください。"
)


# ---------------------------------------------------------------------------
# コア抽出器クラス
# ---------------------------------------------------------------------------


class StructuredExtractor:
    """複数のテクニックを使い、非構造化テキストから構造化データを抽出する。"""

    def __init__(self, model: str, token_tracker: OpenRouterTokenTracker):
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.model = model
        self.token_tracker = token_tracker

    def _call_llm(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int = 2048,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> ChatResult:
        """単一のLLM呼び出しを行い、トークンを追跡する"""
        kwargs: dict[str, Any] = {}
        if tools:
            kwargs["tools"] = tools
        if tool_choice:
            kwargs["tool_choice"] = tool_choice
        if response_format:
            kwargs["response_format"] = response_format

        response: ChatResult = self.client.chat.send(  # type: ignore[call-overload]
            model=self.model,
            max_tokens=max_tokens,
            reasoning={"effort": "none"},
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, *messages],
            **kwargs,
        )
        self.token_tracker.track(response.usage)
        return response

    # -- テクニック1: ツール呼び出しによる構造化出力 --

    def extract_with_tool_use(
        self,
        text: str,
        model_class: type[BaseModel] = TicketClassification,
        tool_name: str = "classify_ticket",
        tool_description: str = "サポートチケットを分類する。",
    ) -> BaseModel | None:
        """tool_choiceでツール定義を強制呼び出しし、構造化出力を抽出する"""
        tool = to_openrouter_tool(self._pydantic_to_tool(tool_name, tool_description, model_class))
        try:
            response = self._call_llm(
                messages=[
                    {"role": "user", "content": f"次のチケットを分析してください:\n\n{text}"}
                ],
                tools=[tool],
                tool_choice={"type": "function", "function": {"name": tool_name}},
            )
            tool_calls = response.choices[0].message.tool_calls
            if tool_calls:
                data = json.loads(tool_calls[0].function.arguments)
                return model_class(**data)
        except Exception as e:
            logger.error("Tool use extraction failed: %s", e)
        return None

    # -- テクニック2: ネイティブな構造化出力（制約付きデコーディング） --

    def extract_with_native_schema(self, text: str) -> TicketClassification | None:
        """OpenRouterのresponse_format（json_schema, strict）を使い、有効性が保証された出力を抽出する"""
        schema = self._pydantic_to_json_schema("ticket_classification", TicketClassification)
        try:
            response = self._call_llm(
                messages=[
                    {"role": "user", "content": f"次のチケットを分析してください:\n\n{text}"}
                ],
                max_tokens=1024,
                response_format={"type": "json_schema", "json_schema": schema},
            )
            content = response.choices[0].message.content
            if isinstance(content, str) and content:
                data = json.loads(content)
                return TicketClassification(**data)
        except Exception as e:
            logger.error("Native schema extraction failed: %s", e)
        return None

    # -- テクニック3: バリデーション + リトライ（自己修復） --

    def extract_with_validation_retry(
        self, text: str, max_retries: int = 3
    ) -> TicketAnalysis | None:
        """バリデーションループで抽出する — エラーフィードバック付きで失敗時にリトライする"""
        tool = to_openrouter_tool(
            self._pydantic_to_tool(
                name="analyze_ticket",
                description="サポートチケットの完全な分析を行う。",
                model=TicketAnalysis,
            )
        )
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": f"完全な分析を行ってください:\n\n{text}"}
        ]

        for attempt in range(1, max_retries + 1):
            tool_calls = None
            try:
                response = self._call_llm(
                    messages=messages,
                    tools=[tool],
                    tool_choice={"type": "function", "function": {"name": "analyze_ticket"}},
                )
                tool_calls = response.choices[0].message.tool_calls

                if tool_calls:
                    raw = json.loads(tool_calls[0].function.arguments)
                    # Pydanticでバリデーション（カスタムのビジネスルールを含む）
                    result = TicketAnalysis(**raw)
                    logger.info("Attempt %d: validation passed", attempt)
                    return result

            except ValidationError as e:
                logger.warning("Attempt %d: validation failed — %s", attempt, e)
                if attempt < max_retries and tool_calls:
                    # エラーをLLMにフィードバックして修正させる
                    messages = [
                        *messages,
                        {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [tc.model_dump() for tc in tool_calls],
                        },
                        {
                            "role": "tool",
                            "tool_call_id": tool_calls[0].id,
                            "content": (
                                f"出力がバリデーションに失敗しました:\n{e}\n\n"
                                "問題を修正して再度試してください。主なルール:\n"
                                "- requires_escalationがtrueの場合、escalation_reasonは"
                                "空でない文字列である必要があります\n"
                                "- すべてのenum値は正確に一致する必要があります"
                            ),
                        },
                    ]
            except Exception as e:
                logger.error("Attempt %d: unexpected error — %s", attempt, e)
                break

        logger.error("All %d attempts failed", max_retries)
        return None

    # -- テクニック4: バッチ抽出 --

    def extract_batch(self, texts: list[str]) -> TicketBatch | None:
        """複数のチケットから、1回の呼び出しで構造化データを抽出する"""
        tool = to_openrouter_tool(
            self._pydantic_to_tool(
                name="batch_analyze",
                description="複数のサポートチケットを分析し、バッチサマリーを提供する。",
                model=TicketBatch,
            )
        )
        numbered_tickets = "\n\n".join(
            f"--- TICKET {i + 1} ---\n{text}" for i, text in enumerate(texts)
        )
        try:
            response = self._call_llm(
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"以下の{len(texts)}件のチケットをすべて分析し、"
                            f"バッチ分析を提供してください:\n\n{numbered_tickets}"
                        ),
                    }
                ],
                max_tokens=4096,
                tools=[tool],
                tool_choice={"type": "function", "function": {"name": "batch_analyze"}},
            )
            tool_calls = response.choices[0].message.tool_calls
            if tool_calls:
                data = json.loads(tool_calls[0].function.arguments)
                return TicketBatch(**data)
        except Exception as e:
            logger.error("Batch extraction failed: %s", e)
        return None

    # -- ヘルパー --

    def _pydantic_to_tool(
        self, name: str, description: str, model: type[BaseModel]
    ) -> dict[str, Any]:
        """任意のPydanticモデルをAnthropicスタイルのツール定義に変換する"""
        return {
            "name": name,
            "description": description,
            "input_schema": model.model_json_schema(),
        }

    def _pydantic_to_json_schema(self, name: str, model: type[BaseModel]) -> dict[str, Any]:
        """Pydanticモデルをresponse_format用のstrict JSON Schemaに変換する

        strictモードでは、すべてのオブジェクトレベルで `additionalProperties: false` と
        全プロパティの `required` 指定が必要となる。この制約はネストしたモデルを扱うため
        再帰的に適用する。
        """
        schema = model.model_json_schema()
        self._add_strict_constraints(schema)
        return {
            "name": name,
            "strict": True,
            "schema": schema,
        }

    def _add_strict_constraints(self, schema: dict[str, Any]) -> None:
        """すべてのobject型に再帰的にadditionalProperties: falseを追加する"""
        if schema.get("type") == "object":
            schema["additionalProperties"] = False
            if "properties" in schema:
                schema.setdefault("required", list(schema["properties"].keys()))
        for value in schema.values():
            if isinstance(value, dict):
                self._add_strict_constraints(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        self._add_strict_constraints(item)
        # $defs はPydanticがネストしたモデルのスキーマを格納する場所
        if "$defs" in schema:
            for defn in schema["$defs"].values():
                self._add_strict_constraints(defn)


# ---------------------------------------------------------------------------
# 表示ヘルパー（Rich UI）
# ---------------------------------------------------------------------------


def _display_result(console: Console, title: str, result: BaseModel | None) -> None:
    """Pydanticモデルを整形されたJSONとして表示する"""
    if result:
        formatted = json.dumps(result.model_dump(), indent=2, default=str, ensure_ascii=False)
        syntax = Syntax(formatted, "json", theme="monokai")
        console.print(Panel(syntax, title=f"{title} [green]SUCCESS[/green]"))
    else:
        console.print(Panel("[red]Extraction failed[/red]", title=title))


# ---------------------------------------------------------------------------
# メニューハンドラー
# ---------------------------------------------------------------------------

TECHNIQUE_LABELS = [
    "1: ツール呼び出しによる構造化出力（単純 + 複雑）",
    "2: ネイティブな構造化出力（制約付きデコーディング）",
    "3: バリデーション + リトライ（自己修復）",
    "4: バッチ抽出（複数アイテム）",
]


def _run_tool_use(console: Console, extractor: StructuredExtractor) -> None:
    """テクニック1: tool_choiceで抽出 — 単純と複雑なスキーマ"""
    console.print(
        "[dim]input_schemaが求める出力スキーマそのものであるツールを定義し、"
        "tool_choiceでそれを呼び出させることで構造化出力を強制する。[/dim]\n"
    )
    console.print(
        Markdown(
            "**仕組み:** ツールを定義 → `tool_choice`で強制 → "
            "`block.input`を構造化データとして抽出。\n\n"
            "**重要なポイント:** `model.model_json_schema()`を使ってPydanticモデルから"
            "ツールスキーマを生成する——複雑な構造に対して手書きのJSON Schemaを"
            "書くことは避ける。\n\n"
            "**信頼性:** 高い——ツール入力はAPIによってスキーマ検証される。\n"
        )
    )

    # パートA: 単純なフラットスキーマ
    console.print(
        "[bold]パートA: 単純なスキーマ[/bold] — `TicketClassification`（フラット、4フィールド）\n"
    )
    ticket_simple = SAMPLE_TICKETS[0]
    console.print(Panel(ticket_simple, title="Input Ticket (Simple)"))

    result_simple = extractor.extract_with_tool_use(ticket_simple)
    _display_result(console, "Simple Schema Extraction", result_simple)

    # パートB: 複雑なネストスキーマ
    console.print(
        "\n[bold]パートB: 複雑なスキーマ[/bold] — `TicketAnalysis` "
        "（ネスト: 分類 + エンティティ + アクションアイテム、10以上のフィールド）\n"
    )
    ticket_complex = SAMPLE_TICKETS[2]
    console.print(Panel(ticket_complex, title="Input Ticket (Complex)"))

    result_complex = extractor.extract_with_tool_use(
        ticket_complex,
        model_class=TicketAnalysis,
        tool_name="analyze_ticket",
        tool_description="サポートチケットの完全な分析を行う。",
    )
    _display_result(console, "Complex Schema Extraction", result_complex)


def _run_native_schema(console: Console, extractor: StructuredExtractor) -> None:
    """テクニック2: ネイティブな制約付きデコーディングで抽出"""
    console.print(
        "[dim]OpenRouterのresponse_format（json_schema, strict）を使う——モデルは"
        "文字通り不正なJSONを生成できなくなる。デコーダーレベルでの制約付き"
        "デコーディングを使用する。[/dim]\n"
    )
    console.print(
        Markdown(
            "**仕組み:** Pydanticモデルから生成したJSON Schemaを`response_format`に渡す → "
            "APIが応答をスキーマに厳密に一致させることを保証する。\n\n"
            "**スキーマ:** `TicketClassification`（フラット、4フィールド）\n\n"
            "**信頼性:** 保証される——デコーダーレベルでの強制、パースエラーはゼロ。\n"
        )
    )

    ticket = SAMPLE_TICKETS[0]
    console.print(Panel(ticket, title="Input Ticket"))

    result = extractor.extract_with_native_schema(ticket)
    _display_result(console, "Native Schema Extraction", result)


def _run_validation_retry(console: Console, extractor: StructuredExtractor) -> None:
    """テクニック3: バリデーション + リトライによる自己修復的な抽出"""
    console.print(
        "[dim]スキーマ検証だけでは不十分な場合——カスタムのビジネスルールを追加する。"
        "失敗時には、バリデーションエラーをLLMにフィードバックして自己修正させる。[/dim]\n"
    )
    console.print(
        Markdown(
            "**仕組み:** 抽出 → Pydanticでバリデーション（カスタムの`@model_validator`"
            "ルールを含む） → 失敗時、エラーをLLMに送り返す → リトライ。\n\n"
            "**カスタムルール:** `requires_escalation`がTrueの場合、`escalation_reason`が"
            "提供されている必要がある（JSON Schemaだけでは表現できない）。\n\n"
            "**最大リトライ回数:** エラー蓄積付きで3回まで。\n"
        )
    )

    # チケット3を使用 — エスカレーションが必要になりやすい（エンタープライズ評価、ブロッカー）
    ticket = SAMPLE_TICKETS[2]
    console.print(Panel(ticket, title="Input Ticket (Requires Escalation)"))

    result = extractor.extract_with_validation_retry(ticket)
    _display_result(console, "Validation + Retry Extraction", result)


def _run_batch(console: Console, extractor: StructuredExtractor) -> None:
    """テクニック4: 複数アイテムからのバッチ抽出"""
    console.print(
        "[dim]複数のチケットを1回のAPI呼び出しで処理する。モデルはそれぞれについて"
        "構造化データを抽出し、バッチサマリーを提供する。[/dim]\n"
    )
    console.print(
        Markdown(
            "**仕組み:** すべてのチケットを1つのプロンプトで送信 → `list[TicketAnalysis]` + "
            "サマリー + 優先度分布を持つ`TicketBatch`を抽出。\n\n"
            "**ユースケース:** チケットキューを処理する本番データパイプライン。\n\n"
            "**トレードオフ:** 1回の呼び出し（安価）対アイテムごとの呼び出し（より信頼性が"
            "高い）。バッチは3〜10アイテムでうまく機能する。それ以上は個別の呼び出しを"
            "並列化すること。\n"
        )
    )

    for i, ticket in enumerate(SAMPLE_TICKETS):
        console.print(Panel(ticket, title=f"Ticket {i + 1}"))

    result = extractor.extract_batch(SAMPLE_TICKETS)
    _display_result(console, "Batch Extraction", result)


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


def main() -> None:
    """4つの構造化出力テクニックでサポートチケット分析を実行する"""
    console = Console()
    token_tracker = OpenRouterTokenTracker()
    extractor = StructuredExtractor(MODEL, token_tracker)

    header = Panel(
        "[bold cyan]構造化出力とバリデーション[/bold cyan]\n\n"
        "LLMから信頼できる構造化データを抽出するための4つのテクニック:\n"
        "  1. Tool Use — tool_choiceで構造化出力を強制（単純 + 複雑）\n"
        "  2. Native Schema — 制約付きデコーディング（有効性が保証される）\n"
        "  3. Validation + Retry — エラーフィードバックによる自己修復\n"
        "  4. Batch Extraction — 複数アイテムを1回の呼び出しで\n\n"
        "[bold]ドメイン:[/bold] サポートチケット分析（分類・エンティティ・アクション）",
        title="Advanced Techniques — OpenRouter",
    )

    handlers = {
        TECHNIQUE_LABELS[0]: _run_tool_use,
        TECHNIQUE_LABELS[1]: _run_native_schema,
        TECHNIQUE_LABELS[2]: _run_validation_retry,
        TECHNIQUE_LABELS[3]: _run_batch,
    }

    try:
        while True:
            selection = interactive_menu(
                console,
                TECHNIQUE_LABELS,
                title="Select a Technique",
                header=header,
            )
            if not selection:
                break

            console.print(f"\n[bold yellow]━━━ {selection} ━━━[/bold yellow]\n")

            try:
                handlers[selection](console, extractor)
            except Exception as e:
                logger.error("Technique error: %s", e)

            token_tracker.report()
            token_tracker.reset()

            console.print("\n[dim]Press Enter to continue...[/dim]")
            input()

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")


if __name__ == "__main__":
    main()
