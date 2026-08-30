"""
共有ナレッジベース・ツール定義・検索機能（OpenRouter）。

すべてのevalチュートリアルスクリプトで使われるリサーチコーパス・
システムプロンプト・OpenRouter（function calling）ツールスキーマを提供する。
"""

from typing import Any

from common import setup_logging
from openrouter_adapter import tokenize_japanese

logger = setup_logging(__name__)

# ---------------------------------------------------------------------------
# ナレッジベースのコーパス
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
            "使用し（例: /users、/orders）、アクションにはHTTPメソッド（GET、POST、"
            "PUT、DELETE）を使用し、結果にはステータスコードを使用します。"
            "ベストプラクティスには、バージョニング（例: /v1/）、コレクションの"
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
            "浪費します。EXPLAINを使ってクエリプランを分析し、不足しているインデック"
            "スを特定してください。"
        ),
        "tags": ["database", "performance", "indexing"],
    },
    {
        "id": "doc_004",
        "title": "認証と認可",
        "content": (
            "認証は本人確認（あなたが誰か）を行い、認可はアクセス制御（何ができる"
            "か）を行います。JWTトークンはクレームベースの認可を伴うステートレスな"
            "認証を可能にします。OAuth 2.0は委任アクセスを提供します。パスワードは"
            "必ずbcryptまたはargon2でハッシュ化してください。ブルートフォース攻撃を"
            "防ぐため、レート制限とアカウントロックアウトを実装してください。"
        ),
        "tags": ["security", "authentication", "authorization"],
    },
    {
        "id": "doc_005",
        "title": "CI/CDパイプライン",
        "content": (
            "継続的インテグレーション（CI）は、コミットのたびに自動的にコードを"
            "ビルド・テストします。継続的デプロイ（CD）は、成功したビルドを自動的に"
            "本番環境へデプロイします。主な実践: 高速なフィードバックループ、"
            "トランクベース開発、段階的なロールアウトのためのフィーチャーフラグ、"
            "失敗時の自動ロールバック。ツールにはGitHub Actions、GitLab CI、"
            "Jenkinsがあります。"
        ),
        "tags": ["devops", "ci-cd", "automation"],
    },
    {
        "id": "doc_006",
        "title": "Kubernetesによるコンテナオーケストレーション",
        "content": (
            "Kubernetesはクラスタ全体でコンテナ化されたワークロードを管理します。"
            "中心となる概念: Pod（最小のデプロイ単位）、Service（ネットワークの"
            "抽象化）、Deployment（宣言的な更新）、ConfigMap/Secret（設定管理）。"
            "主な機能にはオートスケーリング、自己修復、ローリングアップデート、"
            "サービスディスカバリが含まれます。"
        ),
        "tags": ["devops", "kubernetes", "containers"],
    },
    {
        "id": "doc_007",
        "title": "イベント駆動アーキテクチャ",
        "content": (
            "イベント駆動アーキテクチャは、サービス間のトリガーと通信にイベントを"
            "使用します。パターンにはイベントソーシング（状態をイベントとして"
            "保存）、CQRS（読み取りと書き込みの分離）、pub/subメッセージングが"
            "含まれます。利点: 疎結合、スケーラビリティ、監査証跡。課題: 結果整合性、"
            "イベントの順序、分散フローのデバッグ。"
        ),
        "tags": ["architecture", "events", "messaging"],
    },
    {
        "id": "doc_008",
        "title": "キャッシュ戦略",
        "content": (
            "キャッシュは、頻繁にアクセスされるデータをメモリに保存することで、"
            "レイテンシとデータベース負荷を削減します。戦略にはcache-aside"
            "（アプリケーションがキャッシュを管理）、write-through（書き込み時に"
            "キャッシュを更新）、write-behind（非同期のキャッシュ書き込み）が"
            "あります。分散キャッシュにはRedisやMemcachedを使用してください。"
            "適切なTTLを設定し、キャッシュの無効化を慎重に実装してください。"
        ),
        "tags": ["performance", "caching", "redis"],
    },
]

# ---------------------------------------------------------------------------
# システムプロンプトとツール定義
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "あなたはリサーチアシスタントです。ツール経由で提供された検索結果の情報のみを"
    "使って質問に答えてください。回答には必ずドキュメントIDで出典を明記してください。"
    "関連情報が見つからない場合は、その旨を明確に伝えてください"
    "（例:「関連する情報が見つかりませんでした」）。情報を捏造しないでください。"
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": (
                "クエリに一致するドキュメントをナレッジベースから検索します。"
                "関連するドキュメントをその内容とともに返します。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "関連ドキュメントを見つけるための検索クエリ",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "返すドキュメントの最大数（デフォルト: 3）",
                        "default": 3,
                    },
                },
                "required": ["query"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# 検索関数
# ---------------------------------------------------------------------------


def search_knowledge_base(
    query: str, max_results: int = 3, corpus: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """キーワードマッチングでナレッジベースを検索する。

    クエリの分かち書きにはjanome（tokenize_japanese）を使う——str.split()による
    単純な空白区切りは、分かち書きのない日本語ではほとんど機能しないため。
    """
    docs = corpus if corpus is not None else KNOWLEDGE_BASE
    query_words = tokenize_japanese(query, show_progress=False)[0]
    scored: list[tuple[int, dict[str, Any]]] = []
    for doc in docs:
        text = f"{doc['title']} {doc['content']} {' '.join(doc['tags'])}"
        score = sum(1 for word in query_words if word in text)
        if score > 0:
            scored.append((score, doc))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [doc for _, doc in scored[:max_results]]
