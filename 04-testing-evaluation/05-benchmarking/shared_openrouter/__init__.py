"""ベンチマークチュートリアル向けの共有モジュール（OpenRouter）。"""

from shared_openrouter.knowledge_base import (
    BENCHMARK_TASKS,
    KNOWLEDGE_BASE,
    SYSTEM_PROMPT,
    TOOLS,
    score_answer,
    search_knowledge_base,
)
from shared_openrouter.models import BenchmarkConfig, BenchmarkResult, MODEL_CONFIGS, ModelConfig

__all__ = [
    "BENCHMARK_TASKS",
    "KNOWLEDGE_BASE",
    "MODEL_CONFIGS",
    "SYSTEM_PROMPT",
    "TOOLS",
    "BenchmarkConfig",
    "BenchmarkResult",
    "ModelConfig",
    "score_answer",
    "search_knowledge_base",
]
