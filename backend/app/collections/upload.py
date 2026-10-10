"""Parse and validate a client's upload (CSV or XLSX), driven by the client's
segment (see segments.py).

Two kinds of upload:

- **new** — one row per person who owes; the segment's columns exactly.
- **update** — refresh balances of existing open accounts, matched on the
  client's own reference (admission number, unit, loan reference …):
  `<reference column>, amount_owed, due_date (optional), reason (optional)`.

Any column outside the template rejects the whole file. No segment has a
column for guarantors, references, relatives or employers, so third-party
data can't get in."""

import csv
import io
import re
from datetime import date, datetime

from app.collections import crypto
from app.collections.money import naira_to_kobo
from app.collections.phone import normalise_phone
from app.collections.segments import Column, Segment

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
UPDATE_REASONS = ("payment", "correction")


class UploadRejected(ValueError):
    pass


# ---------------------------------------------------------------- templates


def update_columns(seg: Segment) -> list[Column]:
    ref = seg.reference_column
    return [
        Column(ref.name, "text", True, ref.label, "Must match an account you've already uploaded", "external_reference", 60),
        Column("amount_owed", "amount", True, "New balance (₦)", "What they owe now; 0 if fully paid", "amount"),
        Column("due_date", "date", False, "Due date", "DD/MM/YYYY", "due_date"),
        Column("reason", "text", False, "Reason for a lower balance",
               "payment (they paid you directly — counts as a recovery) or correction", "extra", 20),
    ]


def columns_for(seg: Segment, mode: str) -> list[Column]:
    return update_columns(seg) if mode == "update" else list(seg.columns)


def _example_rows(seg: Segment, mode: str) -> list[list[str]]:
    if mode == "new":
        return [list(r) for r in seg.example_rows]
    ref_index = seg.column_names.index(seg.reference_column.name)
    example_ref = next((r[ref_index] for r in seg.example_rows if r[ref_index]), "REF-001")
    return [[example_ref, "50000", "", "payment"]]


def template_csv(seg: Segment, mode: str = "new") -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([c.name for c in columns_for(seg, mode)])
    writer.writerows(_example_rows(seg, mode))
    return out.getvalue()


def template_xlsx(seg: Segment, mode: str = "new") -> bytes:
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter

    cols = columns_for(seg, mode)
    wb = Workbook()
    ws = wb.active
    ws.title = "Balances" if mode == "new" else "Balance updates"
    ws.append([c.name for c in cols])
    for row in _example_rows(seg, mode):
        ws.append(row)
    for i, c in enumerate(cols, start=1):  # keep leading zeros and dates exactly as typed
        if c.kind in ("phone", "date", "text"):
            for cell in ws[get_column_letter(i)]:
                cell.number_format = "@"
    guide = wb.create_sheet("How to fill this in")
    guide.append(["column", "required", "what to put"])
    for c in cols:
        guide.append([c.name, "yes" if c.required else "no", f"{c.label}. {c.help}".strip(". ")])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- reading


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def read_rows(file_name: str, data: bytes, allowed: list[str], required: list[str]) -> list[dict[str, str]]:
    name = file_name.lower()
    if name.endswith(".csv"):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        rows = list(csv.reader(io.StringIO(text)))
    elif name.endswith(".xlsx"):
        from openpyxl import load_workbook

        try:
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as exc:
            raise UploadRejected("We couldn't open that Excel file.") from exc
        rows = [[_cell(v) for v in row] for row in wb.worksheets[0].iter_rows(values_only=True)]
    else:
        raise UploadRejected("Upload a .csv or .xlsx file.")

    rows = [r for r in rows if any(_cell(c) for c in r)]
    if not rows:
        raise UploadRejected("The file is empty.")
    header = [_cell(h).lower().replace(" ", "_") for h in rows[0]]
    while header and header[-1] == "":
        header.pop()
    extra = [h for h in header if h not in allowed]
    if extra:
        raise UploadRejected(
            f"Unexpected column(s): {', '.join(extra)}. Only the template columns are allowed — "
            "we never accept details of anyone other than the person who owes."
        )
    missing = [c for c in required if c not in header]
    if missing:
        raise UploadRejected(f"Missing required column(s): {', '.join(missing)}.")
    if len(set(header)) != len(header):
        raise UploadRejected("A column appears more than once.")
    out = []
    for raw in rows[1:]:
        values = [_cell(v) for v in raw]
        if any(values[len(header):]):
            raise UploadRejected("A row has values outside the template columns.")
        out.append({col: (values[i] if i < len(values) else "") for i, col in enumerate(header)})
    return out


def read_for(seg: Segment, mode: str, file_name: str, data: bytes) -> list[dict[str, str]]:
    cols = columns_for(seg, mode)
    return read_rows(file_name, data, [c.name for c in cols], [c.name for c in cols if c.required])


# ---------------------------------------------------------------- validating


def _parse_date(value: str) -> date:
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"must be a date like DD/MM/YYYY (got '{value}')")


