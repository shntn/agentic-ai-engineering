"""BM25キーワードインデックスを併用したChromaDBベクトルストア。"""

import logging

import bm25s
import chromadb

from openrouter_adapter import tokenize_japanese
from rag_openrouter.chunker import Chunk
from rag_openrouter.embedder import LocalEmbedder

logger = logging.getLogger(__name__)


class VectorStore:
    """デュアルインデックスストア: ベクトル検索にChromaDB、キーワード検索にBM25。"""

    def __init__(self, embedder: LocalEmbedder, persist_dir: str | None = None):
        self.embedder = embedder
        self.chunks: list[Chunk] = []
        self._chunk_lookup: dict[str, Chunk] = {}

        # ChromaDB——永続化またはインメモリ
        if persist_dir:
            self.chroma_client = chromadb.PersistentClient(path=persist_dir)
        else:
            self.chroma_client = chromadb.Client()

        self.collection = self.chroma_client.get_or_create_collection(
            name="documents",
            metadata={"hnsw:space": "cosine"},
        )

        # BM25——取り込み後に構築される
        self.bm25: bm25s.BM25 | None = None

    def add_chunks(self, chunks: list[Chunk]) -> None:
        """チャンクを埋め込み、ベクトルストアとBM25の両方にインデックス化する。"""
        if not chunks:
            return

        self.chunks = chunks
        self._chunk_lookup = {c.id: c for c in chunks}

        texts = [c.content for c in chunks]
        ids = [c.id for c in chunks]
        metadatas = [{"source": c.source, "chunk_index": c.chunk_index} for c in chunks]

        # 埋め込んでChromaDBに追加する
        embeddings = self.embedder.embed_documents(texts)
        self.collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
        )
        logger.info("Indexed %d chunks in ChromaDB", len(chunks))

        # BM25インデックスを構築する
        # 日本語テキストのため、bm25s.tokenize()の代わりにtokenize_japanese()を使う
        tokenized = tokenize_japanese(texts, show_progress=False)
        self.bm25 = bm25s.BM25()
        self.bm25.index(tokenized, show_progress=False)
        logger.info("Built BM25 index over %d chunks", len(chunks))

    def vector_search(self, query: str, top_k: int = 20) -> list[tuple[Chunk, float]]:
        """ChromaDBによる密ベクトル類似度検索。"""
        query_embedding = self.embedder.embed_query(query)

        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, len(self.chunks)),
        )

        scored: list[tuple[Chunk, float]] = []
        if results["ids"] and results["ids"][0]:
            for chunk_id, distance in zip(results["ids"][0], results["distances"][0]):
                chunk = self._chunk_lookup.get(chunk_id)
                if chunk:
                    # ChromaDBはコサイン距離を返すので、類似度に変換する
                    similarity = 1.0 - distance
                    scored.append((chunk, similarity))

        return scored

    def keyword_search(self, query: str, top_k: int = 20) -> list[tuple[Chunk, float]]:
        """BM25キーワード検索。"""
        if self.bm25 is None or not self.chunks:
            return []

        tokenized_query = tokenize_japanese(query)
        results, scores = self.bm25.retrieve(tokenized_query, k=min(top_k, len(self.chunks)))

        scored: list[tuple[Chunk, float]] = []
        for idx, score in zip(results[0], scores[0]):
            if 0 <= idx < len(self.chunks) and score > 0:
                scored.append((self.chunks[idx], float(score)))

        return scored

    @property
    def chunk_count(self) -> int:
        """インデックス化されたチャンクの数。"""
        return len(self.chunks)
