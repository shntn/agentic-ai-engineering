"""
ツール呼び出し付きストリーミングエージェント (OpenRouter)

ストリーミングの難所——ストリームの途中でツール呼び出しを処理する——を実演します。
エージェントはテキストをリアルタイムでストリーミングし、モデルがツールを呼び出そう
としていることを検知し、それを実行し、結果をフィードバックし、ストリーミングを
再開します——その間、滑らかで応答性の高いターミナルUIを維持します。
"""

import ast
import json
import operator
import os
import random
from typing import Any

from dotenv import find_dotenv, load_dotenv
from openrouter import OpenRouter
from openrouter.components import ChatUsage
from openrouter.errors import OpenRouterError
from openrouter.utils.eventstreaming import EventStream
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel

from common import OpenRouterTokenTracker, setup_logging
from openrouter_adapter import to_openrouter_tools

# ルートの.envファイルから環境変数を読み込む
load_dotenv(find_dotenv())

# ロギングを設定
logger = setup_logging(__name__)

MODEL = "deepseek/deepseek-v4-flash-0731"

SYSTEM_PROMPT = (
    "あなたはツールを使えるアシスタントです。"
    "正確な回答が得られる場合はツールを使ってください——天気や計算を推測しないでください。"
    "ツールの結果を受け取ったら、それを応答に自然に組み込んでください。"
    "応答は簡潔にし、Markdown書式を使用してください。"
)

# --- ツール定義 ---

TOOLS = [
    {
        "name": "get_weather",
        "description": "指定した都市の現在の天気を取得する。気温・天候・湿度を返す。",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {
                    "type": "string",
                    "description": "都市名（例: 'San Francisco', 'Tokyo', 'London'）",
                },
            },
            "required": ["city"],
        },
    },
    {
        "name": "calculate",
        "description": (
            "数式を安全に評価する。四則演算子（+, -, *, /, **, %）、括弧、"
            "一般的な関数をサポートする。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "評価する数式（例: '(15 * 3) + 42'）",
                },
            },
            "required": ["expression"],
        },
    },
]


# --- ツールの実装 ---

# 模擬的な天気データ——APIキー不要で、チュートリアルを自己完結させる
WEATHER_DATA: dict[str, dict[str, Any]] = {
    "san francisco": {"temp_f": 62, "conditions": "Foggy", "humidity": 78},
    "new york": {"temp_f": 45, "conditions": "Partly cloudy", "humidity": 55},
    "tokyo": {"temp_f": 58, "conditions": "Clear", "humidity": 42},
    "london": {"temp_f": 48, "conditions": "Overcast", "humidity": 82},
    "paris": {"temp_f": 52, "conditions": "Light rain", "humidity": 75},
    "sydney": {"temp_f": 77, "conditions": "Sunny", "humidity": 60},
}


def get_weather(city: str) -> dict[str, Any]:
    """それらしいデータを使った模擬的な天気検索。"""
    key = city.lower().strip()
    if key in WEATHER_DATA:
        data = WEATHER_DATA[key]
    else:
        # 未知の都市に対してもっともらしい天気を生成する
        data = {
            "temp_f": random.randint(35, 85),
            "conditions": random.choice(["Clear", "Cloudy", "Partly cloudy", "Light rain"]),
            "humidity": random.randint(30, 90),
        }

    logger.info("Weather lookup: %s → %s", city, data["conditions"])
    return {"city": city, **data}


# 数式評価のための安全な演算子
SAFE_OPERATORS: dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval(node: ast.AST) -> float:
    """安全な演算子のみを使ってASTノードを再帰的に評価する。"""
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    elif isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        return float(node.value)
    elif isinstance(node, ast.BinOp) and type(node.op) in SAFE_OPERATORS:
        left = _safe_eval(node.left)
        right = _safe_eval(node.right)
        result: float = SAFE_OPERATORS[type(node.op)](left, right)
        return result
    elif isinstance(node, ast.UnaryOp) and type(node.op) in SAFE_OPERATORS:
        result_u: float = SAFE_OPERATORS[type(node.op)](_safe_eval(node.operand))
        return result_u
    raise ValueError(f"Unsupported expression: {ast.dump(node)}")


def calculate(expression: str) -> dict[str, Any]:
    """安全な数式評価器——eval()を使わず、ASTパースによる四則演算のみ。"""
    try:
        tree = ast.parse(expression, mode="eval")
        result = _safe_eval(tree)
        logger.info("Calculate: %s = %s", expression, result)
        return {"expression": expression, "result": result}
    except (ValueError, TypeError, ZeroDivisionError, SyntaxError) as e:
        logger.error("Calculation error: %s — %s", expression, e)
        return {"expression": expression, "error": str(e)}


