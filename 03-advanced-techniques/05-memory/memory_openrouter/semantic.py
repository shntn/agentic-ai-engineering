"""Semantic memory — ChromaDBベクトルデータベースに保存される事実と知識。"""

import chromadb
from common.logging_config import setup_logging
from sentence_transformers import SentenceTransformer

from .models import MemoryEntry, MemoryType

logger = setup_logging(__name__)

# ChromaDBのデフォルト埋め込み関数（all-MiniLM-L6-v2相当）は英語中心のため、
# 日本語を含む多言語に対応したE5系モデルを明示的に使用する（06-rag-techniquesの
# rag_openrouter/embedder.pyと同じモデル）。ChromaDBの自動埋め込み（query_texts/
# documentsを渡すだけの方式）には言語別のプレフィックスを差し込む余地がないため、
# ここでは自前でモデルを呼び出し、embeddings/query_embeddingsとして明示的に渡す。
_EMBEDDING_MODEL = "intfloat/multilingual-e5-small"

# E5系モデルは、クエリと文書で異なる接頭辞を付けることを前提に学習されている。
_QUERY_PREFIX = "query: "
_PASSAGE_PREFIX = "passage: "


class SemanticMemory:
    """コサイン類似度検索を行う、ChromaDBを裏側に持つ長期的な事実メモリ。"""

    def __init__(self, persist_dir: str = "data/chroma") -> None:
        self.embedder = SentenceTransformer(_EMBEDDING_MODEL)
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.collection = self.client.get_or_create_collection(
            name="semantic_memory",
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("ChromaDB initialized at %s (%d entries)", persist_dir, self.collection.count())

    def save(self, entry: MemoryEntry) -> None:
        """メモリを保存する——E5モデルで明示的に埋め込む。"""
        entry.memory_type = MemoryType.SEMANTIC
        embedding = self.embedder.encode(_PASSAGE_PREFIX + entry.content).tolist()
        self.collection.add(
            ids=[entry.id],
            embeddings=[embedding],
            documents=[entry.content],
            metadatas=[
                {
                    "timestamp": entry.timestamp.isoformat(),
                    "importance": entry.importance,
                    **{k: str(v) for k, v in entry.metadata.items()},
                }
            ],
        )
        logger.info("Saved semantic memory: %s", entry.content[:60])

    def search(self, query: str, limit: int = 5) -> list[tuple[MemoryEntry, float]]:
        """意味的類似度で検索する——(entry, similarity_score) のペアを返す。"""
        if self.collection.count() == 0:
            return []

        query_embedding = self.embedder.encode(_QUERY_PREFIX + query).tolist()
        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=min(limit, self.collection.count()),
            include=["documents", "metadatas", "distances"],
        )

        entries: list[tuple[MemoryEntry, float]] = []
        for i in range(len(results["ids"][0])):
            metadata = results["metadatas"][0][i]
            # コサイン距離 → 類似度: similarity = 1 - distance
            similarity = 1.0 - results["distances"][0][i]
            entry = MemoryEntry(
                id=results["ids"][0][i],
                content=results["documents"][0][i],
                memory_type=MemoryType.SEMANTIC,
                importance=float(metadata.get("importance", 0.5)),
                metadata={
                    k: v for k, v in metadata.items() if k not in ("timestamp", "importance")
                },
            )
            entries.append((entry, similarity))

        return entries

    def delete(self, memory_id: str) -> bool:
        """IDを指定してメモリを削除する。"""
        try:
            self.collection.delete(ids=[memory_id])
            logger.info("Deleted semantic memory: %s", memory_id)
            return True
        except Exception as e:
            logger.error("Failed to delete semantic memory %s: %s", memory_id, e)
            return False

    def list_all(self) -> list[MemoryEntry]:
        """すべてのsemanticメモリを返す。"""
        if self.collection.count() == 0:
            return []

        results = self.collection.get(include=["documents", "metadatas"])
        entries: list[MemoryEntry] = []
        for i in range(len(results["ids"])):
            metadata = results["metadatas"][i]
            entry = MemoryEntry(
                id=results["ids"][i],
                content=results["documents"][i],
                memory_type=MemoryType.SEMANTIC,
                importance=float(metadata.get("importance", 0.5)),
                metadata={
                    k: v for k, v in metadata.items() if k not in ("timestamp", "importance")
                },
            )
            entries.append(entry)
        return entries

    def clear(self) -> None:
        """コレクションを再作成して、すべてのsemanticメモリをクリアする。"""
        self.client.delete_collection("semantic_memory")
        self.collection = self.client.get_or_create_collection(
            name="semantic_memory",
            metadata={"hnsw:space": "cosine"},
        )
        logger.info("Cleared all semantic memories")

    def stats(self) -> dict:
        """semanticメモリの統計情報を返す。"""
        return {
            "count": self.collection.count(),
            "collection": self.collection.name,
        }
