# app/utils/file_loader.py
from pathlib import Path
from app.config import DATA_DIR, FILE_PATTERNS

def discover_files() -> list[Path]:
    files = []
    for pattern in FILE_PATTERNS:
        files.extend(DATA_DIR.glob(pattern))
    return sorted(set(files))