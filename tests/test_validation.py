from decimal import Decimal

import pytest

from app.validation import (
    ValidationFailed,
    parse_payment_body,
    validate_idempotency_key,
)


# Idempotency-Key header

def test_valid_key_is_returned():
    assert validate_idempotency_key("order-8812") == "order-8812"


@pytest.mark.parametrize("key", [None, "", "   "])
def test_missing_or_blank_key_is_rejected(key):
    with pytest.raises(ValidationFailed, match="Missing Idempotency-Key"):
        validate_idempotency_key(key)


def test_key_of_255_characters_is_accepted():
    assert validate_idempotency_key("k" * 255) == "k" * 255


def test_key_longer_than_255_characters_is_rejected():
    with pytest.raises(ValidationFailed, match="at most 255"):
        validate_idempotency_key("k" * 256)


# Valid bodies

@pytest.mark.parametrize(
    "raw, amount, currency",
    [
        (b'{"amount": 100, "currency": "GHS"}', Decimal("100"), "GHS"),
        (b'{"currency": "USD", "amount": 0.1}', Decimal("0.1"), "USD"),
        (b'{"amount": 100.50, "currency": "EUR"}', Decimal("100.50"), "EUR"),
        (b'{"amount": 100.500, "currency": "GBP"}', Decimal("100.500"), "GBP"),
        (b'{"amount": 0.01, "currency": "NGN"}', Decimal("0.01"), "NGN"),
    ],
)
def test_valid_body_is_parsed(raw, amount, currency):
    payment = parse_payment_body(raw)
    assert payment.amount == amount
    assert isinstance(payment.amount, Decimal)
    assert payment.currency == currency


# Valid JSON object, no duplicates, no NaN or Infinity

@pytest.mark.parametrize(
    "raw",
    [b"", b"hello", b'{"amount": 100', b"[1, 2]", b'"text"', b"100"],
)
def test_body_that_is_not_a_json_object_is_rejected(raw):
    with pytest.raises(ValidationFailed, match="valid JSON object"):
        parse_payment_body(raw)


def test_duplicate_field_is_rejected():
    with pytest.raises(ValidationFailed, match="Duplicate field: amount"):
        parse_payment_body(b'{"amount": 100, "amount": 500, "currency": "GHS"}')


@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity"])
def test_nan_and_infinity_are_rejected(constant):
    with pytest.raises(ValidationFailed, match="is not allowed"):
        parse_payment_body(b'{"amount": ' + constant + b', "currency": "GHS"}')


# Exactly amount and currency

@pytest.mark.parametrize(
    "raw",
    [
        b"{}",
        b'{"amount": 100}',
        b'{"currency": "GHS"}',
        b'{"amount": 100, "currency": "GHS", "note": "x"}',
    ],
)
def test_wrong_fields_are_rejected(raw):
    with pytest.raises(ValidationFailed, match="exactly the fields"):
        parse_payment_body(raw)


# amount is a JSON number

@pytest.mark.parametrize("amount", [b'"100"', b"true", b"false", b"null", b"[100]"])
def test_amount_that_is_not_a_number_is_rejected(amount):
    with pytest.raises(ValidationFailed, match="amount must be a number"):
        parse_payment_body(b'{"amount": ' + amount + b', "currency": "GHS"}')


# amount greater than 0

@pytest.mark.parametrize("amount", [b"0", b"0.00", b"-5"])
def test_zero_or_negative_amount_is_rejected(amount):
    with pytest.raises(ValidationFailed, match="greater than 0"):
        parse_payment_body(b'{"amount": ' + amount + b', "currency": "GHS"}')


# At most 2 decimal places

def test_amount_with_three_decimal_places_is_rejected():
    with pytest.raises(ValidationFailed, match="at most 2 decimal places"):
        parse_payment_body(b'{"amount": 100.555, "currency": "GHS"}')


# Supported currency

@pytest.mark.parametrize("currency", [b'"ghs"', b'"XYZ"', b'"JPY"', b"100", b"null"])
def test_unsupported_currency_is_rejected(currency):
    with pytest.raises(ValidationFailed, match="currency must be one of"):
        parse_payment_body(b'{"amount": 100, "currency": ' + currency + b"}")
