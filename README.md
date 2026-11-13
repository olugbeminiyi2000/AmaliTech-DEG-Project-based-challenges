# Silk Idempotency Gateway

A payment-processing API that charges each payment **at most once**, no matter how many times a client retries the same request.

---

## Contents

- [1. Overview](#1-overview)
- [2. Architecture](#2-architecture)
- [3. Setup](#3-setup)
- [4. API Documentation](#4-api-documentation)

---

## 1. Overview

### 1.1 The problem

When a customer clicks "Pay", the online store's server sends a payment request to the payment processor. If the network times out, the store cannot tell what happened. The request may have been lost before it arrived, or the payment may have gone through and only the response was lost. The store's server retries the request automatically (and the customer may also click "Pay" again). If the payment processor treats the retry as a new payment, the customer is charged twice.

### 1.2 The solution

Every payment request carries an `Idempotency-Key` header: a unique id the client creates once per payment and sends again with every retry of that payment. The gateway remembers each key and what happened to it:

- **First request with a key:** the payment is processed (a simulated delayed bank call that takes 2 seconds), the response is saved, and `201 Created` is returned.
- **Retry with the same key and the same body:** the saved response is returned immediately with the header `X-Cache-Hit: true`. The payment is not processed again.
- **Same key with a different body:** rejected with `422 Unprocessable Entity`, so a key can never confirm a payment it was not created for.
- **Retry that arrives while the first request is still processing:** it waits for the first request to finish and returns the same result.

Completed keys are forgotten after 24 hours by default, and a background task removes them every 60 seconds, so memory does not grow forever. This is the Developer's Choice feature.

### 1.3 Tech stack

| Component | Used for |
|---|---|
| Python 3.12 | Language |
| FastAPI | Web framework: routes requests to the endpoint and generates the interactive API docs |
| Uvicorn | Server: listens for HTTP requests and runs the event loop |
| pytest, httpx | Automated tests, including simultaneous requests |

Keys are kept in memory, in a Python dictionary. There is no database.

---

## 2. Architecture

### 2.1 Request flow

Every path starts when a request arrives and ends with exactly one HTTP response.

```mermaid
flowchart TD
    A([POST /process-payment]) --> B1{Idempotency-Key header present and not blank?}
    B1 -- No --> R1([400 Bad Request, invalid Idempotency-Key])
    B1 -- Yes --> B2{Key at most 255 characters?}
    B2 -- No --> R1
    B2 -- Yes --> C0{Body is a valid JSON object with no duplicate fields?}
    C0 -- No --> R2([400 Bad Request, invalid body, key not stored])
    C0 -- Yes --> C1{Fields are exactly amount and currency?}
    C1 -- No --> R2
    C1 -- Yes --> C2{amount is a JSON number, not a string or boolean?}
    C2 -- No --> R2
    C2 -- Yes --> C3{amount greater than 0?}
    C3 -- No --> R2
    C3 -- Yes --> C4{amount has at most 2 decimal places?}
    C4 -- No --> R2
    C4 -- Yes --> C5{currency is GHS, NGN, USD, EUR or GBP?}
    C5 -- No --> R2
    C5 -- Yes --> D[Hash the canonical body]
    D --> E1{Record exists for this key?}
    E1 -- No --> F[Create record, state = PROCESSING, save body hash]
    E1 -- Yes --> E2{Record expired? Only COMPLETED records have an expiry}
    E2 -- Yes --> E3[Delete expired record]
    E3 --> F
    F --> G[Simulated delayed bank call, 2 seconds, with timeout]
    G --> H{Bank call finished without error or timeout?}
    H -- Yes --> I[Save status code and body in record, state = COMPLETED, expires_at = now + TTL]
    I --> W1[Wake any waiting requests: result ready]
    W1 --> R3([201 Created, X-Cache-Hit: false])
    H -- No --> J[Delete record]
    J --> W2[Wake any waiting requests: first request failed]
    W2 --> R4([500 Error, retry allowed])
    E2 -- No --> K{New body hash equals saved body hash?}
    K -- No --> R5([422 Unprocessable Entity, key used for a different body])
    K -- Yes --> L{Record state?}
    L -- COMPLETED --> P[Read saved status code and body from record]
    P --> R6([Replay saved response, X-Cache-Hit: true])
    L -- PROCESSING --> M[Wait for first request result, up to a max wait]
    M --> N{How did the wait end?}
    N -- Woken: result ready --> P
    N -- Woken: first request failed --> Q[Nothing to delete, first request already deleted the record]
    Q --> R4
    N -- Max wait reached --> R7([503 Service Unavailable, Retry-After header, record kept])
```

### 2.2 Project structure

What each file does.

| File | What it does |
|---|---|
| `app/__main__.py` | Start command: `python -m app` runs the server. Reads `HOST` and `PORT` |
| `app/main.py` | Creates the app. `POST /process-payment` follows the flowchart from top to bottom and builds every response. Also `GET /health`, the redirect from `/` to `/docs`, and the background task that removes expired keys |
| `app/validation.py` | Checks the `Idempotency-Key` header and the request body, in the flowchart's order. Parses the amount as `Decimal` |
| `app/hashing.py` | Turns a valid body into its canonical SHA-256 fingerprint |
| `app/store.py` | The in-memory key store: looks up, claims, completes, fails and expires records, and wakes waiting requests |
| `app/bank.py` | The simulated delayed bank call. Returns a transaction id |
| `app/config.py` | Settings and their defaults, read from environment variables |
| `app/simulation.py` | Reads the `X-Simulate-Bank-*` headers when simulation mode is on |
| `app/api_docs.py` | Descriptions of the headers, body and responses shown on `/docs` |
| `tests/test_validation.py` | Tests for every validation rule |
| `tests/test_api.py` | Tests for every user story, the in-flight bonus, failures, timeouts, expiry and the API docs |
| `tests/test_config.py` | Tests for reading settings from environment variables |
| `requirements.txt` | Exact versions of the packages to install |
| `pytest.ini` | Test runner settings |

---

## 3. Setup

### 3.1 Requirements

- Python 3.12 (the version this project was built and tested with, on Windows 11)
- Git

### 3.2 Install

Clone the repository:

```
git clone https://github.com/olugbeminiyi2000/AmaliTech-DEG-Project-based-challenges.git
cd AmaliTech-DEG-Project-based-challenges
```

Create a virtual environment and install the packages:

| Shell | Commands |
|---|---|
| Windows cmd or PowerShell | `py -3.12 -m venv .venv` then `.venv\Scripts\python -m pip install -r requirements.txt` |
| Git Bash on Windows | `py -3.12 -m venv .venv` then `.venv/Scripts/python -m pip install -r requirements.txt` |
| macOS or Linux | `python3.12 -m venv .venv` then `.venv/bin/python -m pip install -r requirements.txt` |

### 3.3 Run the server

| Shell | Command |
|---|---|
| Windows cmd or PowerShell | `.venv\Scripts\python -m app` |
| Git Bash on Windows | `.venv/Scripts/python -m app` |
| macOS or Linux | `.venv/bin/python -m app` |

The server starts on `http://127.0.0.1:8000`:

- `http://127.0.0.1:8000/docs` is the interactive API documentation, where every request can be tried in the browser. Opening `http://127.0.0.1:8000/` redirects here.
- `http://127.0.0.1:8000/health` confirms the server is running.

Stop the server with `Ctrl+C`.

### 3.4 Run the tests

| Shell | Command |
|---|---|
| Windows cmd or PowerShell | `.venv\Scripts\python -m pytest` |
| Git Bash on Windows | `.venv/Scripts/python -m pytest` |
| macOS or Linux | `.venv/bin/python -m pytest` |

All 67 tests should pass in about 10 seconds.

### 3.5 Configuration

Every setting has a default, so the server runs without any configuration. To change a setting, set an environment variable before starting the server.

| Variable | Default | What it controls |
|---|---|---|
| `PROCESSING_DELAY_SECONDS` | `2` | How long the simulated bank takes to charge a payment |
| `BANK_TIMEOUT_SECONDS` | `5` | A bank call that takes longer than this fails with `500` |
| `MAX_WAIT_SECONDS` | `10` | How long a duplicate request waits for an identical in-flight request before returning `503` |
| `KEY_TTL_SECONDS` | `86400` (24 hours) | How long a completed key is remembered |
| `CLEANUP_INTERVAL_SECONDS` | `60` | How often the background task removes expired keys |
| `SIMULATION_MODE` | `false` | Turns on the `X-Simulate-Bank-*` headers (see 3.6 Simulation mode) |
| `HOST` | `127.0.0.1` | Address the server listens on |
| `PORT` | `8000` | Port the server listens on |

Time values are in seconds and may be decimals, such as `0.5`. If a time value or `SIMULATION_MODE` is not valid (for example `BANK_TIMEOUT_SECONDS=five`, a negative number, or `SIMULATION_MODE=maybe`), the server refuses to start and prints which variable is wrong.

Setting variables differs between shells. For example, to start with simulation mode on and a 20 second bank timeout:

| Shell | Command |
|---|---|
| Windows cmd | `set SIMULATION_MODE=true& set BANK_TIMEOUT_SECONDS=20& .venv\Scripts\python -m app` |
| PowerShell | `$env:SIMULATION_MODE="true"; $env:BANK_TIMEOUT_SECONDS="20"; .venv\Scripts\python -m app` |
| Git Bash on Windows | `SIMULATION_MODE=true BANK_TIMEOUT_SECONDS=20 .venv/Scripts/python -m app` |
| macOS or Linux | `SIMULATION_MODE=true BANK_TIMEOUT_SECONDS=20 .venv/bin/python -m app` |

In cmd and PowerShell the variables stay set until that terminal is closed, so later commands in the same terminal use them too. In Git Bash, macOS and Linux, the form above applies only to that one command. In cmd, there is no space before `&`, because cmd would otherwise include the space in the value.

### 3.6 Simulation mode

The bank is simulated: every payment takes `PROCESSING_DELAY_SECONDS` and succeeds. To test what happens when the bank is slow or fails, start the server with `SIMULATION_MODE=true`. Two optional request headers then control the simulated bank for that one request:

- `X-Simulate-Bank-Delay`: how many seconds the bank takes, from `0` to `30`. A value above `BANK_TIMEOUT_SECONDS` makes the payment time out with `500`.
- `X-Simulate-Bank-Failure`: `true` makes the bank fail with `500`.

Simulation mode is off by default so that a running server cannot be told by its callers to slow down or fail. When it is off, both headers are ignored whatever their value. When it is on, an invalid value (for example a delay of `abc`) is rejected with `400`.

---

## 4. API Documentation

While the server is running, the same information is available as interactive documentation at `http://127.0.0.1:8000/docs`, where every request can be sent from the browser.

### 4.1 Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/process-payment` | Charge a payment exactly once per `Idempotency-Key` |
| `GET` | `/health` | Confirm the server is running and whether simulation mode is on |
| `GET` | `/docs` | Interactive API documentation |
| `GET` | `/openapi.json` | The API description in OpenAPI format, used by `/docs` |
| `GET` | `/` | Redirects to `/docs` |

### 4.2 POST /process-payment

#### 4.2.1 Request headers

| Header | Required | Description |
|---|---|---|
| `Idempotency-Key` | Yes | Unique id for this payment, created by the client. Send the same key when retrying the same payment. At most 255 characters |
| `Content-Type` | Yes | `application/json` |
| `X-Simulate-Bank-Delay` | No | Simulation mode only. Seconds the simulated bank takes for this request, from `0` to `30` |
| `X-Simulate-Bank-Failure` | No | Simulation mode only. `true` makes the simulated bank fail this request |

#### 4.2.2 Request body

```json
{"amount": 100, "currency": "GHS"}
```

| Field | Type | Rules |
|---|---|---|
| `amount` | JSON number | Greater than 0, at most 2 decimal places. A string such as `"100"` is rejected |
| `currency` | string | One of `GHS`, `NGN`, `USD`, `EUR`, `GBP` |

#### 4.2.3 Validation

The checks run in this order. The first one that fails returns `400 Bad Request` with a body of the form `{"error": "<what went wrong>"}`, and nothing is stored for the key, so the client can correct the request and send it again with the same key.

| Check | Rule |
|---|---|
| Idempotency key | `Idempotency-Key` header is present, not blank, and at most 255 characters |
| JSON | Body is a valid JSON object with no duplicate fields. `NaN` and `Infinity` are rejected |
| Fields | Exactly `amount` and `currency`, no other fields |
| Amount type | `amount` is a JSON number. Strings such as `"100"` and booleans are rejected |
| Amount value | `amount` is greater than 0 |
| Amount precision | `amount` has at most 2 decimal places. `100.50` and `100.500` are both accepted as the same value; `100.555` is rejected |
| Currency | `currency` is one of `GHS`, `NGN`, `USD`, `EUR`, `GBP`. All five use 2 decimal places, which is why the precision rule holds for every supported currency |
| Simulation headers | Only when simulation mode is on: `X-Simulate-Bank-Delay` is a number from `0` to `30`, and `X-Simulate-Bank-Failure` is `true` or `false` |

#### 4.2.4 Responses

| Status | When | Body | Headers |
|---|---|---|---|
| `201 Created` | The payment was charged, or this is a replay of an earlier identical request | Payment result (below) | `X-Cache-Hit: false` when processed now, `true` when replayed |
| `400 Bad Request` | A validation check failed | `{"error": "..."}` naming the problem | |
| `422 Unprocessable Entity` | The key was already used with a different body | `{"error": "Idempotency key already used for a different request body."}` | |
| `500 Internal Server Error` | The bank failed or took longer than `BANK_TIMEOUT_SECONDS`. The key is released, so a retry with the same key is processed fresh | `{"error": "Payment processing failed. Retry with the same Idempotency-Key."}` | |
| `503 Service Unavailable` | An identical request is still processing and this request waited `MAX_WAIT_SECONDS` without a result. The key is kept, so a retry gets the first request's result | `{"error": "A request with this Idempotency-Key is still processing. Retry shortly."}` | `Retry-After: 1` |

Payment result fields:

| Field | Example | Description |
|---|---|---|
| `message` | `"Charged 100 GHS"` | Human-readable result |
| `transaction_id` | `"txn_c73d3c37ca9541e09ddf3bfd2832fc8f"` | Id of the charge. A replayed response has the same id as the original, which shows no second charge happened |
| `amount` | `"100"` | The amount charged, as a string so it stays exact |
| `currency` | `"GHS"` | The currency charged |

### 4.3 Example requests

The responses below were produced by the running server; only the `date`, `server` and `content-length` headers are left out. The commands use Git Bash, macOS and Linux syntax. In Windows cmd, write the body as `-d "{\"amount\": 100, \"currency\": \"GHS\"}"`. In PowerShell, type `curl.exe` instead of `curl` and write the body as `-d '{\"amount\": 100, \"currency\": \"GHS\"}'`.

#### 4.3.1 First payment

Takes about 2 seconds.

```
curl -i -X POST http://127.0.0.1:8000/process-payment \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: order-1001" \
  -d '{"amount": 100, "currency": "GHS"}'
```

```
HTTP/1.1 201 Created
x-cache-hit: false
content-type: application/json

{"message":"Charged 100 GHS","transaction_id":"txn_c73d3c37ca9541e09ddf3bfd2832fc8f","amount":"100","currency":"GHS"}
```

#### 4.3.2 Retry of the same payment

The same command again. Returns immediately with the same `transaction_id` and `x-cache-hit: true`.

```
HTTP/1.1 201 Created
x-cache-hit: true
content-type: application/json

{"message":"Charged 100 GHS","transaction_id":"txn_c73d3c37ca9541e09ddf3bfd2832fc8f","amount":"100","currency":"GHS"}
```

#### 4.3.3 Same key, different amount

```
curl -i -X POST http://127.0.0.1:8000/process-payment \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: order-1001" \
  -d '{"amount": 500, "currency": "GHS"}'
```

```
HTTP/1.1 422 Unprocessable Entity
content-type: application/json

{"error":"Idempotency key already used for a different request body."}
```

#### 4.3.4 Two identical requests at the same time

Send the same request twice, the second one while the first is still processing (within 2 seconds), for example from two terminals:

```
curl -i -X POST http://127.0.0.1:8000/process-payment \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: order-1006" \
  -d '{"amount": 250.50, "currency": "USD"}'
```

Both return `201` with the same `transaction_id`. The first was processed; the second waited for it and replayed its result.

```
First request:
HTTP/1.1 201 Created
x-cache-hit: false
content-type: application/json

{"message":"Charged 250.50 USD","transaction_id":"txn_4660e1564529447aa884cfb05c8b79ba","amount":"250.50","currency":"USD"}

Second request:
HTTP/1.1 201 Created
x-cache-hit: true
content-type: application/json

{"message":"Charged 250.50 USD","transaction_id":"txn_4660e1564529447aa884cfb05c8b79ba","amount":"250.50","currency":"USD"}
```

#### 4.3.5 Missing Idempotency-Key

```
curl -i -X POST http://127.0.0.1:8000/process-payment \
  -H "Content-Type: application/json" \
  -d '{"amount": 100, "currency": "GHS"}'
```

```
HTTP/1.1 400 Bad Request
content-type: application/json

{"error":"Missing Idempotency-Key header."}
```

#### 4.3.6 Invalid body

```
curl -i -X POST http://127.0.0.1:8000/process-payment \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: order-1002" \
  -d '{"amount": "100", "currency": "GHS"}'
```

```
HTTP/1.1 400 Bad Request
content-type: application/json

{"error":"amount must be a number."}
```

#### 4.3.7 Bank failure (simulation mode)

Start the server with `SIMULATION_MODE=true` (see 3.5 Configuration).

```
curl -i -X POST http://127.0.0.1:8000/process-payment \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: order-1003" \
  -H "X-Simulate-Bank-Failure: true" \
  -d '{"amount": 100, "currency": "GHS"}'
```

```
HTTP/1.1 500 Internal Server Error
content-type: application/json

{"error":"Payment processing failed. Retry with the same Idempotency-Key."}
```

Sending the request again without the header processes it fresh and returns `201` with `x-cache-hit: false`.

#### 4.3.8 Bank timeout (simulation mode)

A delay of 8 seconds is longer than the default 5 second bank timeout, so the request fails after about 5 seconds.

```
curl -i -X POST http://127.0.0.1:8000/process-payment \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: order-1004" \
  -H "X-Simulate-Bank-Delay: 8" \
  -d '{"amount": 100, "currency": "GHS"}'
```

```
HTTP/1.1 500 Internal Server Error
content-type: application/json

{"error":"Payment processing failed. Retry with the same Idempotency-Key."}
```

#### 4.3.9 Duplicate request that stops waiting (simulation mode)

With the default settings this cannot happen, because the bank timeout (5 seconds) ends the first request before the maximum wait (10 seconds) runs out. To see it, start the server with `SIMULATION_MODE=true`, `BANK_TIMEOUT_SECONDS=20` and `MAX_WAIT_SECONDS=2` (see 3.5 Configuration for each shell), send a first request with `X-Simulate-Bank-Delay: 6`, and while it is processing send the same request without the delay header:

```
curl -i -X POST http://127.0.0.1:8000/process-payment \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: order-2001" \
  -d '{"amount": 100, "currency": "GHS"}'
```

After 2 seconds:

```
HTTP/1.1 503 Service Unavailable
retry-after: 1
content-type: application/json

{"error":"A request with this Idempotency-Key is still processing. Retry shortly."}
```

Once the first request has finished, sending the same request again replays its result with `x-cache-hit: true`.

#### 4.3.10 Invalid simulation header

On the `/docs` page, the browser itself refuses a non-numeric `X-Simulate-Bank-Delay` before sending the request. The server checks the value again for every other client, which can be seen with curl:

```
curl -i -X POST http://127.0.0.1:8000/process-payment \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: order-1005" \
  -H "X-Simulate-Bank-Delay: abc" \
  -d '{"amount": 100, "currency": "GHS"}'
```

```
HTTP/1.1 400 Bad Request
content-type: application/json

{"error":"X-Simulate-Bank-Delay must be a number of seconds between 0 and 30."}
```

### 4.4 GET /health

```
curl -i http://127.0.0.1:8000/health
```

```
HTTP/1.1 200 OK
content-type: application/json

{"status":"ok","simulation_mode":false}
```
