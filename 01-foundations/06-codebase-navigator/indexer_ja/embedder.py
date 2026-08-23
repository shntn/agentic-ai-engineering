"""
埋め込みパイプライン

sentence-transformersを使ってコードチャンクを埋め込み、ChromaDBに保存する。
高速なローカル埋め込みのために軽量なall-MiniLM-L6-v2モデルを使用する。
"""

from typing import Any

from sentence_transformers import SentenceTransformer

from common.logging_config import setup_logging

logger = setup_logging(__name__)

# コード検索に適した軽量モデル
MODEL_NAME = "all-MiniLM-L6-v2"


class Embedder:
    """埋め込みを生成するためにsentence-transformersをラップする。"""

    def __init__(self, model_name: str = MODEL_NAME) -> None:
        logger.info("Loading embedding model: %s", model_name)
        self.model = SentenceTransformer(model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        """テキストのリストに対する埋め込みを生成する。"""
        embeddings = self.model.encode(texts, show_progress_bar=False)
        return list(embeddings.tolist())

    def embed_query(self, query: str) -> list[float]:
        """単一の検索クエリに対する埋め込みを生成する。"""
        return list(self.model.encode(query).tolist())


def index_chunks(
    embedder: Embedder,
    vector_store: Any,
    collection_name: str,
    chunks: list[dict[str, Any]],
) -> int:
    """チャンクを埋め込み、ベクトルストアに保存する。"""
    if not chunks:
        return 0

    # ChromaDB用にデータを準備する
    ids = [f"{collection_name}:{c['filepath']}:{c['start_line']}" for c in chunks]
    documents = [c["content"] for c in chunks]
    metadatas = [
        {
            "filepath": c["filepath"],
            "start_line": c["start_line"],
            "end_line": c["end_line"],
            "repo": c["repo"],
        }
        for c in chunks
    ]

    # バッチで埋め込みを生成する
    logger.info("Generating embeddings for %d chunks...", len(chunks))
    if len(chunks) > 1000:
        logger.info("Large repo detected — this may take a few minutes...")
    batch_size = 128
    all_embeddings: list[list[float]] = []
    for i in range(0, len(documents), batch_size):
        batch = documents[i : i + batch_size]
        all_embeddings.extend(embedder.embed(batch))

    # ベクトルDBに保存する
    vector_store.add_chunks(
        collection_name=collection_name,
        ids=ids,
        documents=documents,
        embeddings=all_embeddings,
        metadatas=metadatas,
    )

    return len(chunks)
