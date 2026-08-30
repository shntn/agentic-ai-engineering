"""レッドチーミング・安全性チュートリアル向けの共有モジュール（OpenRouter）。"""

from shared_openrouter.agent import (
    ATTACK_CATEGORIES,
    BLOCKED_COMMANDS,
    CODING_AGENT_SYSTEM_PROMPT,
    CODING_TOOLS,
    SAFETY_POLICY,
    SENSITIVE_PATHS,
)

__all__ = [
    "ATTACK_CATEGORIES",
    "BLOCKED_COMMANDS",
    "CODING_AGENT_SYSTEM_PROMPT",
    "CODING_TOOLS",
    "SAFETY_POLICY",
    "SENSITIVE_PATHS",
]