TOOL_FUNCTIONS: dict[str, Any] = {
    "get_weather": get_weather,
    "calculate": calculate,
}


def execute_tool(name: str, tool_input: dict[str, Any]) -> str:
    """ツールを実行し、JSON文字列の結果を返す。"""
    if name not in TOOL_FUNCTIONS:
        return json.dumps({"error": f"Unknown tool: {name}"})

    try:
        result = TOOL_FUNCTIONS[name](**tool_input)
        return json.dumps(result)
    except Exception as e:
        logger.error("Tool execution error (%s): %s", name, e)
        return json.dumps({"error": str(e)})


# --- ストリーミングエージェント ---


class StreamingAgent:
    """応答をストリーミングし、ストリームの途中でツール呼び出しを処理するエージェント。

    最大の難所: 1回のAPI応答に、テキストブロックとツール呼び出しの両方が
    入り混じって含まれうるということです。このエージェントはテキストをリアルタイムで
    ターミナルにストリーミングし、届いたツール呼び出しを検知して実行し、モデルの
    後続応答へループバックします。
    """

    def __init__(self, model: str, token_tracker: OpenRouterTokenTracker) -> None:
        self.client = OpenRouter(api_key=os.getenv("OPENROUTER_API_KEY", ""))
        self.model = model
        self.token_tracker = token_tracker
        self.messages: list[dict[str, Any]] = []

    def run(self, user_input: str, console: Console) -> str:
        """エージェントループ全体を実行する: ストリーミング → ツール検知 → 実行 → 再開。

        このループは、モデルがfinish_reason="stop"を返す——つまりもう呼び出す
        ツールがない——まで続きます。
        """
        self.messages.append({"role": "user", "content": user_input})
        full_response_text = ""
        iteration = 0
        max_iterations = 10  # 無限ループを防ぐための安全装置

        while iteration < max_iterations:
            iteration += 1
            logger.info("Agent loop iteration %d", iteration)

            # 応答をストリーミングし、テキストを描画しつつツール呼び出しを検知する
            text, pending_tool_calls, finish_reason, usage = self._stream_response(console)
            full_response_text += text

            if usage is not None:
                self.token_tracker.track(usage)

            # アシスタントの応答全体を会話履歴に追加する
            if pending_tool_calls:
                tool_calls_msg = [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {"name": call["name"], "arguments": call["arguments"]},
                    }
                    for _, call in sorted(pending_tool_calls.items())
                ]
                self.messages.append(
                    {"role": "assistant", "content": text or None, "tool_calls": tool_calls_msg}
                )
            else:
                self.messages.append({"role": "assistant", "content": text})

            # ツール呼び出しがなければ完了
            if finish_reason != "tool_calls":
                logger.info("Agent complete (finish_reason: %s)", finish_reason)
                break

            # ツール呼び出しを実行し、結果をフィードバックする
            tool_results = self._execute_tool_calls(pending_tool_calls, console)
            self.messages.extend(tool_results)

            # 次のイテレーションでモデルの後続応答をストリーミングする

        return full_response_text

    def _stream_response(
        self, console: Console
    ) -> tuple[str, dict[int, dict[str, Any]], str | None, ChatUsage | None]:
        """1回のAPI呼び出しをストリーミングし、テキストとツール呼び出しをリアルタイムで描画する。

        (蓄積されたテキスト, ツール呼び出しの蓄積, finish_reason, usage) を返す。
        """
        logger.info("Calling %s (stream)", self.model)
        response: EventStream = self.client.chat.send(  # type: ignore[call-overload]
            model=self.model,
            max_tokens=2048,
            reasoning={"effort": "none"},
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, *self.messages],
            tools=to_openrouter_tools(TOOLS),
            tool_choice="auto",
            stream=True,
        )
        with response as stream:
            return self._render_stream(stream, console)

    def _render_stream(
        self, stream: EventStream, console: Console
    ) -> tuple[str, dict[int, dict[str, Any]], str | None, ChatUsage | None]:
        """テキストとツール呼び出しが混在するストリームを描画する。

        ツール呼び出し付きのストリーミングを機能させる核心部分:
        - テキストの差分 → markdownとしてリアルタイム描画
        - tool_callsの差分 → indexごとに name/id/arguments断片を蓄積
        - usage → 最後のチャンクにのみ入るため、届いた時点で保持しておく
        """
        accumulated_text = ""
        pending_tool_calls: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        usage: ChatUsage | None = None
        live: Live | None = None

        try:
            for chunk in stream:
                choice = chunk.choices[0]
                delta = choice.delta

                if delta.content:
                    # テキストが届いた——ライブ表示を更新する
                    if live is None:
                        live = Live(Markdown(""), refresh_per_second=15, console=console)
                        live.start()
                    accumulated_text += delta.content
                    live.update(Markdown(accumulated_text))

                if delta.tool_calls:
                    # ツール呼び出しが始まった——テキストのライブ表示を終了する
                    if live is not None:
                        live.stop()
                        live = None

                    for tc in delta.tool_calls:
                        entry = pending_tool_calls.setdefault(
                            tc.index, {"id": None, "name": None, "arguments": ""}
                        )
                        if tc.id:
                            entry["id"] = tc.id
                        if tc.function.name:
                            # 呼び出し名が届いたタイミングで、何が呼ばれているかを表示する
                            entry["name"] = tc.function.name
                            console.print(
                                f"\n[dim]  ⚡ Calling [bold]{tc.function.name}[/bold]...[/dim]"
                            )
                        if tc.function.arguments:
                            # ツール入力パラメータが断片的にストリーミングされてくる
                            entry["arguments"] += tc.function.arguments

                if choice.finish_reason:
                    finish_reason = choice.finish_reason

                if chunk.usage is not None:
                    usage = chunk.usage

        finally:
            # ストリームの途中でエラーが起きてもライブ表示を確実に停止する
            if live is not None:
                live.stop()

        if pending_tool_calls:
            console.print()  # 「Calling...」メッセージの後に改行を入れる

        return accumulated_text, pending_tool_calls, finish_reason, usage

    def _execute_tool_calls(
        self, pending_tool_calls: dict[int, dict[str, Any]], console: Console
    ) -> list[dict[str, Any]]:
        """蓄積されたツール呼び出しをすべて実行し、APIに返す形式に整形する。"""
        tool_messages: list[dict[str, Any]] = []

        for index in sorted(pending_tool_calls):
            call = pending_tool_calls[index]
            tool_input = json.loads(call["arguments"]) if call["arguments"] else {}

            logger.info("Executing tool: %s(%s)", call["name"], json.dumps(tool_input))
            console.print(
                f"[dim]  → {call['name']}({json.dumps(tool_input, separators=(',', ':'))})[/dim]"
            )

            result = execute_tool(call["name"], tool_input)
            console.print(
                f"[dim]  ✓ Result: {result[:100]}{'...' if len(result) > 100 else ''}[/dim]"
            )

            tool_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": result,
                }
            )

        return tool_messages

    def reset(self) -> None:
        """会話履歴をクリアする。"""
        self.messages.clear()
        logger.info("Conversation history cleared")


