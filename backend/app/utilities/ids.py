"""Identifier helpers.

``new_sort_key`` produces a lexicographically sortable, collision-resistant
string used as the DynamoDB sort key for audit events. Sorting by string then
sorts by time, which is what makes "show me everything this user did between
09:00 and 10:00" a range query rather than a scan.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from uuid import UUID, uuid4


def new_id() -> UUID:
    return uuid4()


def new_request_id() -> str:
    return f"req_{secrets.token_hex(8)}"


def new_sort_key(moment: datetime | None = None) -> str:
    """``<iso-8601 utc>#<random>`` — time-ordered and unique under concurrency."""
    stamp = (moment or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="microseconds")
    return f"{stamp}#{secrets.token_hex(4)}"