def _value(col: Column, raw: str):
    """Validate one cell; returns the clean value or raises ValueError."""
    raw = (raw or "").strip()
    if not raw:
        if col.required:
            raise ValueError("is required")
        return None
    if col.kind == "phone":
        return normalise_phone(raw)
    if col.kind == "email":
        if not EMAIL_RE.match(raw):
            raise ValueError(f"'{raw}' is not a valid email")
        return raw.lower()
    if col.kind == "amount":
        return naira_to_kobo(raw)
    if col.kind == "date":
        return _parse_date(raw).isoformat()
    if col.kind == "int":
        try:
            v = int(raw)
        except ValueError as exc:
            raise ValueError("must be a whole number") from exc
        if v < 0:
            raise ValueError("can't be negative")
        return v
    if col.kind == "first_name":
        # first name only — a child's surname must never be stored
        if " " in raw or len(raw) > col.max_len:
            raise ValueError("must be a single first name")
        return raw
    if len(raw) > col.max_len:
        raise ValueError(f"is too long ({col.max_len} characters max)")
    return raw


def validate_row(row: dict[str, str], cols: list[Column]) -> tuple[dict | None, list[str]]:
    """Clean dict: debtor_name, phone, email, description, amount_kobo,
    due_date, dependant_first_name, external_reference, extra{}."""
    errors: list[str] = []
    clean: dict = {"extra": {}, "dependant_first_name": None, "external_reference": None, "due_date": None, "email": None}
    for col in cols:
        try:
            value = _value(col, row.get(col.name, ""))
        except ValueError as exc:
            errors.append(f"{col.name} {exc}")
            continue
        target = col.maps_to
        if target == "amount":
            clean["amount_kobo"] = value
        elif target == "dependant":
            clean["dependant_first_name"] = value
        elif target == "extra":
            if value is not None:
                clean["extra"][col.name] = value
        else:
            clean[target] = value
    return (None if errors else clean), errors


def validate_new_row(row: dict[str, str], seg: Segment) -> tuple[dict | None, list[str]]:
    clean, errors = validate_row(row, list(seg.columns))
    if clean is not None and clean["amount_kobo"] <= 0:
        return None, ["amount_owed must be more than 0"]
    return clean, errors


def duplicate_key(clean: dict) -> str:
    """The client's own reference identifies an account when given;
    otherwise the same phone + description + amount is a duplicate."""
    if clean.get("external_reference"):
        return crypto.blind_index(f"ref|{clean['external_reference'].strip().lower()}")
    return crypto.blind_index(f"{clean['phone']}|{clean['description'].strip().lower()}|{clean['amount_kobo']}")


def reference_index(reference: str) -> str:
    return crypto.blind_index(f"ref|{reference.strip().lower()}")


def build_preview(rows: list[dict[str, str]], seg: Segment, existing_keys: set[str]) -> list[dict]:
    """Every row with status valid / error / duplicate."""
    seen: set[str] = set()
    preview = []
    for i, row in enumerate(rows, start=2):  # row 1 is the header
        clean, errors = validate_new_row(row, seg)
        item = {"row": i, "raw": row, "errors": errors}
        if clean is None:
            item["status"] = "error"
        else:
            key = duplicate_key(clean)
            item["clean"] = clean
            if key in seen or key in existing_keys:
                item["status"] = "duplicate"
                item["errors"] = ["already in this file or one of your open accounts"]
            else:
                item["status"] = "valid"
            seen.add(key)
        preview.append(item)
    return preview


def build_update_preview(rows: list[dict[str, str]], seg: Segment, accounts_by_ref: dict[str, dict]) -> list[dict]:
    """`accounts_by_ref` maps reference_index → {id, balance, status, debtor_name, reference_code}
    for the client's accounts. Status per row: valid / error / unchanged."""
    cols = update_columns(seg)
    seen: set[str] = set()
    preview = []
    for i, row in enumerate(rows, start=2):
        clean, errors = validate_row(row, cols)
        item = {"row": i, "raw": row, "errors": errors}
        if clean is None:
            item["status"] = "error"
            preview.append(item)
            continue
        ref_key = reference_index(clean["external_reference"])
        account = accounts_by_ref.get(ref_key)
        reason = (clean["extra"].get("reason") or "").lower() or None
        if ref_key in seen:
            errors.append("this reference appears more than once in the file")
        elif account is None:
            errors.append(f"no account with {seg.reference_column.label.lower()} '{clean['external_reference']}'")
        elif account["status"] in ("Paid", "Withdrawn"):
            errors.append("that account is already closed")
        elif account["status"] == "Disputed":
            errors.append("that account has an open query — resolve it before changing the balance")
        elif clean["amount_kobo"] < 0:
            errors.append("amount_owed can't be negative")
        elif reason and reason not in UPDATE_REASONS:
            errors.append("reason must be 'payment' or 'correction'")
        seen.add(ref_key)
        if errors:
            item["status"] = "error"
        else:
            change = clean["amount_kobo"] - account["balance"]
            clean["reason"] = reason or ("payment" if change < 0 else None)
            item.update(clean=clean, account=account, change=change,
                        status="unchanged" if change == 0 and not clean.get("due_date") else "valid")
        preview.append(item)
    return preview
