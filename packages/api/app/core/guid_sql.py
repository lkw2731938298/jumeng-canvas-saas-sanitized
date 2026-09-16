"""Helpers for cross-table UUID comparisons in MySQL."""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.sql.elements import ColumnElement


def guid_eq(left: ColumnElement, right: ColumnElement):
    """Compare UUID columns across tables without collation mismatch (MySQL 1267)."""
    return func.binary(left) == func.binary(right)
