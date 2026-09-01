"""
評価フレームワークチュートリアル向けの共有ナレッジベースとリサーチアシスタント（OpenRouter）。

外部API呼び出しなしでフレームワーク固有の評価パターンに集中できるよう、
シミュレートされた応答を持つシンプルなリサーチアシスタントを提供する。
"""

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
            "技術選定の柔軟性があります。"
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
]

# 評価データセット: 期待される回答とメタデータを持つ質問群
EVAL_TASKS: list[dict[str, Any]] = [
    {
        "id": "task_001",
        "question": "マイクロサービスアーキテクチャの主な利点は何ですか？",
        "reference_answer": (
            "主な利点には、スケーラビリティ、障害分離、サービスの独立した"
            "デプロイが含まれます（doc_001）。"
        ),
        "expected_keywords": ["スケーラビリティ", "障害分離", "独立"],
        "expected_source_ids": ["doc_001"],
    },
    {
        "id": "task_002",
        "question": "REST API設計のベストプラクティスは何ですか？",
        "reference_answer": (
            "エンドポイントには名詞を使用し、アクションにはHTTPメソッドを、"
            "適切なステータスコード、バージョニング、ページネーションを"
            "使用します（doc_002）。"
        ),
        "expected_keywords": ["名詞", "エンドポイント", "HTTPメソッド", "ステータスコード"],
        "expected_source_ids": ["doc_002"],
    },
    {
        "id": "task_003",
        "question": "データベースインデックスはどのようにクエリのパフォーマンスを向上させますか？",
        "reference_answer": (
            "インデックスはB-tree構造を使って効率的に検索し、フルテーブル"
            "スキャンを減らしてクエリを高速化します（doc_003）。"
        ),
        "expected_keywords": ["B-tree", "検索", "クエリ"],
        "expected_source_ids": ["doc_003"],
    },
    {
        "id": "task_004",
        "question": "認証と認可の違いは何ですか？",
        "reference_answer": (
            "認証は本人確認を行い、認可はアクセス権限を制御します。JWTを"
            "使用し、パスワードはbcryptでハッシュ化してください（doc_004）。"
        ),
        "expected_keywords": ["本人確認", "アクセス", "認証", "認可"],
        "expected_source_ids": ["doc_004"],
    },
    {
        "id": "task_005",
        "question": "機械学習に最適なプログラミング言語は何ですか？",
        "reference_answer": (
            "ナレッジベースには機械学習のプログラミング言語に関する情報は含まれていません。"
        ),
        "expected_keywords": [],
        "expected_source_ids": [],
    },
]

# シミュレートされたエージェント応答（全フレームワークチュートリアルで使用）
SIMULATED_RESPONSES: dict[str, dict[str, Any]] = {
    "task_001": {
        "answer": (
            "doc_001によると、マイクロサービスアーキテクチャの主な利点は"
            "スケーラビリティ、障害分離、サービスを独立してデプロイできる"
            "ことです。"
        ),
        "tool_calls": [{"name": "search_knowledge_base", "input": {"query": "マイクロサービス"}}],
        "sources": ["doc_001"],
    },
    "task_002": {
        "answer": (
            "doc_002によると、REST APIのベストプラクティスには、"
            "エンドポイントに名詞を使うこと、アクションにHTTPメソッドを"
            "使うこと、適切なステータスコード、バージョニング、"
            "ページネーションが含まれます。"
        ),
        "tool_calls": [{"name": "search_knowledge_base", "input": {"query": "REST API"}}],
        "sources": ["doc_002"],
    },
    "task_003": {
        "answer": (
            "doc_003によると、データベースインデックスは効率的な"
            "B-tree検索構造によって、等価検索・範囲検索のクエリ"
            "パフォーマンスを向上させます。"
        ),
        "tool_calls": [
            {"name": "search_knowledge_base", "input": {"query": "データベースインデックス"}}
        ],
        "sources": ["doc_003"],
    },
    "task_004": {
        "answer": (
            "認証は本人確認を行い、認可はアクセスを制御します。JWTトークンは"
            "ステートレスな認証を提供します。パスワードはbcryptでハッシュ化"
            "してください（doc_004）。"
        ),
        "tool_calls": [{"name": "search_knowledge_base", "input": {"query": "認証"}}],
        "sources": ["doc_004"],
    },
    "task_005": {
        "answer": (
            "ナレッジベース内に機械学習のプログラミング言語に関する情報は見つかりませんでした。"
        ),
        "tool_calls": [{"name": "search_knowledge_base", "input": {"query": "機械学習"}}],
        "sources": [],
    },
}


def get_agent_response(task_id: str) -> dict[str, Any]:
    """指定されたタスクIDに対するシミュレートされたエージェント応答を返す。"""
    return SIMULATED_RESPONSES.get(
        task_id,
        {"answer": "応答がありません。", "tool_calls": [], "sources": []},
    )


def search_knowledge_base(query: str, max_results: int = 3) -> list[dict[str, Any]]:
    """キーワードマッチングでナレッジベースを検索する。

    クエリの分かち書きにはjanome（tokenize_japanese）を使う——str.split()による
    単純な空白区切りは、分かち書きのない日本語ではほとんど機能しないため。
    """
    query_words = tokenize_japanese(query, show_progress=False)[0]
    scored: list[tuple[int, dict[str, Any]]] = []
    for doc in KNOWLEDGE_BASE:
        searchable = f"{doc['title']} {doc['content']} {' '.join(doc['tags'])}"
        score = sum(1 for word in query_words if word in searchable)
        if score > 0:
            scored.append((score, {"id": doc["id"], "title": doc["title"], "score": score}))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in scored[:max_results]]
