
def build_answer_prompt(question: str, documents: list[str], metadatas: list[dict]) -> str:
    blocks = []
    for doc, meta in zip(documents, metadatas):
        blocks.append(
            f"SECTION: {meta.get('section')}\n"
            f"RESOURCE: {meta.get('resouce_kind')}/{meta.get('resource_name')}\n"
            f"NAMESPACE: {meta.get('namespace')}\n"
            f"FILE: {meta.get('file_path')}\n"
            f"CONTENT:\n{doc}"
        )

    context = "\n\n---\n\n".join(blocks)
    return f"""
You are an RCA assistant.

Use only the retrieved context.
If the answer is not in the context, say you do not know.

Context:
{context}

Question:
{question}
""".strip()