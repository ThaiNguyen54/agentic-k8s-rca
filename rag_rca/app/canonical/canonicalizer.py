# app/canonical/canonicalizer.py
import hashlib
import yaml
from app.schemas import ParsedDocument, CanonicalRecord

def _build_record_id(parsed: ParsedDocument, obj: dict) -> str:
    metadata = obj.get("metadata", {})
    raw = "|".join([
        parsed.file_path,
        str(parsed.doc_index),
        str(obj.get("apiVersion")),
        str(obj.get("kind")),
        str(metadata.get("namespace")),
        str(metadata.get("name"))
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()

def canonicalize_record(parsed: ParsedDocument) -> CanonicalRecord:
    obj = parsed.content
    metadata = obj.get("metadata", {}) if isinstance(obj.get("metadata"), dict) else {}
    spec = obj.get ("spec", {}) if isinstance(obj.get("spec"), dict) else {}

    return CanonicalRecord(
        record_id=_build_record_id(parsed, obj),
        source_type=parsed.source_type,
        file_path=parsed.file_path,
        doc_index=parsed.doc_index,
        resource_kind=obj.get("kind"),
        resource_name=metadata.get("name"),
        namespace=metadata.get("namespace"),
        provider=spec.get("provider"),
        section="root",
        path="/",
        text=yaml.dump(obj, sort_keys=False, allow_unicode=True),
        raw=obj,
        attributes={
            "apiVersion": obj.get("apiVersion"),
            "file_path": parsed.file_path,
            "doc_index": parsed.doc_index,
            "top_level_keys": list(obj.keys()),
            "labels_count": len(metadata.get("labels", {}) or {}),
            "annotations_count": len(metadata.get("annotations", {}) or {}),
        },
        links={
            "ownerReferences": metadata.get("ownerReferences", []),
        },
    )