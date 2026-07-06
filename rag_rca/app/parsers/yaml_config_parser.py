# app/parsers/yaml_config_parser.py
from pathlib import Path
import yaml
from app.schemas import ParsedDocument

def can_parse(path: Path) -> bool:
    return path.suffix.lower() in {".yaml", ".yml"}

def parse_yaml_file(path: Path) -> list[ParsedDocument]:
    with open(path, "r", encoding="utf-8") as f:
        docs = list(yaml.safe_load_all(f))

    parsed_docs = []
    for idx, doc in enumerate(docs):
        if isinstance(doc, dict) and doc:
            parsed_docs.append(
                ParsedDocument(
                    source_type="config_yaml",
                    file_path=str(path),
                    doc_index=idx,
                    content=doc,
                )
            )
    return parsed_docs