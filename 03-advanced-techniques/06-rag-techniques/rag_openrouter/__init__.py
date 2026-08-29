"""RAGパイプラインの構成要素: チャンク分割・埋め込み・保存・検索・リランキング。"""

import logging
import os

# サードパーティ製ライブラリの初期化前に、うるさいログとプログレスバーを抑制する
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["SAFETENSORS_LOG_LEVEL"] = "error"
for _lib in (
    "sentence_transformers",
    "transformers",
    "torch",
    "huggingface_hub",
    "chromadb",
    "bm25s",
    "safetensors",
):
    logging.getLogger(_lib).setLevel(logging.ERROR)

from rag_openrouter.chunker import Chunk, recursive_split  # noqa: E402
from rag_openrouter.embedder import LocalEmbedder  # noqa: E402
from rag_openrouter.reranker import Reranker  # noqa: E402
from rag_openrouter.retriever import HybridRetriever  # noqa: E402
from rag_openrouter.store import VectorStore  # noqa: E402

__all__ = [
    "Chunk",
    "HybridRetriever",
    "Reranker",
    "VectorStore",
    "LocalEmbedder",
    "recursive_split",
]