def main() -> None:
    """ツール呼び出し付きの対話型ストリーミングエージェント。"""
    console = Console()
    token_tracker = OpenRouterTokenTracker()
    agent = StreamingAgent(MODEL, token_tracker)

    console.print(
        Panel(
            "[bold cyan]Streaming Agent with Tools[/bold cyan]\n\n"
            "応答がリアルタイムでストリーミングされる様子を見てください——応答の途中で"
            "ツールが呼ばれる場合も含めて。\n\n"
            "[bold]利用可能なツール:[/bold]\n"
            "  🌤️  [green]get_weather[/green] — 任意の都市の現在の天気\n"
            "  🔢 [green]calculate[/green]    — 数式を評価\n\n"
            "[bold]試してみてください:[/bold]\n"
            '  • "サンフランシスコの天気はどうですか？"\n'
            '  • "複利を計算してください: 10000 * (1 + 0.05) ** 10"\n'
            '  • "東京とロンドンの天気を比較して、気温差を計算してください"\n\n'
            "[bold]clear[/bold] と入力するとリセット、[bold]quit[/bold] と入力すると終了します。",
            title="02-streaming / 02 — Streaming Agent",
        )
    )

    while True:
        console.print("\n[bold green]You:[/bold green] ", end="")
        try:
            user_input = input().strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Interrupted.[/yellow]")
            break

        if not user_input or user_input.lower() in ("quit", "exit"):
            console.print("[yellow]Ending session...[/yellow]")
            break

        if user_input.lower() == "clear":
            agent.reset()
            console.print("[dim]Conversation cleared.[/dim]")
            continue

        try:
            console.print("\n[bold blue]Assistant:[/bold blue]")
            agent.run(user_input, console)
        except OpenRouterError as e:
            logger.error("API error: %s", e)
            console.print(f"\n[red]API error: {e}[/red]")

    console.print()
    token_tracker.report()
    console.print(f"[dim]Messages exchanged: {len(agent.messages)}[/dim]")


if __name__ == "__main__":
    main()
