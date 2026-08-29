"""ローカルのsentence-transformer埋め込み——APIキー不要。"""

import logging

from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# 元の`rag/`が使うall-MiniLM-L6-v2は英語専用のため、日本語を含む多言語に対応した
# E5系モデルをデフォルトにしている（初回実行時に約470MBダウンロードされる）。
DEFAULT_MODEL = "intfloat/multilingual-e5-small"

# E5系モデルは、クエリと文書で異なる接頭辞を付けることを前提に学習されている。
# これを付けないと検索精度が大きく落ちるため、埋め込み前に必ず付与する。
_QUERY_PREFIX = "query: "
_PASSAGE_PREFIX = "passage: "


class LocalEmbedder:
    """ローカルのsentence-transformersモデルを使って埋め込みを生成する。"""

    def __init__(self, model_name: str = DEFAULT_MODEL):
        logger.info("Loading embedding model: %s", model_name)
        self.model = SentenceTransformer(model_name)
        logger.info(
            "Embedding model loaded (dimension=%d)", self.model.get_sentence_embedding_dimension()
        )

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """インデックス化のためにドキュメントを埋め込む。"""
        if not texts:
            return []

        prefixed = [_PASSAGE_PREFIX + t for t in texts]
        embeddings = self.model.encode(prefixed, show_progress_bar=False)
        logger.info("Embedded %d documents", len(texts))
        return [e.tolist() for e in embeddings]

    def embed_query(self, query: str) -> list[float]:
        """検索クエリを埋め込む。"""
        embedding = self.model.encode(_QUERY_PREFIX + query)
        result: list[float] = embedding.tolist()
        return result
