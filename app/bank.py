"""Stand-in for the external bank that actually moves the money."""

import asyncio
import uuid
from typing import Optional

from app.validation import PaymentRequest


class SimulatedBankError(Exception):
    """The simulated bank refused or could not complete the charge."""


class SimulatedBank:
    def __init__(self, delay_seconds: float):
        self.delay_seconds = delay_seconds
        # Counts charge attempts, so tests can prove a payment ran exactly once
        self.charge_count = 0

    async def charge(
        self,
        _payment: PaymentRequest,
        delay_seconds: Optional[float] = None,
        fail: bool = False,
    ) -> str:
        """Charge the payment and return a transaction id.

        delay_seconds and fail let tests and simulation mode simulate a slow
        or failing bank. A real bank client would use the payment details; the
        simulation does not need them.
        """
        self.charge_count += 1
        await asyncio.sleep(self.delay_seconds if delay_seconds is None else delay_seconds)
        if fail:
            raise SimulatedBankError("Simulated bank failure.")
        return f"txn_{uuid.uuid4().hex}"
