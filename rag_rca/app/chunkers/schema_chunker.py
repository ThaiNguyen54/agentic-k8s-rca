# app/chunkers/schema_chunker.py
import yaml
from typing import Any, List
from app.schemas import CanonicalRecord

SKIP_KEYS = {"apiVersion", "kind"}

def _label_for_item(item: Any, idx: int) -> str:
    if isinstance(item, dict):
        for key in ("name", "id", "key", "type"):
            if item.get(key):
                return str(item[key])
    return str(idx)

def _extract_attrs(value: Any) -> dict:
    attrs = {"value_type": type(value).__name__}

    if isinstance(value, dict):
        attrs["field_count"] = len(value)
        for k in ("name", "kind", "apiVersion", "namespace", "provider", "type"):
            if k in value:
                attrs[k] = value[k]
    
    elif isinstance(value, list):
        attrs["item_count"] = len(value)

    return attrs

def schema_aware_chunk(record: CanonicalRecord) -> List[CanonicalRecord]:
    obj = record.raw
    chunks: List[CanonicalRecord] = []

    def add_chunk(section: str, path: str, content: dict, extra_attrs: dict | None = None):
        extra_attrs = extra_attrs or {}
        chunks.append(
            CanonicalRecord(
                record_id=f"{record.record_id}:{section}",
                source_type=record.source_type,
                file_path=record.file_path,
                doc_index=record.doc_index,
                resource_kind=record.resource_kind,
                resource_name=record.resource_name,
                namespace=record.namespace,
                provider=record.provider,
                section=section,
                path=path,
                text=yaml.dump(content, sort_keys=False, allow_unicode=True),
                raw=content,
                attributes={**record.atrributes, **extra_attrs, "section":section},
                links=record.links
            )
        )
    metadata = obj.get("metadata")
    if isinstance(metadata, dict):
        add_chunk("metadata", "/metadata", {"metadata": metadata}, _extract_attrs(metadata))

    for key, value in obj.items():
        if key in SKIP_KEYS or key == "metadata":
            continue

        if isinstance(value, dict):
            for subkey, subvalue in value.items():
                section = f"{key}.{subkey}"
                path = f"/{key}/{subkey}"

                if isinstance(subvalue, list) and subvalue and all(isinstance(x, dict) for x in subvalue):
                    for idx, item in enumerate(subvalue):
                        label = _label_for_item(item, idx)
                        add_chunk(
                            f"{section}.{label}",
                            f"{path}/{idx}",
                            {subkey: item},
                            _extract_attrs(item),
                        )
                else:
                    add_chunk(section, path, {subkey: subvalue}, _extract_attrs(subvalue))
        elif isinstance(value, list):
            if value and all(isinstance(x, dict) for x in value):
                for idx, item in enumerate(value):
                    label = _label_for_item(item, idx)
                    add_chunk(
                        f"{key}.{label}",
                        f"/{key}/{idx}",
                        {key: item},
                        _extract_attrs(item),
                    )
            else:
                add_chunk(key, f"/{key}", {key: value}, _extract_attrs(value))
        
        else:
            add_chunk(key, f"/{key}", {key: value}, _extract_attrs(value))

    if not chunks:
        chunks.append(record)
    return chunks