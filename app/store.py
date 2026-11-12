"""In-memory store of idempotency records.

No method here contains an await, so the event loop can never switch to
another request in the middle of one. That is what makes "look up the key,
then claim it" atomic without a lock, as long as the app runs in a single
process. Several processes or servers would each have their own copy of this
store, so production needs a shared store such as Redis.
"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, Optional


class State(Enum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"


@dataclass(frozen=True)
class SavedResponse:
    status_code: int
    body: dict


@dataclass
class Record:
    body_hash: str
    state: State = State.PROCESSING
    response: Optional[SavedResponse] = None
    # Only COMPLETED records have an expiry
    expires_at: Optional[float] = None
    # Set when the first request finishes, waking every request waiting on it
    done: asyncio.Event = field(default_factory=asyncio.Event)
    # True if the first request failed; waiters read this after waking
    failed: bool = False


class IdempotencyStore:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        # monotonic time only moves forward, so changing the system clock
        # cannot make keys expire early or live forever
        self._clock = clock
        self._records: Dict[str, Record] = {}

    def get(self, key: str) -> Optional[Record]:
        """Return the live record for a key, or None (boxes E1, E2, E3)."""
        record = self._records.get(key)
        if record is None:
            return None
        if self._is_expired(record):
            del self._records[key]
            return None
        return record

    def create(self, key: str, body_hash: str) -> Record:
        """Claim a key with a new PROCESSING record (box F)."""
        record = Record(body_hash=body_hash)
        self._records[key] = record
        return record

    def complete(self, record: Record, response: SavedResponse, ttl_seconds: float) -> None:
        """Save the result and wake waiters (boxes I and W1)."""
        record.state = State.COMPLETED
        record.response = response
        record.expires_at = self._clock() + ttl_seconds
        record.done.set()

    def fail(self, key: str, record: Record) -> None:
        """Delete the record so a retry starts fresh, then wake waiters (boxes J and W2)."""
        if self._records.get(key) is record:
            del self._records[key]
        record.failed = True
        record.done.set()

    def purge_expired(self) -> int:
        """Remove every expired record. Returns how many were removed."""
        expired = [key for key, record in self._records.items() if self._is_expired(record)]
        for key in expired:
            del self._records[key]
        return len(expired)

    def __len__(self) -> int:
        return len(self._records)

    def _is_expired(self, record: Record) -> bool:
        return record.expires_at is not None and self._clock() >= record.expires_at
