"""
ベクトルストア

インデックス化されたコードベースに対するセマンティック検索用の、ChromaDBベースのベクトルストア。
これは拡張LLM（Augmented LLM）パターンにおける「検索」拡張である。
"""

from pathlib import Path
from typing import Any

import chromadb

from common.logging_config import setup_logging

logger = setup_logging(__name__)

# ChromaDBのデータをローカルディレクトリに永続化する
DEFAULT_CHROMA_PATH = str(Path(__file__).parent.parent / "data" / "chroma")


class VectorStore:
    """コードの埋め込みを保存・クエリするためのChromaDBラッパー。"""

    def __init__(self, persist_dir: str = DEFAULT_CHROMA_PATH) -> None:
        self.client = chromadb.PersistentClient(path=persist_dir)
        logger.info("ChromaDB initialized at %s", persist_dir)

    def get_or_create_collection(self, name: str) -> chromadb.Collection:
        """リポジトリ用のコレクションを取得または作成する。"""
        return self.client.get_or_create_collection(
            name=name,
            metadata={"hnsw:space": "cosine"},
        )

    def add_chunks(
        self,
        collection_name: str,
        ids: list[str],
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
    ) -> None:
        """事前計算済みの埋め込みを持つコードチャンクをコレクションに追加する。"""
        collection = self.get_or_create_collection(collection_name)
        # ChromaDBにはバッチサイズの上限があるため、500件ずつ追加する
        batch_size = 500
        for i in range(0, len(ids), batch_size):
            end = i + batch_size
            collection.add(
                ids=ids[i:end],
                documents=documents[i:end],
                embeddings=embeddings[i:end],
                metadatas=metadatas[i:end],
            )
        logger.info("Added %d chunks to collection '%s'", len(ids), collection_name)

    def search(
        self,
        query_embedding: list[float],
        collection_name: str | None = None,
        n_results: int = 5,
    ) -> list[dict[str, Any]]:
        """1つまたはすべてのコレクションを対象に、類似するコードチャンクを検索する。"""
        collections = (
            [self.client.get_collection(collection_name)]
            if collection_name
            else self.client.list_collections()
        )

        all_results: list[dict[str, Any]] = []
        for collection in collections:
            if collection.count() == 0:
                continue
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=min(n_results, collection.count()),
                include=["documents", "metadatas", "distances"],
            )
            for j in range(len(results["ids"][0])):
                all_results.append(
                    {
                        "id": results["ids"][0][j],
                        "content": results["documents"][0][j],
                        "metadata": results["metadatas"][0][j],
                        "distance": results["distances"][0][j],
                        "collection": collection.name,
                    }
                )

        # distanceでソート（cosineでは値が小さいほど類似度が高い）
        all_results.sort(key=lambda x: x["distance"])
        return all_results[:n_results]

    def list_collections(self) -> list[dict[str, Any]]:
        """統計情報とともにインデックス化済みのすべてのリポジトリを一覧表示する。"""
        result = []
        for collection in self.client.list_collections():
            result.append(
                {
                    "name": collection.name,
                    "chunks": collection.count(),
                }
            )
        return result

    def collection_exists(self, name: str) -> bool:
        """コレクションがすでに存在するかを確認する。"""
        return any(c.name == name for c in self.client.list_collections())
