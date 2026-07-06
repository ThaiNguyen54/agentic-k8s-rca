# app/store/qdrant_store.py
from qdrant_client import QdrantClient, models
from app.config import (
    QDRANT_MODE,
    QDRANT_PATH,
    QDRANT_URL,
    COLLECTION_NAME
)

class QdrantVectorStore:
    def __init__(self):
        if QDRANT_MODE == "local":
            self.client = QdrantClient(path=QDRANT_PATH)
        else:
            self.client = QdrantClient(url=QDRANT_URL)

    def ensure_collection(self, vector_size: int):
        if not self.client.collection_exists(COLLECTION_NAME):
            self.client.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=models.VectorParams(
                    size=vector_size,
                    distance=models.Distance.COSINE,
                )
            )
    
    def upsert(self, ids: list[str], vectors: list[list[float]], payloads: list[dict]):
        points = [
            models.PointStruct(id=pid, vector=vec, payload=payload) for pid, vec, payload in zip(ids, vectors, payloads)
        ]
        self.client.upsert(
            collection_name=COLLECTION_NAME,
            points=points,
            wait=True,
        )

    def search(self, query_vector: list[float], top_k: int = 4, query_filter=None):
        return self.client.search(
            collection_name=COLLECTION_NAME,
            query_vector=query_vector,
            query_filter=query_filter,
            with_payload=True,
            limit=top_k
        )
        