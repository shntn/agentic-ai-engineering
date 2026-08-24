"""
ツールスキーマ、Web検索の設定、ツール実行機。

構造化されたJSON応答を強制するために、OpenRouterのtool_choiceを使用する——
チュートリアル03（ルーティング）、05（オーケストレーター）、06（評価者）と同じ技法。
サーバー側のweb_searchツールがリアルタイムのリサーチを担当する。
"""

import json
from typing import Any

from common import setup_logging

logger = setup_logging(__name__)

# ─── Web検索 ──────────────────────────────────────────────────────────────────

# OpenRouterのweb_searchサーバーツール — サーバー側で実行され、モデルが検索するかどうかを判断する。
# max_uses でフェーズごとの検索回数を制限し、トークンコストを抑える
WEB_SEARCH_TOOL: dict = {
    "type": "openrouter:web_search",
    "parameters": {"max_uses": 1},
}

# OpenRouterのweb_searchサーバーツールは、検索の完了まで数十秒かかることがある。
# SDKのデフォルトタイムアウトはこれより短く、正常な応答でも ReadTimeout →
# 自動リトライ → 再度 ReadTimeout... のループに陥り、体感で10分以上かかることがある。
# そのため、Web検索を使うステップでも安全に完了できるよう、余裕を持った値を明示する。
REQUEST_TIMEOUT_MS = 120_000  # 120秒

# ─── 分類 ──────────────────────────────────────────────────────────────────────

CLASSIFY_TOOLS: list[dict] = [
    {
        "name": "classify_content",
        "description": "コンテンツリクエストをタイプ・トピック・主要な側面に分類する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "content_type": {
                    "type": "string",
                    "enum": ["blog", "tutorial", "concept"],
                    "description": (
                        "blog: 意見記事、経験の共有、学んだ教訓。"
                        "tutorial: ステップバイステップのガイド、ハウツーコンテンツ。"
                        "concept: アイデア・パターン・技術の深い解説。"
                    ),
                },
                "topic": {
                    "type": "string",
                    "description": "数語で表した中核となる主題",
                },
                "key_aspects": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "取り上げるべき2〜3個の具体的な角度",
                },
                "reasoning": {
                    "type": "string",
                    "description": "分類を選んだ理由の簡潔な説明",
                },
            },
            "required": ["content_type", "topic", "key_aspects", "reasoning"],
        },
    }
]

# ─── 計画 ────────────────────────────────────────────────────────────────────

PLANNING_TOOLS: list[dict] = [
    {
        "name": "create_research_plan",
        "description": "トピックを焦点を絞ったリサーチサブトピックに分解する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "subtopics": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "research_prompt": {
                                "type": "string",
                                "description": "焦点を絞ったリサーチ質問",
                            },
                        },
                        "required": ["title", "research_prompt"],
                    },
                    "description": "並行して調査する2〜3個のサブトピック",
                },
            },
            "required": ["subtopics"],
        },
    }
]

# ─── 評価 ──────────────────────────────────────────────────────────────────────

EVALUATION_TOOLS: list[dict] = [
    {
        "name": "evaluate_draft",
        "description": "下書きを複数の品質観点で評価する。",
        "input_schema": {
            "type": "object",
            "properties": {
                "clarity": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                },
                "technical_accuracy": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                },
                "structure": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                },
                "engagement": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                },
                "human_voice": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "実在の人間が書いたような文章に聞こえるか？",
                },
                "issues": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "見つかった具体的な問題点",
                },
                "suggestions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "実行可能な改善提案",
                },
            },
            "required": [
                "clarity",
                "technical_accuracy",
                "structure",
                "engagement",
                "human_voice",
                "issues",
                "suggestions",
            ],
        },
    }
]

# ─── SEOタイトル評価 ────────────────────────────────────────────────────────────

SEO_EVALUATION_TOOLS: list[dict] = [
    {
        "name": "pick_best_title",
        "description": "SEOタイトル候補を評価し、最も良いものを選ぶ。",
        "input_schema": {
            "type": "object",
            "properties": {
                "winning_index": {
                    "type": "integer",
                    "description": "最も良いタイトルの0始まりのインデックス",
                },
                "winning_title": {
                    "type": "string",
                    "description": "選ばれた最良のタイトル",
                },
                "reasoning": {
                    "type": "string",
                    "description": "このタイトルが最良のSEO選択である理由",
                },
            },
            "required": ["winning_index", "winning_title", "reasoning"],
        },
    }
]


# ─── ツール実行機 ──────────────────────────────────────────────────────────────


class ToolExecutor:
    """ユーザー定義のツールを実行する。サーバー側のツール（web_search）はOpenRouterが処理する。"""

    def execute(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        """ツール呼び出しをディスパッチする。カスタムツールはここに追加する。"""
        logger.warning("Unknown tool: %s", tool_name)
        return json.dumps({"error": f"Unknown tool: {tool_name}"})
