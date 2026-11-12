import asyncio
import time

import httpx
import pytest

from app.config import Settings
from app.main import create_app

DELAY = 0.3
FAST = Settings(
    processing_delay_seconds=DELAY,
    bank_timeout_seconds=2.0,
    max_wait_seconds=3.0,
    key_ttl_seconds=60.0,
)
SIMULATED = Settings(
    processing_delay_seconds=DELAY,
    bank_timeout_seconds=1.0,
    max_wait_seconds=3.0,
    key_ttl_seconds=60.0,
    simulation_mode=True,
)
PAYMENT = {"amount": 100, "currency": "GHS"}
BANK_FAILS = {"X-Simulate-Bank-Failure": "true"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def pay(client, key, body=PAYMENT, extra_headers=None):
    headers = {} if key is None else {"Idempotency-Key": key}
    headers.update(extra_headers or {})
    return await client.post("/process-payment", json=body, headers=headers)


# User Story 1: first transaction

@pytest.mark.anyio
async def test_first_request_is_processed_and_charged():
    app = create_app(FAST)
    async with client_for(app) as client:
        started = time.monotonic()
        response = await pay(client, "key-1")
        elapsed = time.monotonic() - started

    assert response.status_code == 201
    assert response.json()["message"] == "Charged 100 GHS"
    assert response.headers["X-Cache-Hit"] == "false"
    assert elapsed >= DELAY
    assert app.state.bank.charge_count == 1


# User Story 2: duplicate attempt

@pytest.mark.anyio
async def test_duplicate_request_replays_saved_response_without_charging():
    app = create_app(FAST)
    async with client_for(app) as client:
        first = await pay(client, "key-1")
        started = time.monotonic()
        second = await pay(client, "key-1")
        elapsed = time.monotonic() - started

    assert second.status_code == first.status_code
    assert second.json() == first.json()
    assert second.headers["X-Cache-Hit"] == "true"
    assert elapsed < DELAY
    assert app.state.bank.charge_count == 1


@pytest.mark.anyio
async def test_same_payment_written_differently_counts_as_duplicate():
    app = create_app(FAST)
    async with client_for(app) as client:
        first = await client.post(
            "/process-payment",
            content=b'{"amount": 100.5, "currency": "USD"}',
            headers={"Idempotency-Key": "key-1"},
        )
        second = await client.post(
            "/process-payment",
            content=b'{"currency": "USD", "amount": 100.500}',
            headers={"Idempotency-Key": "key-1"},
        )

    assert first.json()["message"] == "Charged 100.50 USD"
    assert second.json() == first.json()
    assert second.headers["X-Cache-Hit"] == "true"
    assert app.state.bank.charge_count == 1


# User Story 3: same key, different body

@pytest.mark.anyio
async def test_same_key_with_different_body_is_rejected():
    app = create_app(FAST)
    async with client_for(app) as client:
        await pay(client, "key-1")
        response = await pay(client, "key-1", {"amount": 500, "currency": "GHS"})

    assert response.status_code == 422
    assert response.json() == {"error": "Idempotency key already used for a different request body."}
    assert app.state.bank.charge_count == 1


@pytest.mark.anyio
async def test_different_body_is_rejected_even_while_first_is_processing():
    app = create_app(FAST)
    async with client_for(app) as client:
        first_task = asyncio.create_task(pay(client, "key-1"))
        await asyncio.sleep(DELAY / 3)
        second = await pay(client, "key-1", {"amount": 500, "currency": "GHS"})
        first = await first_task

    assert first.status_code == 201
    assert second.status_code == 422
    assert app.state.bank.charge_count == 1


# Bonus: in-flight duplicate waits for the first request

@pytest.mark.anyio
async def test_simultaneous_duplicates_are_charged_once_and_get_same_result():
    app = create_app(FAST)
    async with client_for(app) as client:
        first, second = await asyncio.gather(pay(client, "key-1"), pay(client, "key-1"))

    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    assert sorted([first.headers["X-Cache-Hit"], second.headers["X-Cache-Hit"]]) == ["false", "true"]
    assert app.state.bank.charge_count == 1


@pytest.mark.anyio
async def test_waiting_request_gets_503_when_max_wait_is_reached():
    settings = Settings(processing_delay_seconds=1.0, bank_timeout_seconds=2.0, max_wait_seconds=0.2)
    app = create_app(settings)
    async with client_for(app) as client:
        first_task = asyncio.create_task(pay(client, "key-1"))
        await asyncio.sleep(0.1)
        waiting = await pay(client, "key-1")
        first = await first_task
        retry = await pay(client, "key-1")

    assert waiting.status_code == 503
    assert waiting.headers["Retry-After"] == "1"
    assert first.status_code == 201
    # The record was kept, so the retry finds the finished result
    assert retry.json() == first.json()
    assert retry.headers["X-Cache-Hit"] == "true"
    assert app.state.bank.charge_count == 1


# Failures

@pytest.mark.anyio
async def test_failed_processing_returns_500_and_releases_the_key():
    app = create_app(SIMULATED)
    async with client_for(app) as client:
        failed = await pay(client, "key-1", extra_headers=BANK_FAILS)
        assert failed.status_code == 500
        assert failed.json() == {"error": "Payment processing failed. Retry with the same Idempotency-Key."}
        assert len(app.state.store) == 0

        # The bank recovers; the retry with the same key is processed fresh
        retry = await pay(client, "key-1")

    assert retry.status_code == 201
    assert retry.headers["X-Cache-Hit"] == "false"
    assert app.state.bank.charge_count == 2


@pytest.mark.anyio
async def test_waiting_request_gets_500_when_first_request_fails():
    app = create_app(SIMULATED)
    async with client_for(app) as client:
        first, second = await asyncio.gather(
            pay(client, "key-1", extra_headers=BANK_FAILS),
            pay(client, "key-1"),
        )

    assert first.status_code == second.status_code == 500
    assert app.state.bank.charge_count == 1


@pytest.mark.anyio
async def test_bank_timeout_is_treated_as_failure():
    settings = Settings(processing_delay_seconds=1.0, bank_timeout_seconds=0.1)
    app = create_app(settings)
    async with client_for(app) as client:
        response = await pay(client, "key-1")

    assert response.status_code == 500
    assert len(app.state.store) == 0


# Expiry

@pytest.mark.anyio
async def test_expired_key_is_processed_as_new():
    settings = Settings(processing_delay_seconds=0.05, key_ttl_seconds=0.2)
    app = create_app(settings)
    async with client_for(app) as client:
        first = await pay(client, "key-1")
        await asyncio.sleep(0.3)
        second = await pay(client, "key-1")

    assert second.status_code == 201
    assert second.headers["X-Cache-Hit"] == "false"
    assert second.json()["transaction_id"] != first.json()["transaction_id"]
    assert app.state.bank.charge_count == 2


@pytest.mark.anyio
async def test_purge_removes_only_expired_keys():
    settings = Settings(processing_delay_seconds=0.05, key_ttl_seconds=0.2)
    app = create_app(settings)
    async with client_for(app) as client:
        await pay(client, "old")
        await asyncio.sleep(0.3)
        await pay(client, "new")

    assert app.state.store.purge_expired() == 1
    assert len(app.state.store) == 1


# Validation through the API

@pytest.mark.anyio
async def test_missing_key_returns_400():
    app = create_app(FAST)
    async with client_for(app) as client:
        response = await pay(client, None)

    assert response.status_code == 400
    assert response.json() == {"error": "Missing Idempotency-Key header."}


@pytest.mark.anyio
async def test_invalid_body_returns_400_and_does_not_use_up_the_key():
    app = create_app(FAST)
    async with client_for(app) as client:
        bad = await pay(client, "key-1", {"amount": "100", "currency": "GHS"})
        good = await pay(client, "key-1")

    assert bad.status_code == 400
    assert good.status_code == 201
    assert good.headers["X-Cache-Hit"] == "false"


# Simulation mode

@pytest.mark.anyio
async def test_slow_bank_header_triggers_bank_timeout_in_simulation_mode():
    app = create_app(SIMULATED)
    async with client_for(app) as client:
        started = time.monotonic()
        response = await pay(client, "key-1", extra_headers={"X-Simulate-Bank-Delay": "5"})
        elapsed = time.monotonic() - started

    assert response.status_code == 500
    # Gave up at the 1 second bank timeout, not after the 5 second delay
    assert elapsed < 2


@pytest.mark.anyio
async def test_simulation_headers_are_ignored_when_simulation_mode_is_off():
    app = create_app(FAST)
    async with client_for(app) as client:
        response = await pay(client, "key-1", extra_headers=BANK_FAILS)

    assert response.status_code == 201


@pytest.mark.anyio
@pytest.mark.parametrize(
    "headers",
    [
        {"X-Simulate-Bank-Delay": "abc"},
        {"X-Simulate-Bank-Delay": "-1"},
        {"X-Simulate-Bank-Delay": "31"},
        {"X-Simulate-Bank-Failure": "maybe"},
    ],
)
async def test_invalid_simulation_headers_return_400(headers):
    app = create_app(SIMULATED)
    async with client_for(app) as client:
        response = await pay(client, "key-1", extra_headers=headers)

    assert response.status_code == 400
    assert len(app.state.store) == 0


# Health, root and API docs

@pytest.mark.anyio
async def test_health_reports_simulation_mode():
    async with client_for(create_app(SIMULATED)) as client:
        response = await client.get("/health")

    assert response.json() == {"status": "ok", "simulation_mode": True}


@pytest.mark.anyio
async def test_root_redirects_to_api_docs():
    async with client_for(create_app(FAST)) as client:
        response = await client.get("/")

    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/docs"


@pytest.mark.anyio
async def test_api_docs_describe_headers_body_and_responses():
    async with client_for(create_app(FAST)) as client:
        spec = (await client.get("/openapi.json")).json()

    operation = spec["paths"]["/process-payment"]["post"]
    header_names = {p["name"] for p in operation["parameters"] if p["in"] == "header"}
    assert header_names == {"Idempotency-Key", "X-Simulate-Bank-Delay", "X-Simulate-Bank-Failure"}
    assert operation["requestBody"]["content"]["application/json"]["example"] == {"amount": 100, "currency": "GHS"}
    assert {"201", "400", "422", "500", "503"} <= set(operation["responses"])
