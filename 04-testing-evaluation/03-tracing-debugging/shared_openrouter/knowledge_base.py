"""
共有ナレッジベース・ツール定義・ツール実行ロジック（OpenRouter）。

トレーシングチュートリアル全体で、トレース対象のリサーチアシスタントから
使われる。
"""

from collections.abc import Callable
from typing import Any

from common import setup_logging
from openrouter_adapter import tokenize_japanese

logger = setup_logging(__name__)

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
            "浪費します。EXPLAINを使ってクエリプランを分析してください。"
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
            "レート制限とアカウントロックアウトを実装してください。"
        ),
        "tags": ["security", "authentication", "authorization"],
    },
    {
        "id": "doc_005",
        "title": "CI/CDパイプライン",
        "content": (
            "CIは、コミットのたびに自動的にコードをビルド・テストします。CDは、"
            "成功したビルドを自動的にデプロイします。主な実践: 高速なフィードバック"
            "ループ、トランクベース開発、フィーチャーフラグ、自動ロールバック。"
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
            "疎結合、スケーラビリティ、監査証跡。課題: 結果整合性、イベントの順序。"
        ),
        "tags": ["architecture", "events", "messaging"],
    },
    {
        "id": "doc_008",
        "title": "キャッシュ戦略",
        "content": (
            "キャッシュは、頻繁にアクセスされるデータをメモリに保存することで"
            "レイテンシを削減します。戦略: cache-aside、write-through、"
            "write-behind。RedisやMemcachedを使用してください。適切なTTLを設定し、"
            "キャッシュの無効化を実装してください。"
        ),
        "tags": ["performance", "caching", "redis"],
    },
]

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
                        "description": "最大結果数",
                        "default": 3,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_document",
            "description": "指定したIDのドキュメントを取得します。",
            "parameters": {
                "type": "object",
                "properties": {"doc_id": {"type": "string", "description": "ドキュメントID"}},
                "required": ["doc_id"],
            },
        },
    },
]

SYSTEM_PROMPT = (
    "あなたはリサーチアシスタントです。回答する前にsearch_knowledge_baseと"
    "get_documentツールを使って情報を探してください。回答は必ず見つかった"
    "ドキュメントに基づいてください。関連するドキュメントが見つからない場合は、"
    "その旨を伝えてください。"
)


def search_knowledge_base(query: str, max_results: int = 3) -> list[dict[str, Any]]:
    """クエリの語をタイトル・本文・タグと照合してナレッジベースを検索する。

    クエリの分かち書きにはjanome（tokenize_japanese）を使う——str.split()による
    単純な空白区切りは、分かち書きのない日本語ではほとんど機能しないため。
    """
    query_terms = tokenize_japanese(query, show_progress=False)[0]
    scored: list[tuple[float, dict[str, Any]]] = []
    for doc in KNOWLEDGE_BASE:
        searchable = f"{doc['title']} {doc['content']} {' '.join(doc['tags'])}"
        score = sum(1 for term in query_terms if term in searchable)
        if score > 0:
            scored.append((score, {"id": doc["id"], "title": doc["title"], "score": score}))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in scored[:max_results]]


def get_document(doc_id: str) -> dict[str, Any]:
    """IDでドキュメントを取得する。"""
    for doc in KNOWLEDGE_BASE:
        if doc["id"] == doc_id:
            return doc
    return {"error": f"Document not found: {doc_id}"}


TOOL_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "search_knowledge_base": search_knowledge_base,
    "get_document": get_document,
}


def execute_tool(tool_name: str, tool_input: dict[str, Any]) -> Any:
    """ツールを実行し、その結果を返す。"""
    if tool_name not in TOOL_FUNCTIONS:
        return {"error": f"Unknown tool: {tool_name}"}
    try:
        return TOOL_FUNCTIONS[tool_name](**tool_input)
    except Exception as e:
        logger.error("Tool execution error: %s", e)
        return {"error": str(e)}
