"""評価ハーネス向けの軽量トレースコレクター（OpenRouter）。"""

import logging
import time

from eval_harness_openrouter.models import TraceSpan

logger = logging.getLogger(__name__)


class SimpleTracer:
    """評価実行中にスパンを記録する軽量なトレースコレクター。"""

    def __init__(self) -> None:
        self._spans: list[TraceSpan] = []
        self._active_spans: list[TraceSpan] = []

    def start_span(self, name: str, span_type: str) -> TraceSpan:
        """新しいトレーススパンを開始して返す。"""
        span = TraceSpan(
            name=name,
            span_type=span_type,
            start_time=time.time(),
        )

        # 現在アクティブなスパンがあれば、その下にネストする
        if self._active_spans:
            self._active_spans[-1].children.append(span)
        else:
            self._spans.append(span)

        self._active_spans.append(span)
        logger.debug("Span started: %s (%s)", name, span_type)
        return span

    def end_span(self, span: TraceSpan) -> None:
        """終了時刻を記録してトレーススパンを終了する。"""
        span.end_time = time.time()

        if self._active_spans and self._active_spans[-1] is span:
            self._active_spans.pop()

        logger.debug("Span ended: %s (%.1fms)", span.name, span.duration_ms)

    def get_spans(self) -> list[TraceSpan]:
        """収集されたすべてのルートレベルのスパンを返す。"""
        return list(self._spans)

    def reset(self) -> None:
        """収集済みのすべてのスパンをクリアする。"""
        self._spans.clear()
        self._active_spans.clear()

    def get_total_duration_ms(self) -> float:
        """すべてのルートスパンの所要時間を合計する。"""
        return sum(s.duration_ms for s in self._spans)

    def get_span_count(self) -> int:
        """ネストした子スパンを含む全スパン数を数える。"""
        count = 0

        def _count(spans: list[TraceSpan]) -> None:
            nonlocal count
            for span in spans:
                count += 1
                _count(span.children)

        _count(self._spans)
        return count
