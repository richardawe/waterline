"""L1 — Field Mapping (WCDS spec §16 stage 4).

Auto-proposes a source-column -> WCDS-field mapping, accepts caller overrides,
and reports which source columns are unmapped ("explicitly ignored with reason"
per spec §3 L1). Every used mapping later becomes a TransformationEvent (rule_id
'MAP-AUTO' or 'MAP-MANUAL') so lineage is traceable back to the exact source column.
"""

from dataclasses import dataclass

from app.ingest.field_registry import FIELDS_BY_NAME, suggest_field


@dataclass
class MappingResult:
    column_to_field: dict[str, str]  # source_column -> wcds_field_name
    unmapped_columns: list[str]
    missing_required_fields: list[str]
    auto_matched: dict[str, str]  # subset of column_to_field that came from auto-matching


def propose_mapping(source_columns: list[str], overrides: dict[str, str] | None = None) -> MappingResult:
    """overrides: caller-supplied source_column -> wcds_field_name, takes precedence
    over auto-matching. Pass wcds_field_name="" to explicitly ignore a column."""
    overrides = overrides or {}
    column_to_field: dict[str, str] = {}
    auto_matched: dict[str, str] = {}
    unmapped: list[str] = []
    used_fields: set[str] = set()

    for col in source_columns:
        if col in overrides:
            target = overrides[col]
            if target:
                column_to_field[col] = target
                used_fields.add(target)
            else:
                unmapped.append(col)
            continue
        guess = suggest_field(col)
        if guess and guess not in used_fields:
            column_to_field[col] = guess
            auto_matched[col] = guess
            used_fields.add(guess)
        else:
            unmapped.append(col)

    missing_required = [
        name for name, spec in FIELDS_BY_NAME.items() if spec.required and name not in used_fields
    ]

    return MappingResult(
        column_to_field=column_to_field,
        unmapped_columns=unmapped,
        missing_required_fields=missing_required,
        auto_matched=auto_matched,
    )
