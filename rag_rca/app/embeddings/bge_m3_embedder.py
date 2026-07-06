# app/embeddings/bge_m3_embedder.py
from sentence_transformer import SentenceTransformer
from app.schemas import CanonicalRecord

class BGEEmbedder:
    def __init__(self, model_name: str):
        self.model = SentenceTransformer(model_name)

    def dimension(self) -> int:
        return self.model.get_sentence_embedding_dimension()

    def serialize(self, chunk: CanonicalRecord) -> str:
        header = [
            f"source_type: {chunk.source_type}",
            f"resource_kind: {chunk.resource_kind}",
            f"resource_name: {chunk.resource_name}",
            f"namespace: {chunk.namespace}",
            f"provider: {chunk.provider}",
            f"section: {chunk.section}",
            f"path: {chunk.path}",
            f"file_path: {chunk.file_path}",
            f"doc_index: {chunk.doc_index}",
        ]
        attrs = "\n".join(f"{k}: {v}" for k, v in chunk.attributes.items())
        return "\n".join(header) + "\n\natrributes:\n" + attrs + "\n\ncontent:\n" + chunk.text

    def encode_texts(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, normalize_embeddings=True).tolist()