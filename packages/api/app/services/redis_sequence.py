"""向后兼容：发号器已迁至 app.common.utils.redis_sequence。"""

from ..common.utils.redis_keys import sequence_key
from ..common.utils.redis_sequence import (
    SEQUENCE_GENERATION_JOB,
    allocate_sequence_range,
    bootstrap_sequence,
    next_daily_sequence,
    next_sequence,
)

__all__ = [
    "SEQUENCE_GENERATION_JOB",
    "allocate_sequence_range",
    "bootstrap_sequence",
    "next_daily_sequence",
    "next_sequence",
    "sequence_key",
]
