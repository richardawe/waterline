"""Recovery rules (per batch, or the client's default) and the payment
options they generate for a debtor."""

import json
import math
from datetime import date, timedelta

from app.collections.models import Client, DebtorAccount, UploadBatch
from app.collections.money import percent_of

DEFAULT_RULES = {
    "min_instalment": 500_000,  # ₦5,000 in kobo
    "max_instalments": 3,
    "discount_percent": 0,
    "discount_deadline": None,
    "start_date": None,
}

FIRST_INSTALMENT_DAYS = 7
INSTALMENT_INTERVAL_DAYS = 30


class RulesInvalid(ValueError):
    pass


def validate_rules(raw: dict) -> dict:
    rules = dict(DEFAULT_RULES)
    rules.update({k: v for k, v in (raw or {}).items() if k in DEFAULT_RULES})
    try:
        rules["min_instalment"] = int(rules["min_instalment"])
        rules["max_instalments"] = int(rules["max_instalments"])
        rules["discount_percent"] = float(rules["discount_percent"] or 0)
    except (TypeError, ValueError) as exc:
        raise RulesInvalid("Rules must be numbers.") from exc
    if rules["min_instalment"] < 0:
        raise RulesInvalid("Minimum instalment can't be negative.")
    if not 1 <= rules["max_instalments"] <= 6:
        raise RulesInvalid("Maximum number of instalments must be between 1 and 6.")
    if not 0 <= rules["discount_percent"] <= 50:
        raise RulesInvalid("Discount must be between 0% and 50%.")
    for key in ("discount_deadline", "start_date"):
        if rules[key]:
            try:
                rules[key] = date.fromisoformat(str(rules[key])).isoformat()
            except ValueError as exc:
                raise RulesInvalid(f"{key} must be a date (YYYY-MM-DD).") from exc
        else:
            rules[key] = None
    if rules["discount_percent"] and not rules["discount_deadline"]:
        raise RulesInvalid("A full-payment discount needs a deadline.")
    return rules


def effective_rules(account: DebtorAccount, batch: UploadBatch | None, client: Client) -> dict:
    for source in (batch.rules if batch else None, client.default_rules):
        if source:
            return validate_rules(json.loads(source))
    return dict(DEFAULT_RULES)


def active_discount(rules: dict, balance: int, today: date) -> dict | None:
    pct, deadline = rules.get("discount_percent") or 0, rules.get("discount_deadline")
    if not pct or not deadline or balance <= 0:
        return None
    deadline_date = date.fromisoformat(deadline)
    if today > deadline_date:
        return None
    discount = percent_of(balance, pct)
    return {"percent": pct, "deadline": deadline, "discount_amount": discount, "pay_amount": balance - discount}


def plan_options(rules: dict, balance: int, today: date) -> list[dict]:
    options = []
    for n in range(2, rules["max_instalments"] + 1):
        amount = math.ceil(balance / n / 100) * 100  # whole naira, rounded up
        if amount < rules["min_instalment"] or amount * (n - 1) >= balance:
            continue
        options.append({"instalments": n, "instalment_amount": amount, "schedule": schedule(balance, n, amount, today)})
    return options


def schedule(balance: int, n: int, amount: int, today: date) -> list[dict]:
    out, remaining = [], balance
    for i in range(n):
        this = amount if i < n - 1 else remaining
        out.append({"due": (today + timedelta(days=FIRST_INSTALMENT_DAYS + i * INSTALMENT_INTERVAL_DAYS)).isoformat(), "amount": this})
        remaining -= this
    return out
