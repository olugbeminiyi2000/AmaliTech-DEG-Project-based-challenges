"""Request validation for POST /process-payment.

Each check matches a decision box in the README flowchart (B1, B2, C0 to C5)
and runs in the same order. The first failing check raises ValidationFailed,
which the API turns into a 400 response.
"""

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

MAX_KEY_LENGTH = 255
SUPPORTED_CURRENCIES = frozenset({"GHS", "NGN", "USD", "EUR", "GBP"})
MAX_DECIMAL_PLACES = 2
REQUIRED_FIELDS = {"amount", "currency"}


class ValidationFailed(Exception):
    """A request broke one of the validation rules."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class PaymentRequest:
    amount: Decimal
    currency: str


def validate_idempotency_key(key: Optional[str]) -> str:
    # B1: header present and not blank
    if key is None or key.strip() == "":
        raise ValidationFailed("Missing Idempotency-Key header.")

    # B2: key at most 255 characters
    if len(key) > MAX_KEY_LENGTH:
        raise ValidationFailed(
            f"Idempotency-Key must be at most {MAX_KEY_LENGTH} characters."
        )

    return key


def parse_payment_body(raw_body: bytes) -> PaymentRequest:
    # C0: valid JSON object with no duplicate fields
    body = _parse_json_object(raw_body)

    # C1: exactly amount and currency
    if set(body.keys()) != REQUIRED_FIELDS:
        raise ValidationFailed(
            "Request body must contain exactly the fields amount and currency."
        )

    amount = body["amount"]
    currency = body["currency"]

    # C2: amount is a JSON number, not a string or boolean.
    # bool is checked first because in Python True and False are also ints.
    if isinstance(amount, bool) or not isinstance(amount, (int, Decimal)):
        raise ValidationFailed("amount must be a number.")
    amount = Decimal(amount)

    # C3: amount greater than 0
    if amount <= 0:
        raise ValidationFailed("amount must be greater than 0.")

    # C4: at most 2 decimal places. normalize() drops trailing zeros first,
    # so 100.500 is treated as 100.5 and accepted.
    if -amount.normalize().as_tuple().exponent > MAX_DECIMAL_PLACES:
        raise ValidationFailed(
            f"amount must have at most {MAX_DECIMAL_PLACES} decimal places."
        )

    # C5: currency is in the supported list
    if not isinstance(currency, str) or currency not in SUPPORTED_CURRENCIES:
        supported = ", ".join(sorted(SUPPORTED_CURRENCIES))
        raise ValidationFailed(f"currency must be one of: {supported}.")

    return PaymentRequest(amount=amount, currency=currency)


def _parse_json_object(raw_body: bytes) -> dict:
    try:
        body = json.loads(
            raw_body,
            # Decimal instead of float, so 0.10 stays exactly 0.10
            parse_float=Decimal,
            # Python accepts NaN and Infinity by default; JSON does not allow them
            parse_constant=_reject_constant,
            # Python silently keeps the last of duplicate fields; reject instead
            object_pairs_hook=_reject_duplicate_fields,
        )
    except ValueError:
        # Malformed JSON. ValidationFailed from the hooks below is not a
        # ValueError, so it passes through with its own specific message.
        raise ValidationFailed("Request body must be a valid JSON object.")

    if not isinstance(body, dict):
        raise ValidationFailed("Request body must be a valid JSON object.")

    return body


def _reject_constant(name: str):
    raise ValidationFailed(f"{name} is not allowed.")


def _reject_duplicate_fields(pairs: list) -> dict:
    result = {}
    for field, value in pairs:
        if field in result:
            raise ValidationFailed(f"Duplicate field: {field}.")
        result[field] = value
    return result
