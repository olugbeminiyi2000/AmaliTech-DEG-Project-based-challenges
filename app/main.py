import asyncio
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Optional

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app import api_docs
from app.bank import SimulatedBank
from app.config import Settings
from app.hashing import canonical_body_hash
from app.simulation import (
    DELAY_HEADER,
    FAILURE_HEADER,
    NO_SIMULATION,
    BankSimulation,
    parse_simulation_headers,
)
from app.store import IdempotencyStore, Record, SavedResponse, State
from app.validation import (
    PaymentRequest,
    ValidationFailed,
    parse_payment_body,
    validate_idempotency_key,
)

KEY_REUSED_MESSAGE = "Idempotency key already used for a different request body."
PROCESSING_FAILED_MESSAGE = "Payment processing failed. Retry with the same Idempotency-Key."
STILL_PROCESSING_MESSAGE = "A request with this Idempotency-Key is still processing. Retry shortly."


def create_app(settings: Optional[Settings] = None, bank: Optional[SimulatedBank] = None) -> FastAPI:
    settings = settings or Settings()
    bank = bank or SimulatedBank(delay_seconds=settings.processing_delay_seconds)
    store = IdempotencyStore()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        cleaner = asyncio.create_task(_remove_expired_keys(store, settings.cleanup_interval_seconds))
        yield
        cleaner.cancel()

    app = FastAPI(
        title="Silk Idempotency Gateway",
        description="Processes each payment at most once, no matter how many times a client retries.",
        lifespan=lifespan,
    )
    app.state.store = store
    app.state.bank = bank

    generate_openapi = app.openapi

    def openapi() -> dict:
        # Build the schema once with FastAPI's generator, then describe the
        # headers as string, number and boolean for /docs
        if app.openapi_schema is None:
            api_docs.apply_header_schemas(generate_openapi())
        return app.openapi_schema

    app.openapi = openapi

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        return RedirectResponse("/docs")

    @app.get("/health")
    async def health() -> dict:
        """Report that the server is running and whether simulation mode is on."""
        return {"status": "ok", "simulation_mode": settings.simulation_mode}

    @app.post(
        "/process-payment",
        status_code=201,
        responses=api_docs.RESPONSES,
        openapi_extra=api_docs.REQUEST_BODY,
    )
    async def process_payment(
        request: Request,
        # Declared as plain strings so /docs shows input fields; the rules
        # are checked by our own validation, in the flowchart's order.
        idempotency_key: Optional[str] = Header(
            None, alias="Idempotency-Key", description=api_docs.IDEMPOTENCY_KEY_DESCRIPTION
        ),
        simulate_bank_delay: Optional[str] = Header(
            None, alias=DELAY_HEADER, description=api_docs.SIMULATE_DELAY_DESCRIPTION
        ),
        simulate_bank_failure: Optional[str] = Header(
            None, alias=FAILURE_HEADER, description=api_docs.SIMULATE_FAILURE_DESCRIPTION
        ),
    ) -> JSONResponse:
        """Charge a payment exactly once per Idempotency-Key.

        The first request with a key is processed (about 2 seconds) and its
        response saved. A retry with the same key and body returns the saved
        response immediately with X-Cache-Hit: true. Reusing the key with a
        different body returns 422.
        """
        # B1 to C5: validate before touching the store
        try:
            key = validate_idempotency_key(idempotency_key)
            payment = parse_payment_body(await request.body())
            simulation = (
                parse_simulation_headers(simulate_bank_delay, simulate_bank_failure)
                if settings.simulation_mode
                else NO_SIMULATION
            )
        except ValidationFailed as error:
            return _error(400, error.message)

        # D
        body_hash = canonical_body_hash(payment)

        # E1 to F: no await between get() and create(), so no other request
        # can claim the same key in between
        record = store.get(key)
        if record is None:
            record = store.create(key, body_hash)
            return await _process_new_payment(key, record, payment, simulation)

        # K
        if record.body_hash != body_hash:
            return _error(422, KEY_REUSED_MESSAGE)

        # L
        if record.state is State.COMPLETED:
            return _replay(record)

        # M, N: still PROCESSING, so wait for the first request to finish
        try:
            await asyncio.wait_for(record.done.wait(), timeout=settings.max_wait_seconds)
        except asyncio.TimeoutError:
            return _error(503, STILL_PROCESSING_MESSAGE, headers={"Retry-After": "1"})
        if record.failed:
            return _error(500, PROCESSING_FAILED_MESSAGE)
        return _replay(record)

    async def _process_new_payment(
        key: str, record: Record, payment: PaymentRequest, simulation: BankSimulation
    ) -> JSONResponse:
        # G, H
        try:
            transaction_id = await asyncio.wait_for(
                bank.charge(payment, delay_seconds=simulation.delay_seconds, fail=simulation.fail),
                timeout=settings.bank_timeout_seconds,
            )
        except asyncio.CancelledError:
            # The request itself was cancelled (for example the server is
            # shutting down). Release the key so it does not stay PROCESSING.
            store.fail(key, record)
            raise
        except Exception:
            # J, W2: any error, including the bank timing out
            store.fail(key, record)
            return _error(500, PROCESSING_FAILED_MESSAGE)

        # I, W1
        response = SavedResponse(
            status_code=201,
            body={
                "message": f"Charged {_format_amount(payment.amount)} {payment.currency}",
                "transaction_id": transaction_id,
                "amount": _format_amount(payment.amount),
                "currency": payment.currency,
            },
        )
        store.complete(record, response, ttl_seconds=settings.key_ttl_seconds)
        return JSONResponse(response.body, status_code=response.status_code, headers={"X-Cache-Hit": "false"})

    return app


def _replay(record: Record) -> JSONResponse:
    # P, R6
    saved = record.response
    return JSONResponse(saved.body, status_code=saved.status_code, headers={"X-Cache-Hit": "true"})


def _error(status_code: int, message: str, headers: Optional[dict] = None) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code, headers=headers)


def _format_amount(amount: Decimal) -> str:
    """100 -> "100", 100.5 -> "100.50"."""
    if amount == amount.to_integral_value():
        return str(amount.quantize(Decimal("1")))
    return str(amount.quantize(Decimal("0.01")))


async def _remove_expired_keys(store: IdempotencyStore, interval_seconds: float) -> None:
    """Background cleaner: forget expired keys nobody asks about again."""
    while True:
        await asyncio.sleep(interval_seconds)
        store.purge_expired()


app = create_app(Settings.from_env())
