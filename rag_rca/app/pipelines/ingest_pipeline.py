from itertools import islice
from app.config import EMBEDDING_MODEL, BATCH_SIZE
from app.utils.file_loader import discover_files
from app.parsers import parse_file
from app.canonical.canonicalizer import canonicalize_record
from app.chunkers.schema_chunker import schema_aware_chunk
from app.embeddings.bge_m3_embedder import BGEEmbedder
from app.store.qdrant_store import QdrantVectorStore

def batched(iterable, n):
    it = iter(iterable)
    while batch := list(islice(it, n)):
        yield batch

def run_ingest():
    embedder = BGEEmbedder(EMBEDDING_MODEL)
    store = QdrantVectorStore()
    store.ensure_collection(embedder.dimension())

    all_chunks = []

    for path in discover_files():
        parsed_docs = parse_file(path)
        for parsed in parsed_docs:
            canonical = canonicalize_record(parsed)
            chunks = schema_aware_chunk(canonical)
            all_chunks.extend(chunks)

    for chunk_batch in batched(all_chunks, BATCH_SIZE):
        texts = [embedder.serialize(c) for c in chunk_batch]
        vectors = embedder.encode_texts(texts)

        ids = []
        payloads = []

        for chunk, doc_text in zip(chunk_batch, texts):
            ids.append(chunk.record_id)
            payloads.append({
                "document": doc_text,
                "source_type": chunk.source_type,
                "resource_kind": chunk.resource_kind,
                "resource_name": chunk.resource_name,
                "namespace": chunk.namespace,
                "provider": chunk.provider,
                "section": chunk.section,
                "path": chunk.path,
                "file_path": chunk.file_path,
                "doc_index": chunk.doc_index,
                **{k: str(v) for k, v in chunk.attributes.items()},
            })
        
        store.upsert(ids=ids, vectors=vectors, payloads=payloads)
    
    return len(all_chunks)

