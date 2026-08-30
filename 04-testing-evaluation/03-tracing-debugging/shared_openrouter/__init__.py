"""トレーシング・デバッグチュートリアル向けの共有モジュール（OpenRouter）。"""

from shared_openrouter.agent import TracedResearchAssistant
from shared_openrouter.knowledge_base import (
    KNOWLEDGE_BASE,
    SYSTEM_PROMPT,
    TOOL_FUNCTIONS,
    TOOLS,
    execute_tool,
    get_document,
    search_knowledge_base,
)
from shared_openrouter.tracer import Span, TraceCollector, collect_all_spans

__all__ = [
    "KNOWLEDGE_BASE",
    "SYSTEM_PROMPT",
    "Span",
    "TOOL_FUNCTIONS",
    "TOOLS",
    "TraceCollector",
    "TracedResearchAssistant",
    "collect_all_spans",
    "execute_tool",
    "get_document",
    "search_knowledge_base",
]
