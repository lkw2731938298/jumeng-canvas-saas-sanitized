"""Generation job lifecycle status — DB stores the string value."""

from __future__ import annotations

from enum import Enum


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    POLLING = "polling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABNORMAL = "abnormal"
    AWAITING_APPROVAL = "awaiting_approval"

    @classmethod
    def terminal(cls) -> frozenset["JobStatus"]:
        return frozenset({cls.SUCCEEDED, cls.FAILED})

    @classmethod
    def in_flight(cls) -> frozenset["JobStatus"]:
        return frozenset({cls.RUNNING, cls.POLLING})
