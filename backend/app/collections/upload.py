"""Parse and validate a client's debtor upload (CSV or XLSX).

The template has exactly the columns below and nothing else — there's no
place for references, guarantors or relatives, and any extra column rejects
the whole file (third-party data must never get in)."""

import csv
import io
import re
from datetime import date, datetime

from app.collections import crypto
from app.collections.money import naira_to_kobo
from app.collections.phone import normalise_phone

COLUMNS = ["debtor_name", "phone", "email", "description", "dependant_first_name", "amount_owed", "due_date"]
REQUIRED = {"debtor_name", "phone", "description", "amount_owed"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

EXAMPLE_ROWS = [
    ["Adaeze Okafor", "08031234567", "adaeze@example.com", "School fees – 2nd term 2025/26", "Tolu", "150000", "15/01/2026"],
    ["Musa Bello", "+2348051234567", "", "Estate service charge – Q1 2026", "", "45000.50", ""],
]


class UploadRejected(ValueError):
    pass


def template_csv() -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(COLUMNS)
    writer.writerows(EXAMPLE_ROWS)
    return out.getvalue()


def template_xlsx() -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Balances"
    ws.append(COLUMNS)
    for row in EXAMPLE_ROWS:
        ws.append(row)
    for col in ("B", "G"):  # keep leading zeros on phones and dates as typed
        for cell in ws[col]:
            cell.number_format = "@"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


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


def read_rows(file_name: str, data: bytes) -> list[dict[str, str]]:
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
    extra = [h for h in header if h not in COLUMNS]
    if extra:
        raise UploadRejected(
            f"Unexpected column(s): {', '.join(extra)}. Only the template columns are allowed — "
            "we never accept details of anyone other than the person who owes."
        )
    missing = REQUIRED - set(header)
    if missing:
        raise UploadRejected(f"Missing required column(s): {', '.join(sorted(missing))}.")
    if len(set(header)) != len(header):
        raise UploadRejected("A column appears more than once.")
    out = []
    for raw in rows[1:]:
        values = [_cell(v) for v in raw]
        if any(values[len(header):]):
            raise UploadRejected("A row has values outside the template columns.")
        out.append({col: (values[i] if i < len(values) else "") for i, col in enumerate(header)})
    return out


def _parse_date(value: str) -> date:
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"due_date '{value}' must be DD/MM/YYYY")


def validate_row(row: dict[str, str]) -> tuple[dict | None, list[str]]:
    errors: list[str] = []
    clean: dict = {}
    name = row.get("debtor_name", "").strip()
    if not name:
        errors.append("debtor_name is required")
    elif len(name) > 160:
        errors.append("debtor_name is too long")
    clean["debtor_name"] = name
    try:
        clean["phone"] = normalise_phone(row.get("phone"))
    except ValueError as exc:
        errors.append(str(exc) if row.get("phone") else "phone is required")
    email = row.get("email", "").strip()
    if email and not EMAIL_RE.match(email):
        errors.append(f"email '{email}' is not valid")
    clean["email"] = email.lower() or None
    description = row.get("description", "").strip()
    if not description:
        errors.append("description is required")
    elif len(description) > 300:
        errors.append("description is too long (300 characters max)")
    clean["description"] = description
    dependant = row.get("dependant_first_name", "").strip()
    if dependant and (" " in dependant or len(dependant) > 40):
        # first name only — a child's surname must never be stored
        errors.append("dependant_first_name must be a single first name")
    clean["dependant_first_name"] = dependant or None
    try:
        kobo = naira_to_kobo(row.get("amount_owed"))
        if kobo <= 0:
            errors.append("amount_owed must be more than 0")
        clean["amount_kobo"] = kobo
    except ValueError as exc:
        errors.append(f"amount_owed: {exc}")
    due = row.get("due_date", "").strip()
    if due:
        try:
            clean["due_date"] = _parse_date(due).isoformat()
        except ValueError as exc:
            errors.append(str(exc))
    else:
        clean["due_date"] = None
    return (None if errors else clean), errors


def build_preview(rows: list[dict[str, str]], existing_keys: set[str]) -> list[dict]:
    """Every row with status valid / error / duplicate. A duplicate is the
    same phone + description + amount as an earlier row in the file or an
    open account the client already has."""
    seen: set[str] = set()
    preview = []
    for i, row in enumerate(rows, start=2):  # row 1 is the header
        clean, errors = validate_row(row)
        item = {"row": i, "raw": row, "errors": errors}
        if clean is None:
            item["status"] = "error"
        else:
            key = duplicate_key(clean["phone"], clean["description"], clean["amount_kobo"])
            item["clean"] = clean
            if key in seen or key in existing_keys:
                item["status"] = "duplicate"
                item["errors"] = ["same person, description and amount already in this file or your accounts"]
            else:
                item["status"] = "valid"
            seen.add(key)
        preview.append(item)
    return preview


def duplicate_key(phone: str, description: str, amount_kobo: int) -> str:
    return crypto.blind_index(f"{phone}|{description.strip().lower()}|{amount_kobo}")
