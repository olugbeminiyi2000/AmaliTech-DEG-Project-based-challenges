"""Canonical fingerprint of a payment body (flowchart box D)."""

import hashlib
import json

from app.validation import PaymentRequest


def canonical_body_hash(payment: PaymentRequest) -> str:
    """SHA-256 of the payment in one fixed form.

    Two bodies describing the same payment get the same hash, even if the
    fields were in a different order or the amount was written as 100.5,
    100.50 or 100.500.
    """
    canonical = json.dumps(
        {
            # normalize() removes trailing zeros; format "f" avoids
            # scientific notation (Decimal("100").normalize() is 1E+2)
            "amount": format(payment.amount.normalize(), "f"),
            "currency": payment.currency,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
