"""L4 — Reconciliation standard (WCDS spec §9).

Compares portfolio-level control totals computed from canonicalized rows against
declared source controls (when the uploader supplies them — e.g. "the LMS export
says 3,050 live facilities totalling ₦X outstanding"). Absent a declared control,
we still record the computed total so it's available for the next reconciliation
(investor tape vs WCDS export, etc.) — see WCDS-to-destination control in the spec.
"""

from dataclasses import dataclass

TOLERANCE_PCT = 0.005  # 0.5% — reasonable rounding/timing tolerance, not a spec-mandated figure


@dataclass
class ReconciliationResult:
    control_name: str
    source_control_total: float | None
    wcds_control_total: float
    variance: float | None
    within_tolerance: bool | None
    notes: str = ""


def _variance_ok(source: float, computed: float) -> bool:
    if source == 0:
        return computed == 0
    return abs(computed - source) / abs(source) <= TOLERANCE_PCT


def reconcile(
    rows: list[dict],
    declared_facility_count: int | None = None,
    declared_principal_total: float | None = None,
    declared_exposure_total: float | None = None,
) -> list[ReconciliationResult]:
    live_statuses = {"ACTIVE", "SUSPENDED", "PENDING"}
    live_rows = [row for row in rows if row.get("facility_status") in live_statuses]

    facility_count = len({row.get("facility_id") or row.get("source_facility_id") for row in rows})
    principal_total = sum(float(row.get("outstanding_principal") or 0) for row in rows)
    exposure_total = sum(float(row.get("total_exposure") or 0) for row in rows)
    live_balance_total = sum(float(row.get("outstanding_principal") or 0) for row in live_rows)

    results = [
        ReconciliationResult(
            control_name="facility_count",
            source_control_total=float(declared_facility_count) if declared_facility_count is not None else None,
            wcds_control_total=float(facility_count),
            variance=(facility_count - declared_facility_count) if declared_facility_count is not None else None,
            within_tolerance=(
                _variance_ok(declared_facility_count, facility_count) if declared_facility_count is not None else None
            ),
        ),
        ReconciliationResult(
            control_name="principal_control",
            source_control_total=declared_principal_total,
            wcds_control_total=principal_total,
            variance=(principal_total - declared_principal_total) if declared_principal_total is not None else None,
            within_tolerance=(
                _variance_ok(declared_principal_total, principal_total) if declared_principal_total is not None else None
            ),
        ),
        ReconciliationResult(
            control_name="exposure_control",
            source_control_total=declared_exposure_total,
            wcds_control_total=exposure_total,
            variance=(exposure_total - declared_exposure_total) if declared_exposure_total is not None else None,
            within_tolerance=(
                _variance_ok(declared_exposure_total, exposure_total) if declared_exposure_total is not None else None
            ),
        ),
        ReconciliationResult(
            control_name="crms_400d_live_balance",
            source_control_total=None,
            wcds_control_total=live_balance_total,
            variance=None,
            within_tolerance=None,
            notes=f"Sum of outstanding_principal across {len(live_rows)} live (ACTIVE/SUSPENDED/PENDING) facilities.",
        ),
    ]
    return results
