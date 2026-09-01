"""評価フレームワークチュートリアル向けの共有モジュール（OpenRouter）。"""

from shared_openrouter.knowledge_base import (
    EVAL_TASKS,
    KNOWLEDGE_BASE,
    SIMULATED_RESPONSES,
    get_agent_response,
    search_knowledge_base,
)

__all__ = [
    "EVAL_TASKS",
    "KNOWLEDGE_BASE",
    "SIMULATED_RESPONSES",
    "get_agent_response",
    "search_knowledge_base",
]
