"""L0 — Raw preservation + initial load (WCDS spec §3 L0, §16 stages 1-2).

Reads a CSV/XLSX loan tape into row dicts and computes the SHA-256 of the raw
bytes so the source file stays addressable/immutable per spec, independent of
whatever happens to it downstream.
"""

import hashlib
import io
from pathlib import Path

import pandas as pd


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_tape(file_bytes: bytes, filename: str) -> tuple[list[dict], list[str], str]:
    """Returns (rows, column_order, file_hash). CSV and XLSX only (spec §12 also
    lists JSON/Parquet — not needed for the MVP mapper)."""
    file_hash = sha256_bytes(file_bytes)
    suffix = Path(filename).suffix.lower()
    buf = io.BytesIO(file_bytes)
    if suffix in (".xlsx", ".xls"):
        df = pd.read_excel(buf)
    else:
        df = pd.read_csv(buf, dtype=str, keep_default_na=True)
    df = df.where(pd.notnull(df), None)
    rows = df.to_dict(orient="records")
    return rows, list(df.columns), file_hash
