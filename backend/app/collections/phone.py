"""Nigerian phone normalisation to E.164 (+234...)."""

import re

_MOBILE_PREFIXES = ("70", "80", "81", "90", "91")


def normalise_phone(raw) -> str:
    """Accepts 08031234567, 8031234567, 2348031234567, +234 803 123 4567,
    0803-123-4567. Returns +2348031234567 or raises ValueError."""
    if raw is None:
        raise ValueError("phone is missing")
    text = str(raw).strip()
    if text.endswith(".0"):  # spreadsheets turn 8031234567 into 8031234567.0
        text = text[:-2]
    digits = re.sub(r"[\s\-().]", "", text)
    if digits.startswith("+"):
        digits = digits[1:]
    if not digits.isdigit():
        raise ValueError(f"'{raw}' is not a phone number")
    if digits.startswith("234"):
        national = digits[3:]
        if national.startswith("0"):
            national = national[1:]
    elif digits.startswith("0"):
        national = digits[1:]
    else:
        national = digits
    if len(national) != 10 or not national.startswith(_MOBILE_PREFIXES):
        raise ValueError(f"'{raw}' is not a valid Nigerian mobile number")
    return "+234" + national
