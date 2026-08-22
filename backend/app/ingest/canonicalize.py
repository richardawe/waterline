"""L2 — Canonicalisation (WCDS spec §3 L2, §16 stage 5).

Converts raw source values to WCDS canonical types: dates -> `date`, money/rate ->
`float`, booleans -> `bool`, enums/currency -> upper-stripped strings. Deliberately
does NOT "fix" out-of-range values (e.g. a rate entered as 26 instead of 0.26) —
that's a validation failure (WCDS-R021), not a silent correction; auto-correcting
would hide the exact kind of error investors need surfaced. Every field touched
gets one TransformationEvent-shaped record for lineage (spec §13).
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from dateutil import parser as dateutil_parser

from app.ingest.field_registry import FIELDS_BY_NAME

TRUE_VALUES = {"true", "1", "yes", "y", "t"}
FALSE_VALUES = {"false", "0", "no", "n", "f"}


@dataclass
class TransformEvent:
    field_name: str
    original_value: Any
    normalised_value: Any
    rule_id: str


class CanonicalizeError(ValueError):
    pass


def _is_blank(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and v != v:  # NaN
        return True
    if isinstance(v, str) and v.strip() == "":
        return True
    return False


def _to_decimal(v: Any) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("₦", "").replace("NGN", "").strip()
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    return float(s)


def _to_integer(v: Any) -> int:
    if isinstance(v, bool):
        raise CanonicalizeError("boolean is not a valid integer")
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(round(v))
    s = str(v).strip().replace(",", "")
    return int(round(float(s)))


def _to_boolean(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in TRUE_VALUES:
        return True
    if s in FALSE_VALUES:
        return False
    raise CanonicalizeError(f"cannot parse boolean from {v!r}")


def _to_date(v: Any) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    parsed = dateutil_parser.parse(s, dayfirst=False, yearfirst=True)
    return parsed.date()


def _to_string(v: Any) -> str:
    return str(v).strip()


CONVERTERS = {
    "decimal": (_to_decimal, "NORM-DECIMAL"),
    "integer": (_to_integer, "NORM-INTEGER"),
    "boolean": (_to_boolean, "NORM-BOOLEAN"),
    "date": (_to_date, "NORM-DATE"),
    "string": (_to_string, "NORM-STRING"),
    "enum": (lambda v: _to_string(v).upper().replace(" ", "_"), "NORM-ENUM"),
}


def canonicalize_row(
    raw_row: dict[str, Any], column_to_field: dict[str, str]
) -> tuple[dict[str, Any], list[TransformEvent], dict[str, str]]:
    """Returns (canonical_values, transform_events, field_errors).
    field_errors maps wcds_field_name -> error message for values that could not
    be canonicalized at all (distinct from values that parse but fail validation)."""
    canonical: dict[str, Any] = {}
    events: list[TransformEvent] = []
    errors: dict[str, str] = {}

    for column, field_name in column_to_field.items():
        if column not in raw_row:
            continue
        raw_value = raw_row[column]
        spec = FIELDS_BY_NAME.get(field_name)
        if spec is None:
            continue
        if _is_blank(raw_value):
            canonical[field_name] = None
            continue
        converter, rule_id = CONVERTERS[spec.dtype]
        try:
            value = converter(raw_value)
        except (ValueError, TypeError, ArithmeticError) as exc:
            errors[field_name] = f"{column!r} -> {field_name}: {exc}"
            canonical[field_name] = None
            continue
        canonical[field_name] = value
        events.append(TransformEvent(field_name=field_name, original_value=raw_value, normalised_value=value, rule_id=rule_id))

    return canonical, events, errors
