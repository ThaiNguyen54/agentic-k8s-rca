from pathlib import Path
from app.parsers.yaml_config_parser import can_parse, parse_yaml_file
from app.schemas import ParsedDocument

def parse_file(path: Path) -> list[ParsedDocument]:
    if can_parse(path):
        return parse_yaml_file(path)
    return []