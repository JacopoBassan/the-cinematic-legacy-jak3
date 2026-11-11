"""Small file utilities shared by this game builder and installer."""
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def sha(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()
