"""Credit reservation settlement status on generation jobs."""

from __future__ import annotations

from enum import Enum


class CreditStatus(str, Enum):
    SKIPPED = "skipped"
    RESERVED = "reserved"
    COMMITTED = "committed"
    RELEASED = "released"
    COMMIT_PENDING = "commit_pending"
    RELEASE_PENDING = "release_pending"
