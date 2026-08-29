"""ベクトル検索・BM25・リランキングを組み合わせたハイブリッド検索器。"""

import logging

from rag_openrouter.chunker import Chunk
from rag_openrouter.reranker import Reranker
from rag_openrouter.store import VectorStore

logger = logging.getLogger(__name__)


class HybridRetriever:
    """ベクトル検索とBM25を、reciprocal rank fusionとリランキングで組み合わせる。"""

    def __init__(self, store: VectorStore, reranker: Reranker | None = None):
        self.store = store
        self.reranker = reranker

    def retrieve(self, query: str, top_k: int = 5, candidates: int = 20) -> list[Chunk]:
        """完全な検索パイプライン: ベクトル + BM25 → RRF → リランク → top_k。

        各手法から`candidates`件を取得し、RRFで融合したうえで、
        オプションでリランクして最終的なtop_k件の結果を生成する。
        """
        vector_results = self.store.vector_search(query, top_k=candidates)
        keyword_results = self.store.keyword_search(query, top_k=candidates)

        logger.info(
            "Retrieved %d vector + %d keyword results for: %s",
            len(vector_results),
            len(keyword_results),
            query[:60],
        )

        # reciprocal rank fusionで結果を融合する
        fused = self._reciprocal_rank_fusion(vector_results, keyword_results)
        fused_chunks = [chunk for chunk, _ in fused]

        # リランカーが利用可能ならリランクする
        if self.reranker and fused_chunks:
            return self.reranker.rerank(query, fused_chunks, top_k=top_k)

        return fused_chunks[:top_k]

    def _reciprocal_rank_fusion(
        self,
        vector_results: list[tuple[Chunk, float]],
        keyword_results: list[tuple[Chunk, float]],
        k: int = 60,
    ) -> list[tuple[Chunk, float]]:
        """Reciprocal Rank Fusionを使ってランク付けされたリストをマージする。

        RRFスコア = そのアイテムが登場する全リストにわたる sum(1 / (k + rank))。
        k=60は元論文で使われている標準的な定数。
        """
        scores: dict[str, float] = {}
        chunk_map: dict[str, Chunk] = {}

        for rank, (chunk, _) in enumerate(vector_results):
            scores[chunk.id] = scores.get(chunk.id, 0) + 1 / (k + rank + 1)
            chunk_map[chunk.id] = chunk

        for rank, (chunk, _) in enumerate(keyword_results):
            scores[chunk.id] = scores.get(chunk.id, 0) + 1 / (k + rank + 1)
            chunk_map[chunk.id] = chunk

        # 融合スコアの降順でソートする
        sorted_ids = sorted(scores, key=lambda cid: scores[cid], reverse=True)
        return [(chunk_map[cid], scores[cid]) for cid in sorted_ids]
