"""
統合テスト向けの共有テストフィクスチャとカセットの仕組み（OpenRouter）。

記録/再生テスト用のCassetteClient、あらかじめ用意されたカセットデータ、
テストモジュール間で使われるフィクスチャを提供する。
"""

import json
from pathlib import Path
from typing import Any

import pytest
from common import setup_logging

logger = setup_logging(__name__)


# ---------------------------------------------------------------------------
# カセットの仕組み — API応答の記録と再生
# ---------------------------------------------------------------------------


class _ToolCallFunction:
    """OpenRouterのtool_call.functionを模した軽量なオブジェクト。"""

    def __init__(self, name: str, arguments: str) -> None:
        self.name = name
        self.arguments = arguments


class _ToolCall:
    """OpenRouterのtool_callを模した軽量なオブジェクト。"""

    def __init__(self, tool_call_id: str, function: _ToolCallFunction) -> None:
        self.id = tool_call_id
        self.type = "function"
        self.function = function


class _Message:
    """OpenRouterのresponse.choices[0].messageを模した軽量なオブジェクト。"""

    def __init__(self, content: str | None, tool_calls: list[_ToolCall] | None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _Choice:
    """OpenRouterのresponse.choices[0]を模した軽量なオブジェクト。"""

    def __init__(self, message: _Message, finish_reason: str) -> None:
        self.message = message
        self.finish_reason = finish_reason


class _Usage:
    """OpenRouterのresponse.usageを模した軽量なオブジェクト。"""

    def __init__(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class CassetteResponse:
    """OpenRouter APIレスポンスの形状を模した、再構築済みレスポンスオブジェクト。"""

    def __init__(self, data: dict[str, Any]) -> None:
        tool_calls_data = data.get("tool_calls")
        tool_calls = None
        if tool_calls_data:
            tool_calls = [
                _ToolCall(tc["id"], _ToolCallFunction(tc["name"], json.dumps(tc["arguments"])))
                for tc in tool_calls_data
            ]

        message = _Message(data.get("content"), tool_calls)
        choice = _Choice(message, data["finish_reason"])
        self.choices = [choice]

        usage_data = data.get("usage", {})
        self.usage = _Usage(
            usage_data.get("prompt_tokens", 0),
            usage_data.get("completion_tokens", 0),
        )


class CassetteClient:
    """カセットファイルからレスポンスを再生する、偽のOpenRouterクライアント。"""

    def __init__(self, cassette_path: Path) -> None:
        with cassette_path.open(encoding="utf-8") as f:
            self._interactions = json.load(f)
        self._call_index = 0
        self.chat = self

    def send(self, **kwargs: Any) -> CassetteResponse:
        """次に記録されているレスポンスを再生する。"""
        if self._call_index >= len(self._interactions):
            raise RuntimeError(
                f"Cassette exhausted: expected at most {len(self._interactions)} API calls, "
                f"but got call #{self._call_index + 1}. "
                "The agent's behavior has diverged from the recording."
            )
        interaction = self._interactions[self._call_index]
        self._call_index += 1
        logger.info(
            "Replaying cassette response %d/%d",
            self._call_index,
            len(self._interactions),
        )
        return CassetteResponse(interaction["response"])

    @property
    def calls_remaining(self) -> int:
        """再生されずに残っている記録済みレスポンスの数。"""
        return len(self._interactions) - self._call_index


def serialize_response(response: Any) -> dict[str, Any]:
    """OpenRouter APIレスポンスを、記録用のJSONセーフなdictにシリアライズする。"""
    message = response.choices[0].message
    tool_calls = None
    if message.tool_calls:
        tool_calls = [
            {
                "id": tool_call.id,
                "name": tool_call.function.name,
                "arguments": json.loads(tool_call.function.arguments),
            }
            for tool_call in message.tool_calls
        ]
    return {
        "finish_reason": response.choices[0].finish_reason,
        "content": message.content,
        "tool_calls": tool_calls,
        "usage": {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
        },
    }


# ---------------------------------------------------------------------------
# あらかじめ用意されたカセットデータ
# ---------------------------------------------------------------------------

# カセット: シンプルなテキスト応答（ツール使用なし）
CASSETTE_TEXT_ONLY: list[dict[str, Any]] = [
    {
        "response": {
            "finish_reason": "stop",
            "content": "こんにちは！計算のお手伝いをいたします。",
            "tool_calls": None,
            "usage": {"prompt_tokens": 120, "completion_tokens": 15},
        }
    }
]

# カセット: 1回のツール呼び出し（電卓）に続くテキスト応答
CASSETTE_CALCULATOR: list[dict[str, Any]] = [
    {
        "response": {
            "finish_reason": "tool_calls",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_01ABC",
                    "name": "calculator",
                    "arguments": {"operation": "multiply", "a": 12, "b": 15},
                }
            ],
            "usage": {"prompt_tokens": 150, "completion_tokens": 40},
        }
    },
    {
        "response": {
            "finish_reason": "stop",
            "content": "12かける15は180です。",
            "tool_calls": None,
            "usage": {"prompt_tokens": 200, "completion_tokens": 12},
        }
    },
]

# カセット: 複数ターンのツール使用——連続する2回の電卓呼び出し
CASSETTE_MULTI_TOOL: list[dict[str, Any]] = [
    {
        "response": {
            "finish_reason": "tool_calls",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_01STEP1",
                    "name": "calculator",
                    "arguments": {"operation": "add", "a": 100, "b": 200},
                }
            ],
            "usage": {"prompt_tokens": 160, "completion_tokens": 35},
        }
    },
    {
        "response": {
            "finish_reason": "tool_calls",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_01STEP2",
                    "name": "calculator",
                    "arguments": {"operation": "multiply", "a": 300, "b": 2},
                }
            ],
            "usage": {"prompt_tokens": 220, "completion_tokens": 38},
        }
    },
    {
        "response": {
            "finish_reason": "stop",
            "content": "まず100 + 200を計算して300、それを2倍して600になりました。",
            "tool_calls": None,
            "usage": {"prompt_tokens": 280, "completion_tokens": 25},
        }
    },
]

# カセット: ブロックされたコマンド——LLMがrmを要求し、エージェントがブロックし、LLMが謝罪する
CASSETTE_BLOCKED_COMMAND: list[dict[str, Any]] = [
    {
        "response": {
            "finish_reason": "tool_calls",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_01DANGER",
                    "name": "run_bash",
                    "arguments": {"command": "rm -rf /tmp/data"},
                }
            ],
            "usage": {"prompt_tokens": 140, "completion_tokens": 30},
        }
    },
    {
        "response": {
            "finish_reason": "stop",
            "content": "申し訳ありませんが、そのコマンドは安全のためブロックされました。",
            "tool_calls": None,
            "usage": {"prompt_tokens": 210, "completion_tokens": 18},
        }
    },
]


# ---------------------------------------------------------------------------
# フィクスチャ
# ---------------------------------------------------------------------------


@pytest.fixture()
def cassette_dir(tmp_path: Path) -> Path:
    """一時的なカセットディレクトリを作成する。"""
    d = tmp_path / "cassettes"
    d.mkdir()
    return d


def write_cassette(cassette_dir: Path, name: str, data: list[dict[str, Any]]) -> Path:
    """カセットファイルを書き出し、そのパスを返す。"""
    path = cassette_dir / f"{name}.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path
