"""
ツール出力のコンテキストエンジニアリング (OpenRouter)

エージェントのコンテキストウィンドウ内でツール出力を管理する3つの戦略を実演します:
naive（生データをそのまま注入）、truncation（文字数上限で切り詰め）、summarization
（LLMによる抽出）。現実的に大きなJSONペイロードを返す模擬的なビジネスツールを使い、
ツール出力がいかにコンテキスト消費を支配するかを示します。
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import tiktoken
from dotenv import find_dotenv, load_dotenv
from openrouter import OpenRouter
from openrouter.components import ChatResult
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from common import OpenRouterTokenTracker, interactive_menu, setup_logging
from openrouter_adapter import to_openrouter_tools

# ルートの.envファイルから環境変数を読み込む
load_dotenv(find_dotenv())

# ロギングを設定
logger = setup_logging(__name__)

# モデル設定
MODEL = "deepseek/deepseek-v4-flash"

SYSTEM_PROMPT = (
    "あなたはCRM・注文・製品ツールにアクセスできるビジネスデータアシスタントです。"
    "適切なツールを呼び出してユーザーの質問に答えてください。簡潔に、ツール結果の"
    "具体的なデータポイントを参照しながら回答してください。"
)

# デモですぐに圧縮がトリガーされるよう、人為的に低い予算を設定
MAX_CONTEXT_TOKENS = 4096
RESPONSE_RESERVE = 2048
RECENT_MESSAGES_TO_KEEP = 4

# 戦略の定数
TRUNCATE_MAX_CHARS = 500

STRATEGIES = {
    "naive": "生のツール出力をそのまま注入する（ベースライン——コンテキストを一気に消費する）",
    "truncate": f"ツール出力を{TRUNCATE_MAX_CHARS}文字で打ち切る（無料だが情報が失われる）",
    "summarize": "LLMがツール出力から要点を抽出する（追加のAPI呼び出しが発生するが意味を保持する）",
}

# --- 模擬ビジネスツール ---

TOOLS = [
    {
        "name": "lookup_customer",
        "description": (
            "顧客名で顧客を検索する。連絡先情報・住所・アカウント履歴・好み・"
            "直近のサポートチケットを返す。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "検索する顧客名",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_order_history",
        "description": (
            "顧客の注文履歴を取得する。明細・合計金額・日付・配送状況を含む注文のリストを返す。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "description": "顧客ID（例: CUST-1001）",
                },
            },
            "required": ["customer_id"],
        },
    },
    {
        "name": "search_products",
        "description": (
            "キーワードで製品カタログを検索する。説明・仕様・価格・在庫状況を含む"
            "一致した製品を返す。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "検索キーワードまたはフレーズ",
                },
            },
            "required": ["query"],
        },
    },
]

# APIに渡す形式・トークンカウントの両方で使い回すため、一度だけ変換しておく
OPENROUTER_TOOLS = to_openrouter_tools(TOOLS)

# OpenRouterにはAnthropicのmessages.count_tokens()に相当する専用のトークンカウント
# APIがないため、tiktokenのcl100k_baseエンコーディングで近似する。
_ENCODING = tiktoken.get_encoding("cl100k_base")


DB_PATH = Path(__file__).parent / "database.json"


class MockDatabaseService:
    """JSONから読み込む模擬的なビジネスデータベース。"""

    def __init__(self, db_path: Path) -> None:
        self.data: dict[str, Any] = json.loads(db_path.read_text())
        logger.info("Loaded mock database from %s", db_path.name)

    def get_customer(self, name: str) -> dict:
        """顧客名で検索し、最初に一致した顧客を返す。"""
        name_lower = name.lower()
        for customer in self.data["customers"].values():
            if name_lower in customer["name"].lower():
                match: dict = customer
                return match
        return {"error": f"Customer '{name}' not found"}

    def get_orders(self, customer_id: str) -> dict:
        """顧客IDの注文履歴を取得する。"""
        if customer_id in self.data["orders"]:
            orders: dict = self.data["orders"][customer_id]
            return orders
        return {"error": f"No orders found for customer '{customer_id}'"}

    def search_products(self, query: str) -> dict:
        """キーワードで製品カタログを検索する。"""
        query_lower = query.lower()
        matches = [p for p in self.data["products"] if query_lower in json.dumps(p).lower()]
        # 具体的な一致がなければ全製品を返す（広範な検索を模擬）
        results = matches if matches else self.data["products"]
        return {"query": query, "total_results": len(results), "products": results}


# --- データクラス（自己完結、スクリプト03と同じパターン） ---


@dataclass
class ContextBudget:
    """コンテキストの各コンポーネントにまたがるトークン予算の配分。"""

    max_context: int
    system_tokens: int = 0
    response_reserve: int = RESPONSE_RESERVE

    @property
    def history_budget(self) -> int:
        """会話履歴に利用可能なトークン数。"""
        return self.max_context - self.system_tokens - self.response_reserve


@dataclass
class TokenSnapshot:
    """予算表示用のトークン使用量のスナップショット。"""

    system: int = 0
    history: int = 0
    history_budget: int = 0
    reserve: int = 0
    message_count: int = 0
    compression_count: int = 0


# --- コアエージェント ---


class ToolContextAgent:
    """ツール出力のコンテキスト管理戦略を実演するエージェント。"""

    def __init__(
        self,
        model: str,
        strategy: str,
        max_context: int,
        token_tracker: OpenRouterTokenTracker,
        db: MockDatabaseService,
    ):
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.model = model
        self.strategy = strategy
        self.token_tracker = token_tracker
        self.db = db
        self.messages: list[dict[str, Any]] = []
        self.budget = ContextBudget(max_context=max_context)
        self.compression_count = 0

        # ツール名 → サービスメソッドのマッピング
        self.tool_handlers: dict[str, Any] = {
            "lookup_customer": lambda **kw: self.db.get_customer(kw["name"]),
            "get_order_history": lambda **kw: self.db.get_orders(kw["customer_id"]),
            "search_products": lambda **kw: self.db.search_products(kw["query"]),
        }

        # 初期化時に一度だけシステムプロンプト＋ツール定義のトークン数を計測する
        self.budget.system_tokens = self._count_tokens([])
        logger.info(
            "Context budget — system+tools: %d, history: %d, reserve: %d, strategy: %s",
            self.budget.system_tokens,
            self.budget.history_budget,
            self.budget.response_reserve,
            self.strategy,
        )

    def chat(self, user_input: str) -> str:
        """エージェントループ: 送信 → ツール呼び出しを検知 → 実行 → 結果を処理 → 繰り返す。"""
        self.messages.append({"role": "user", "content": user_input})

        # 履歴が予算を超えていれば送信前に圧縮する
        self._compress_if_needed()

        while True:
            logger.info(
                "Sending request (messages: %d, history tokens: ~%d/%d)",
                len(self.messages),
                self._count_tokens(self.messages) - self.budget.system_tokens,
                self.budget.history_budget,
            )

            response: ChatResult = self.client.chat.send(  # type: ignore[call-overload]
                model=self.model,
                max_tokens=self.budget.response_reserve,
                reasoning={"effort": "none"},
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, *self.messages],
                tools=OPENROUTER_TOOLS,
                tool_choice="auto",
            )

            self.token_tracker.track(response.usage)

            message = response.choices[0].message
            text = str(message.content or "")
            tool_calls = message.tool_calls

            # アシスタントの応答を履歴に追加する
            if tool_calls:
                self.messages.append(
                    {
                        "role": "assistant",
                        "content": text or None,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                },
                            }
                            for tc in tool_calls
                        ],
                    }
                )
            else:
                self.messages.append({"role": "assistant", "content": text})

            # ツール呼び出しがなければテキスト応答を返す
            if not tool_calls:
                return text

            # ツールを実行し、結果に戦略を適用する
            for tool_call in tool_calls:
                tool_name = tool_call.function.name
                tool_input = json.loads(tool_call.function.arguments)

                logger.info("Executing tool: %s(%s)", tool_name, json.dumps(tool_input))

                # データベースサービス経由でツールを実行する
                raw_result = json.dumps(self.tool_handlers[tool_name](**tool_input), indent=2)

                # ツール出力にコンテキスト戦略を適用する
                processed_result = self._process_tool_result(tool_name, raw_result)

                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": processed_result,
                    }
                )

            # ツール結果が予算を超えさせていれば再度圧縮する
            self._compress_if_needed()

    def _process_tool_result(self, tool_name: str, raw_result: str) -> str:
        """コンテキストに注入する前に、選択した戦略をツール出力へ適用する。"""
        raw_chars = len(raw_result)

        if self.strategy == "naive":
            logger.info("[naive] Tool %s: %d chars injected as-is", tool_name, raw_chars)
            return raw_result

        if self.strategy == "truncate":
            processed = self._truncate_result(raw_result)
            logger.info("[truncate] Tool %s: %d → %d chars", tool_name, raw_chars, len(processed))
            return processed

        if self.strategy == "summarize":
            processed = self._summarize_result(tool_name, raw_result)
            logger.info("[summarize] Tool %s: %d → %d chars", tool_name, raw_chars, len(processed))
            return processed

        return raw_result

    def _truncate_result(self, result: str) -> str:
        """TRUNCATE_MAX_CHARSで打ち切り、切り詰めを示すマーカーを付与する。"""
        if len(result) <= TRUNCATE_MAX_CHARS:
            return result
        return result[:TRUNCATE_MAX_CHARS] + "\n... [TRUNCATED — output exceeded limit]"

    def _summarize_result(self, tool_name: str, result: str) -> str:
        """ツール出力から要点を抽出するLLM呼び出し。"""
        response: ChatResult = self.client.chat.send(  # type: ignore[call-overload]
            model=self.model,
            max_tokens=512,
            reasoning={"effort": "none"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "このツール出力から重要な事実を抽出し、簡潔な要約にまとめて"
                        "ください。名前・ID・数値・日付・ステータスはすべて保持して"
                        "ください。フラットな箇条書き形式で、簡潔かつ漏れなくまとめて"
                        "ください。"
                    ),
                },
                {
                    "role": "user",
                    "content": f"Tool: {tool_name}\n\nOutput:\n{result}",
                },
            ],
        )

        self.token_tracker.track(response.usage)
        return str(response.choices[0].message.content or "")

    def _count_tokens(self, messages: list[dict[str, Any]]) -> int:
        """tiktokenによる近似値でトークン数をカウントする（システム＋ツール定義を含む）。"""
        parts = [SYSTEM_PROMPT, json.dumps(OPENROUTER_TOOLS)]
        for m in messages:
            content = m.get("content")
            if content:
                parts.append(str(content))
            if m.get("tool_calls"):
                parts.append(json.dumps(m["tool_calls"]))
        return len(_ENCODING.encode("".join(parts)))

    def _compress_if_needed(self) -> None:
        """履歴が予算を超えていれば、古いメッセージを要約する。"""
        history_tokens = self._count_tokens(self.messages) - self.budget.system_tokens

        if history_tokens <= self.budget.history_budget:
            return

        logger.info(
            "History (%d tokens) exceeds budget (%d tokens) — compressing",
            history_tokens,
            self.budget.history_budget,
        )

        # 分割: 直近のメッセージはそのまま残し、それ以外を要約する
        keep_count = min(RECENT_MESSAGES_TO_KEEP, len(self.messages))
        old_messages = self.messages[:-keep_count] if keep_count > 0 else self.messages
        recent_messages = self.messages[-keep_count:] if keep_count > 0 else []

        if not old_messages:
            logger.warning("No messages to compress — budget may be too small")
            return

        old_tokens = self._count_tokens(old_messages) - self.budget.system_tokens

        # 古いメッセージを要約する
        summary = self._summarize_messages(old_messages)

        # 古いメッセージを要約に置き換える
        summary_message = {
            "role": "user",
            "content": (
                f"[これまでの会話の要約]\n{summary}\n[要約終わり — ここから会話を続けてください]"
            ),
        }

        # ロールが交互になるようにする: 要約（user）の後に直近のメッセージを続ける
        if recent_messages and recent_messages[0]["role"] == "user":
            self.messages = [
                summary_message,
                {"role": "assistant", "content": "了解しました。会話の文脈を把握しています。"},
                *recent_messages,
            ]
        else:
            self.messages = [summary_message, *recent_messages]

        new_tokens = self._count_tokens(self.messages) - self.budget.system_tokens
        self.compression_count += 1

        logger.info(
            "Compressed %d messages: %d → %d tokens (saved %d tokens)",
            len(old_messages),
            old_tokens,
            new_tokens,
            old_tokens - new_tokens,
        )

    def _summarize_messages(self, messages: list[dict[str, Any]]) -> str:
        """ツールとのやり取りを含むメッセージのまとまりをLLMで要約する。"""
        role_labels = {"user": "User", "assistant": "Assistant", "tool": "Tool result"}
        parts = []
        for m in messages:
            label = role_labels.get(m["role"], m["role"])
            content = str(m.get("content") or "")
            if m.get("tool_calls"):
                content = f"{content} [tool_calls: {json.dumps(m['tool_calls'])}]".strip()
            parts.append(f"{label}: {content}")

        transcript = "\n".join(parts)

        response: ChatResult = self.client.chat.send(  # type: ignore[call-overload]
            model=self.model,
            max_tokens=1024,
            reasoning={"effort": "none"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "以下の会話を簡潔に要約してください。重要な事実・データ・"
                        "顧客名・注文ID・ツール結果は保持してください。三人称過去形で"
                        "記述してください。簡潔かつ漏れなくまとめてください。"
                    ),
                },
                {"role": "user", "content": transcript},
            ],
        )

        self.token_tracker.track(response.usage)
        return str(response.choices[0].message.content or "")

    def get_token_snapshot(self) -> TokenSnapshot:
        """可視化用の予算状態。"""
        history_tokens = 0
        if self.messages:
            history_tokens = self._count_tokens(self.messages) - self.budget.system_tokens

        return TokenSnapshot(
            system=self.budget.system_tokens,
            history=history_tokens,
            history_budget=self.budget.history_budget,
            reserve=self.budget.response_reserve,
            message_count=len(self.messages),
            compression_count=self.compression_count,
        )


# --- UI ---


def _render_budget_display(console: Console, snapshot: TokenSnapshot) -> None:
    """コンテキスト予算の可視化を描画する。"""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Component", style="dim")
    table.add_column("Tokens", justify="right")
    table.add_column("Bar", min_width=30)

    usage_ratio = snapshot.history / snapshot.history_budget if snapshot.history_budget > 0 else 0
    bar_width = 25
    filled = int(usage_ratio * bar_width)
    bar_color = "green" if usage_ratio < 0.7 else "yellow" if usage_ratio < 0.9 else "red"
    bar = f"[{bar_color}]{'█' * filled}[/{bar_color}][dim]{'░' * (bar_width - filled)}[/dim]"

    table.add_row("System+Tools", f"[cyan]{snapshot.system:,}[/cyan]", "[dim]fixed[/dim]")
    table.add_row(
        "History",
        f"[{bar_color}]{snapshot.history:,}[/{bar_color}] / {snapshot.history_budget:,}",
        bar,
    )
    table.add_row("Response Reserve", f"[cyan]{snapshot.reserve:,}[/cyan]", "[dim]max_tokens[/dim]")

    footer = f"Messages: {snapshot.message_count}"
    if snapshot.compression_count > 0:
        footer += f" │ Compressions: {snapshot.compression_count}"

    console.print(
        Panel(table, title="Context Budget", subtitle=footer, border_style="dim", padding=(0, 1))
    )


def main() -> None:
    """ツールコンテキストエンジニアリングのデモ用メインオーケストレーション関数。"""
    console = Console()

    # 戦略選択
    strategy_items = [f"{name} — {desc}" for name, desc in STRATEGIES.items()]
    header = Panel(
        "[bold cyan]Tool Output Context Engineering[/bold cyan]\n\n"
        "ツール出力は、エージェントシステムにおいて最もコンテキストを消費する要因です。\n"
        "1回のAPI呼び出しで1000トークン以上のJSONが返ることもあります。\n\n"
        "戦略を選択し、コンテキスト使用量への影響を確認してください:",
        border_style="cyan",
    )

    selected = interactive_menu(
        console,
        items=strategy_items,
        title="Context Strategy",
        header=header,
    )

    if selected is None:
        console.print("[yellow]Exiting.[/yellow]")
        return

    # 選択肢から戦略名を取り出す
    strategy = selected.split(" — ")[0]
    console.clear()

    token_tracker = OpenRouterTokenTracker()
    db = MockDatabaseService(DB_PATH)
    agent = ToolContextAgent(MODEL, strategy, MAX_CONTEXT_TOKENS, token_tracker, db)

    # 戦略ごとのウェルカムメッセージ
    strategy_hints = {
        "naive": (
            "生のツール出力がそのままコンテキストに注入されます。\n"
            "2〜3回のツール呼び出しで予算全体が埋まる様子を見てください！"
        ),
        "truncate": (
            f"ツール出力は{TRUNCATE_MAX_CHARS}文字で打ち切られます。\n"
            "無料ですが、結果の後半にある重要なデータが失われることがあります。"
        ),
        "summarize": (
            "各ツール出力から、注入前にLLMが要点を抽出します。\n"
            "ツール使用のたびに追加のAPI呼び出しが発生しますが、意味は保持されます。"
        ),
    }

    console.print(
        Panel(
            f"[bold cyan]Strategy: {strategy.upper()}[/bold cyan]\n\n"
            f"{strategy_hints[strategy]}\n\n"
            f"Context budget: {MAX_CONTEXT_TOKENS:,} tokens total, "
            f"~{agent.budget.history_budget:,} for history.\n\n"
            "試してみてください: 「顧客のAlice Johnsonを調べて」の後に「彼女の注文履歴を見せて」\n"
            "[bold]'quit'[/bold] または [bold]'exit'[/bold] と入力すると終了します。",
            title="Business Data Agent",
        )
    )

    # 初期の予算を表示
    _render_budget_display(console, agent.get_token_snapshot())

    while True:
        console.print("\n[bold green]You:[/bold green] ", end="")
        user_input = input().strip()

        if user_input.lower() in ["quit", "exit", ""]:
            console.print("\n[yellow]Ending session...[/yellow]")
            break

        try:
            response = agent.chat(user_input)

            console.print("\n[bold blue]Agent:[/bold blue]")
            console.print(Markdown(response))

            # 各ターンの後に予算を表示
            console.print()
            _render_budget_display(console, agent.get_token_snapshot())

        except Exception as e:
            logger.error("Error during chat: %s", e)
            console.print(f"\n[red]Error: {e}[/red]")
            break

    # 最終レポート
    console.print()
    token_tracker.report()
    console.print(
        f"\n[dim]Messages: {len(agent.messages)} │ "
        f"Compressions: {agent.compression_count} │ "
        f"Strategy: {strategy}[/dim]"
    )


if __name__ == "__main__":
    main()
