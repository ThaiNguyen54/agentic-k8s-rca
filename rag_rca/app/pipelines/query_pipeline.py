from qdrant_client import models
from app.config import EMBEDDING_MODEL, TOP_K
from app.embeddings.bge_m3_embedder import BGEEmbedder
from app.store.qdrant_store import QdrantVectorStore
from app.prompts.answer_prompt import build_answer_prompt

def build_filter(namespace=None, resource_kind=None, source_type=None, file_path=None):
    must = []

    if namespace:
        must.append(models.FieldCondition(
            key="namespace",
            match=models.MatchValue(value=namespace)
        ))
    if resource_kind:
        must.append(models.FieldCondition(
            key="resource_kind",
            match=models.MatchValue(value=resource_kind)
        ))
    if source_type:
        must.append(models.FieldCondition(
            key="source_type",
            match=models.MatchValue(value=source_type)
        ))
    if file_path:
        must.append(models.FieldCondition(
            key="file_path",
            match=models.MatchValue(value=file_path)
        ))
    
    return models.Filter(must=must) if must else None

def run_query(question: str, namespace=None, resource_kind=None, source_type="config_yaml"):
    embedder = BGEEmbedder(EMBEDDING_MODEL)
    store = QdrantVectorStore()

    query_vector = embedder.encode_texts([question])[0]
    query_filter = build_filter(
        namespace=namespace,
        resource_kind=resource_kind,
        source_type=source_type,
    )

    hits = store.search(
        query_vector=query_vector,
        top_k=TOP_K,
        query_filter=query_filter,
    )

    documents = [hit.payload["document"] for hit in hits]
    metadatas = [
        {k: v for k, v in hit.payload.items() if k != "document"} for hit in hits
    ]

    prompt = build_answer_prompt(question, documents, metadatas)
    return {
        "documents": documents,
        "metadatas": metadatas,
        "prompt": prompt,
    }

    