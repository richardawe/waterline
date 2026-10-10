import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.collections.models import AuditLog


@dataclass
class Actor:
    type: str  # client / ops / debtor / system
    id: str | None = None
    ip: str | None = None
    client_id: str | None = None  # for client actors, the tenant they belong to
    role: str | None = None


SYSTEM = Actor(type="system")


def _dump(value) -> str | None:
    if value is None:
        return None
    return json.dumps(value, default=str, sort_keys=True)


def record(
    db: Session,
    actor: Actor,
    action: str,
    entity: str,
    entity_id: str | None,
    *,
    client_id: str | None = None,
    before=None,
    after=None,
) -> None:
    db.add(
        AuditLog(
            actor_type=actor.type,
            actor_id=actor.id,
            client_id=client_id or actor.client_id,
            action=action,
            entity=entity,
            entity_id=entity_id,
            before=_dump(before),
            after=_dump(after),
            ip=actor.ip,
        )
    )
