"""
共有ナレッジベース・ツール定義・システムプロンプト・検索関数（OpenRouter）。

このチュートリアルモジュールの全ベンチマークスクリプトで使われる。
"""

from typing import Any

from common import setup_logging
from openrouter_adapter import tokenize_japanese

logger = setup_logging(__name__)

# ---------------------------------------------------------------------------
# ナレッジベース（共有のリサーチアシスタントコーパス）
# ---------------------------------------------------------------------------

KNOWLEDGE_BASE = [
    {
        "id": "doc_001",
        "title": "マイクロサービスアーキテクチャ",
        "content": (
            "マイクロサービスアーキテクチャは、アプリケーションを小さく独立した"
            "サービスに分解します。各サービスは独自のプロセスで実行され、API経由で"
            "通信し、独立してデプロイできます。利点にはスケーラビリティ、障害分離、"
            "技術選定の柔軟性があります。課題には分散システムの複雑さ、データ整合性、"
            "運用オーバーヘッドが含まれます。"
        ),
        "tags": ["architecture", "microservices", "distributed-systems"],
    },
    {
        "id": "doc_002",
        "title": "REST API設計",
        "content": (
            "REST APIはリソース指向の設計原則に従います。エンドポイントには名詞を"
            "使用し、アクションにはHTTPメソッドを、結果にはステータスコードを"
            "使用します。ベストプラクティスには、バージョニング、コレクションの"
            "ページネーション、一貫したエラーレスポンス形式が含まれます。"
        ),
        "tags": ["api", "rest", "design"],
    },
    {
        "id": "doc_003",
        "title": "データベースインデックス",
        "content": (
            "データベースインデックスは、効率的な検索構造を作ることでクエリの"
            "パフォーマンスを向上させます。B-treeインデックスは等価検索と範囲検索を"
            "処理します。複合インデックスは複数列のクエリをサポートしますが、列の"
            "順序が重要です。過剰なインデックスは書き込みを遅くし、ストレージを"
            "浪費します。"
        ),
        "tags": ["database", "performance", "indexing"],
    },
    {
        "id": "doc_004",
        "title": "認証と認可",
        "content": (
            "認証は本人確認を行い、認可はアクセス制御を行います。JWTトークンは"
            "ステートレスな認証を可能にします。OAuth 2.0は委任アクセスを提供します。"
            "パスワードは必ずbcryptまたはargon2でハッシュ化してください。"
        ),
        "tags": ["security", "authentication", "authorization"],
    },
    {
        "id": "doc_005",
        "title": "CI/CDパイプライン",
        "content": (
            "継続的インテグレーション（CI）は、コミットのたびに自動的にコードを"
            "ビルド・テストします。継続的デプロイ（CD）は、成功したビルドを"
            "自動的にデプロイします。主な実践: 高速なフィードバックループ、"
            "トランクベース開発、フィーチャーフラグ、自動ロールバック。"
        ),
        "tags": ["devops", "ci-cd", "automation"],
    },
    {
        "id": "doc_006",
        "title": "Kubernetesによるコンテナオーケストレーション",
        "content": (
            "Kubernetesはコンテナ化されたワークロードを管理します。中心となる"
            "概念: Pod、Service、Deployment、ConfigMap/Secret。主な機能: "
            "オートスケーリング、自己修復、ローリングアップデート、"
            "サービスディスカバリ。"
        ),
        "tags": ["devops", "kubernetes", "containers"],
    },
    {
        "id": "doc_007",
        "title": "イベント駆動アーキテクチャ",
        "content": (
            "イベント駆動アーキテクチャは、サービス間の通信のトリガーにイベントを"
            "使用します。パターン: イベントソーシング、CQRS、pub/sub。利点: "
            "疎結合、スケーラビリティ、監査証跡。"
        ),
        "tags": ["architecture", "events", "messaging"],
    },
    {
        "id": "doc_008",
        "title": "キャッシュ戦略",
        "content": (
            "キャッシュは、頻繁にアクセスされるデータをメモリに保存することで"
            "レイテンシを削減します。戦略: cache-aside、write-through、"
            "write-behind。分散キャッシュにはRedisやMemcachedを使用してください。"
        ),
        "tags": ["performance", "caching", "redis"],
    },
]

SYSTEM_PROMPT = (
    "あなたはリサーチアシスタントです。ツール経由で提供された検索結果の情報のみを"
    "使って質問に答えてください。回答には必ずドキュメントIDで出典を明記してください。"
    "関連情報が見つからない場合は、その旨を明確に伝えてください。情報を捏造しないで"
    "ください。"
)

# OpenRouter（OpenAI function calling互換）のツール形式。
# 元のコードはAnthropic用とOpenAI用の2つの形式を別々に定義していたが、
# OpenRouterでは全モデルが同じ形式でアクセスできるため1つに統合する。
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": "クエリに一致するドキュメントをナレッジベースから検索します。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "検索クエリ"},
                    "max_results": {
                        "type": "integer",
                        "description": "返すドキュメントの最大数",
                        "default": 3,
                    },
                },
                "required": ["query"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# ベンチマークタスク（正解データセットのサブセット）
# ---------------------------------------------------------------------------

BENCHMARK_TASKS = [
    {
        "id": "bench_001",
        "question": "マイクロサービスアーキテクチャの主な利点は何ですか？",
        "expected_keywords": ["スケーラビリティ", "障害分離", "独立"],
        "category": "architecture",
    },
    {
        "id": "bench_002",
        "question": "REST APIのエンドポイントはどのように設計すべきですか？",
        "expected_keywords": ["名詞", "HTTPメソッド", "ステータスコード"],
        "category": "api",
    },
    {
        "id": "bench_003",
        "question": "データベースインデックスにはどのような戦略がありますか？",
        "expected_keywords": ["B-tree", "複合", "クエリのパフォーマンス"],
        "category": "database",
    },
    {
        "id": "bench_004",
        "question": "認証と認可の違いを説明してください。",
        "expected_keywords": ["本人確認", "アクセス", "JWT", "OAuth"],
        "category": "security",
    },
    {
        "id": "bench_005",
        "question": "CI/CDにおける主な実践は何ですか？",
        "expected_keywords": ["継続的", "自動化", "フィードバック"],
        "category": "devops",
    },
]

# ---------------------------------------------------------------------------
# ナレッジベース検索ユーティリティ
# ---------------------------------------------------------------------------


def search_knowledge_base(query: str, max_results: int = 3) -> list[dict[str, Any]]:
    """キーワードマッチングでナレッジベースを検索する。

    クエリの分かち書きにはjanome（tokenize_japanese）を使う——str.split()による
    単純な空白区切りは、分かち書きのない日本語ではほとんど機能しないため。
    """
    query_words = tokenize_japanese(query, show_progress=False)[0]
    scored: list[tuple[int, dict[str, Any]]] = []
    for doc in KNOWLEDGE_BASE:
        text = f"{doc['title']} {doc['content']} {' '.join(doc['tags'])}"
        score = sum(1 for word in query_words if word in text)
        if score > 0:
            scored.append((score, doc))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [doc for _, doc in scored[:max_results]]


def score_answer(answer: str, expected_keywords: list[str]) -> float:
    """期待されるキーワードのカバー率に基づいて回答を採点する。"""
    answer_lower = answer.lower()
    found = sum(1 for kw in expected_keywords if kw.lower() in answer_lower)
    return found / len(expected_keywords) if expected_keywords else 1.0
