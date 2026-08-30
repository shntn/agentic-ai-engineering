"""
共有トレーシングの基本要素: SpanデータクラスとTraceCollector。

すべてのトレーシングチュートリアルで使われる、スパンベースのトレーシング
基盤を提供する。
"""

import json
import time
import uuid
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from common import setup_logging

logger = setup_logging(__name__)


@dataclass
class Span:
    """トレース対象となる1つの操作。"""

    name: str
    span_type: str  # "llm_call"、"tool_call"、"agent_step"、"search"
    start_time: float
    end_time: float | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    children: list["Span"] = field(default_factory=list)
    tokens: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    @property
    def duration_ms(self) -> float:
        """所要時間（ミリ秒）。"""
        if self.end_time is None:
            return 0.0
        return (self.end_time - self.start_time) * 1000

    def to_dict(self) -> dict[str, Any]:
        """スパンをシリアライズ可能な辞書に変換する。"""
        return {
            "name": self.name,
            "span_type": self.span_type,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": round(self.duration_ms, 2),
            "inputs": self.inputs,
            "outputs": self.outputs,
            "metadata": self.metadata,
            "tokens": self.tokens,
            "error": self.error,
            "children": [child.to_dict() for child in self.children],
        }


class TraceCollector:
    """階層的なスパンで実行トレースを収集する。"""

    def __init__(self) -> None:
        self.trace_id: str = str(uuid.uuid4())[:8]
        self.root_spans: list[Span] = []
        self._span_stack: list[Span] = []

    @contextmanager
    def span(
        self, name: str, span_type: str, inputs: dict[str, Any] | None = None
    ) -> Generator[Span, None, None]:
        """トレース対象のスパンを作成するコンテキストマネージャー。"""
        new_span = Span(
            name=name,
            span_type=span_type,
            start_time=time.time(),
            inputs=inputs or {},
        )

        # 現在の親の下にネストするか、ルートとして追加する
        if self._span_stack:
            self._span_stack[-1].children.append(new_span)
        else:
            self.root_spans.append(new_span)

        self._span_stack.append(new_span)
        try:
            yield new_span
        except Exception as e:
            new_span.error = str(e)
            raise
        finally:
            new_span.end_time = time.time()
            self._span_stack.pop()

    def traced(self, name: str, span_type: str) -> Callable:
        """関数呼び出しをトレースするためのデコレーター。"""

        def decorator(func: Callable) -> Callable:
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                with self.span(name, span_type, inputs={"args": str(args), **kwargs}) as s:
                    result = func(*args, **kwargs)
                    s.outputs = {"result": str(result)[:200]}
                    return result

            return wrapper

        return decorator

    def to_dict(self) -> dict[str, Any]:
        """トレースをシリアライズ可能な辞書としてエクスポートする。"""
        return {
            "trace_id": self.trace_id,
            "spans": [span.to_dict() for span in self.root_spans],
        }

    def save(self, path: str) -> None:
        """トレースをJSONファイルに保存する。"""
        from pathlib import Path

        with Path(path).open("w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, default=str, ensure_ascii=False)
        logger.info("Trace saved to %s", path)


def collect_all_spans(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """スパンツリーを（深さ優先で）フラットなリストに展開する。"""
    result: list[dict[str, Any]] = []
    for span in spans:
        result.append(span)
        result.extend(collect_all_spans(span.get("children", [])))
    return result
