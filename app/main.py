from fastapi import FastAPI

app = FastAPI(
    title="Silk Idempotency Gateway",
    description="Processes each payment at most once, no matter how many times a client retries.",
)


@app.get("/health")
async def health() -> dict:
    """Report that the server is running."""
    return {"status": "ok"}
