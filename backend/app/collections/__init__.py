"""Waterline Collections — a standalone fee-recovery product for schools,
estates, gyms and service businesses, served under /collections.

Deliberately separate from the lender/loan-book product: its own tables
(`cl_*`), its own auth (client sessions, ops sessions with TOTP, debtor
token links) and no shared routes with the admin API. See
docs/collections.md for the operating reference."""
