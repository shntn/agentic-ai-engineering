"""CrossEncoderリランカー——多言語対応、CPUのみ、APIキー不要。"""

import logging

from sentence_transformers import CrossEncoder

from rag_openrouter.chunker import Chunk

logger = logging.getLogger(__name__)


class Reranker:
    """sentence-transformersのCrossEncoderを使い、クエリとの関連性でチャンクをリランクする。

    元の`rag/`はFlashRank（英語専用のms-marco-MiniLM-L-12-v2）を使用しているが、
    FlashRankが提供する多言語モデル（ms-marco-MultiBERT-L-12）は実測で日本語の
    関連度判定の精度が低く、ベクトル検索単体より悪化することを確認したため、
    多言語MS MARCO（mMARCO、日本語を含む）で学習されたCrossEncoderに切り替えている。
    sentence-transformersはembedder.pyで既に依存関係に入っているため、追加の
    パッケージは不要（flashrank自体は`rag/`用にそのまま残している）。
    """

    def __init__(self, model: str = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"):
        self.model = CrossEncoder(model)
        logger.info("Reranker initialized with model=%s", model)

    def rerank(self, query: str, chunks: list[Chunk], top_k: int = 5) -> list[Chunk]:
        """クエリとの関連性でチャンクをリランクし、上位top_k件を返す。"""
        if not chunks:
            return []

        pairs = [(query, chunk.content) for chunk in chunks]
        scores = self.model.predict(pairs)

        # リランカーのスコア（降順）でソートする
        scored_chunks = sorted(zip(chunks, scores), key=lambda x: x[1], reverse=True)
        reranked = [chunk for chunk, _ in scored_chunks[:top_k]]

        logger.info("Reranked %d chunks → top %d", len(chunks), len(reranked))
        return reranked
