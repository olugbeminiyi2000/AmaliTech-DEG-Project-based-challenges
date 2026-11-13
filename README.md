# Silk Idempotency Gateway

A payment-processing API that charges each payment **at most once**, no matter how many times a client retries the same request.

---

## Contents

- [1. Overview](#1-overview)
- [2. Architecture](#2-architecture)
- [3. Setup](#3-setup)
- [4. API Documentation](#4-api-documentation)
- [5. Design Decisions](#5-design-decisions)
- [6. Developer's Choice: Key Expiry](#6-developers-choice-key-expiry)
- [7. Testing](#7-testing)

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

---

## 5. Design Decisions

### 5.1 One process and one event loop: claiming a key is atomic without locks

The server runs as one process. Uvicorn runs an asyncio event loop that handles many requests concurrently, but it only switches from one request to another at an `await`, when a request is waiting for something such as the bank. Plain code between two `await`s always runs to the end before any other request gets a turn.

The endpoint looks up the key (`store.get`) and claims it (`store.create`) with no `await` in between, and none of the store's methods contain an `await`. So once a request has found that a key is new, no other request can run until that request has claimed it. If two identical requests arrive at the same moment, exactly one claims the key and the other finds it already claimed. No lock is needed.

The endpoint is declared with `async def` on purpose. FastAPI runs endpoints declared with plain `def` in a pool of threads, and the operating system can switch threads at any line, including between looking up and claiming a key. That would allow two requests to claim the same key and charge twice.

This guarantee only holds within one process. See 5.14 for running several processes or servers.

### 5.2 In-memory key store

Keys are kept in a Python dictionary inside the server process, which the brief allows. It needs no setup, and every lookup is fast.

The consequences are that keys are lost when the server restarts, and that two server processes would each have their own separate store. Both are covered in 5.14.

### 5.3 Comparing bodies with a canonical fingerprint

To detect a key reused with a different payment, each key's record stores a fingerprint of the first body: a SHA-256 hash. A hash has a fixed size, is fast to compare, and means the store does not need to keep the payment details themselves.

The hash is taken from a canonical form of the body, not from the raw bytes. The body is parsed, the amount is normalised, and it is written back out with the fields in sorted order and with no extra spaces. Without this, the same payment written differently would get a different hash and be wrongly rejected with `422`. For example, these all produce the same fingerprint:

```
{"amount": 100.5, "currency": "USD"}
{"currency": "USD", "amount": 100.50}
{"amount":100.500,"currency":"USD"}
```

### 5.4 Validating before the key is claimed

Every check on the header and body runs before the store is touched. A rejected request therefore leaves nothing behind: the key is not used up, and the client can correct the request and send it again with the same key.

FastAPI can validate request bodies automatically, but it does so before the endpoint's own code runs, in its own order, and it reports problems as `422` in its own format. Here `422` has a specific meaning (a key reused with a different body), the header must be checked before the body, and every validation error should look the same. So the endpoint reads the raw body and validates it itself, in the order shown in the flowchart. Because FastAPI can then no longer describe the body on its own, `app/api_docs.py` describes the headers, body and responses for the `/docs` page.

### 5.5 Money is handled as Decimal, never float

A `float` stores numbers in binary, and most decimal fractions, such as `0.1`, cannot be stored exactly in binary. Small errors appear and add up: `0.1 + 0.2` gives `0.30000000000000004`, and adding `0.10` a million times gives `100000.00000133288` instead of `100000.00`. `Decimal` stores the decimal digits themselves, so amounts stay exact.

- The JSON body is parsed straight into `Decimal`, so an amount never passes through a `float` on the way in.
- The response returns the amount as a string, such as `"100"` or `"250.50"`, because many JSON clients read numbers as floats.
- Amounts are limited to 2 decimal places, and only currencies that use 2 decimal places are supported (`GHS`, `NGN`, `USD`, `EUR`, `GBP`). Currencies such as the Japanese yen (0 decimal places) or the Kuwaiti dinar (3) would need a rule per currency.
- Systems that process very high volumes often store amounts as whole numbers of the smallest unit instead (for example `10050` pesewas for 100.50 GHS), which is both exact and fast. This project keeps the format from the brief, where amounts are given in cedis, dollars and so on.

### 5.6 A strict request body

The body must match exactly what a payment needs. Anything unexpected is rejected rather than guessed at.

| Rejected | Why |
|---|---|
| Amount as a string (`"100"`) | Most likely a bug in the client. Accepting it would hide the bug |
| Amount as `true` or `false` | In Python, `True` and `False` are also integers (`True == 1`), so this is checked explicitly |
| Extra fields | They would change the fingerprint, so the same payment with a different extra field would get a `422` |
| Duplicate fields, such as `"amount"` twice | Python's JSON parser silently keeps the last one, so the client and the server could disagree about the amount |
| `NaN` and `Infinity` | Not valid JSON, although Python's parser accepts them by default |
| Unsupported currencies | Only a fixed list of currencies is accepted, not any 3 letters, as a real payment processor would |
| Keys longer than 255 characters | Keeps the memory used by each stored key small |

### 5.7 422 for a key reused with a different body

The brief allows `422 Unprocessable Entity` or `409 Conflict`. `422` was chosen because the request itself is well-formed, but it cannot be processed with that key. It also keeps `409` out of the API entirely, since the brief says in-flight duplicates must not receive `409`.

The body check runs before the state check, so a different body is rejected immediately, even while the first request with that key is still processing.

### 5.8 Duplicates that arrive during processing wait for the first result

Each record holds an `asyncio.Event`, a signal that starts unset. When the first request finishes, successfully or not, it sets the signal. A duplicate that finds the record still `PROCESSING` waits on that signal. Waiting uses no CPU, and the duplicate wakes up the moment the first request finishes, then returns the saved result with `X-Cache-Hit: true`. Nothing is checked repeatedly on a timer.

A duplicate does not wait forever. After `MAX_WAIT_SECONDS` (10 by default) it stops waiting and returns `503 Service Unavailable` with `Retry-After: 1`. The record is kept, so the retry gets the first request's result once it is ready. With the default settings this does not happen, because the bank timeout (5 seconds) ends the first request first. The limit is a safety net in case something unexpected keeps the first request running.

### 5.9 A failed charge releases the key

If the bank call fails or takes longer than `BANK_TIMEOUT_SECONDS`, the record is deleted, any waiting duplicates are told the first request failed, and `500` is returned. A retry with the same key is then processed as a new attempt. The failure is not saved and replayed, because it means the charge did not complete, and the client should be able to try again.

The same happens if the request is cancelled while the bank call is in progress, for example because the server is shutting down. Without this, the key would stay `PROCESSING` forever.

Releasing the key is only safe if the failure happened before any money moved. The simulated bank moves no real money, so here it is always safe. With a real bank, a timeout is uncertain: the bank may have charged and only its answer was lost. A real payment processor passes its own idempotency key or reference on to the bank, so that a repeated charge request is recognised there too, or asks the bank for the payment's status before trying again.

### 5.10 Two separate time limits

| Limit | Applies to | Default | Purpose |
|---|---|---|---|
| Bank timeout | Records in `PROCESSING` | 5 seconds | Stops a charge attempt from waiting on the bank forever |
| Key expiry (TTL) | Records in `COMPLETED` | 24 hours | Remembers the result long enough for late retries, then frees the memory |

`PROCESSING` records have no expiry. Forgetting a key while its charge is still in progress would let a retry start a second charge. A `PROCESSING` record always ends within the bank timeout instead, either by becoming `COMPLETED` or by being deleted.

Expiry times are measured with a monotonic clock, which only moves forward. Changing the computer's date or time therefore cannot make keys expire early or never expire.

### 5.11 Responses

- A new charge returns `201 Created`, because a payment was created.
- A replay returns exactly the saved status code and body, as the brief requires.
- `X-Cache-Hit` is `false` on a response that was processed now and `true` on a replay.
- Every successful response includes a `transaction_id`. A replay has the same id as the original, which shows that no second charge happened.

### 5.12 Simulation mode is off by default

The headers that make the simulated bank slow or fail are useful for testing, but a payment endpoint that its callers can tell to fail would be a weakness in a real deployment. They only work when the server is started with `SIMULATION_MODE=true`. When the mode is off, the headers are ignored whatever their value. When it is on, invalid values are rejected with `400`, so a typo such as `ture` does not silently do nothing.

### 5.13 Status codes

| Status | Meaning | Can the client retry with the same key? |
|---|---|---|
| `201` | Charged, or a replay of the charge | Not needed. A retry replays the same result |
| `400` | The request is invalid | Yes, after correcting the request. The key was not used up |
| `422` | The key belongs to a different payment | No. Send the original body, or use a new key for a new payment |
| `500` | The charge did not complete | Yes. The key was released |
| `503` | An identical request is still processing | Yes, after the `Retry-After` seconds |

### 5.14 Known limitations and what production would add

| Limitation | Effect | What a production system would add |
|---|---|---|
| Keys are kept in memory | A restart forgets every key, so a retry after a restart would be charged again | Keep keys in Redis or a database |
| One process only | With several processes or servers, each has its own store, so two of them could both accept the same key | A shared store with an atomic claim, such as Redis `SET key value NX`. Waiting duplicates on other servers would also need a shared notification, such as Redis publish and subscribe |
| No authentication | Anyone who can reach the server can send payments | Authenticate each client, for example with an API key |
| Keys are not separated by client | Two clients that happen to use the same key string would collide: one would receive `422`, or a replay of the other's response | Store keys per client, so each client has its own key space |
| Uncertain bank failures | See 5.9 | Pass an idempotency key or reference on to the bank |

---

## 6. Developer's Choice: Key Expiry

### 6.1 The problem

Without expiry, every key the gateway has ever seen stays in memory for as long as the server runs. Each payment adds a record (the key, the body fingerprint, the saved response), so memory use grows with every payment and never shrinks. A busy payment processor would eventually run out of memory and crash. Because the keys are kept in memory, a crash also loses every key, so retries arriving after the restart would be charged again. A safety layer that slowly brings the payment service down is worse than having none.

Keeping a key forever is also unnecessary. Retries happen within seconds or minutes of the original request, not weeks later.

### 6.2 What it does

- When a payment completes, its record gets an expiry time: now plus `KEY_TTL_SECONDS` (24 hours by default).
- Expired records are removed in two ways:
  - **When the key is used again:** if a request arrives with a key whose record has expired, the record is deleted and the request is handled as a new payment.
  - **By a background task:** every `CLEANUP_INTERVAL_SECONDS` (60 seconds by default), a task that starts with the server removes every expired record, including keys that are never used again.
- Records still in `PROCESSING` never expire, because forgetting a key while its charge is in progress would allow a second charge (see 5.10).

### 6.3 Why 24 hours

24 hours is far longer than any realistic retry: automatic retries happen within seconds, and even a client recovering from an outage retries within hours. It also matches common practice in payment APIs; Stripe, for example, keeps idempotency keys for at least 24 hours. The value can be changed with `KEY_TTL_SECONDS`.

The trade-off is that a retry arriving after its key has expired is treated as a new payment. The expiry time therefore has to be much longer than any retry window, which is why it is measured in hours rather than minutes.

### 6.4 Where it is in the code

| File | What it does |
|---|---|
| `app/store.py` | `complete()` sets the expiry time. `get()` deletes an expired record when its key is used again. `purge_expired()` removes all expired records |
| `app/main.py` | `_remove_expired_keys()` is the background task. It is started when the server starts and stopped when it shuts down |
| `app/config.py` | `KEY_TTL_SECONDS` and `CLEANUP_INTERVAL_SECONDS` |

### 6.5 How to see it working

Start the server with a 30 second expiry (see 3.5 Configuration for each shell), for example in Git Bash:

```
KEY_TTL_SECONDS=30 .venv/Scripts/python -m app
```

1. Send a payment with a new key. It returns `201` with `x-cache-hit: false`. Note the `transaction_id`.
2. Send the same request within 30 seconds. It returns immediately with the same `transaction_id` and `x-cache-hit: true`.
3. Wait more than 30 seconds and send it again. It takes about 2 seconds and returns `201` with a new `transaction_id` and `x-cache-hit: false`: the key was forgotten.

The automated tests `test_expired_key_is_processed_as_new` and `test_purge_removes_only_expired_keys` in `tests/test_api.py` check the same behaviour.

---

## 7. Testing

### 7.1 Automated tests

Run the tests with the command for your shell in 3.4. All 67 tests should pass. The API tests send real HTTP requests to the app, including several at the same time, with shorter delays than the defaults so that they finish in about 10 seconds.

| What is tested | Tests | Count |
|---|---|---|
| User Story 1: first payment | `test_first_request_is_processed_and_charged` | 1 |
| User Story 2: duplicate request | `test_duplicate_request_replays_saved_response_without_charging`, `test_same_payment_written_differently_counts_as_duplicate` | 2 |
| User Story 3: same key, different body | `test_same_key_with_different_body_is_rejected`, `test_different_body_is_rejected_even_while_first_is_processing` | 2 |
| Bonus: in-flight duplicates | `test_simultaneous_duplicates_are_charged_once_and_get_same_result`, `test_waiting_request_gets_500_when_first_request_fails`, `test_waiting_request_gets_503_when_max_wait_is_reached` | 3 |
| Bank failure and timeout | `test_failed_processing_returns_500_and_releases_the_key`, `test_bank_timeout_is_treated_as_failure` | 2 |
| Developer's Choice: key expiry | `test_expired_key_is_processed_as_new`, `test_purge_removes_only_expired_keys` | 2 |
| Validation through the API | `test_missing_key_returns_400`, `test_invalid_body_returns_400_and_does_not_use_up_the_key` | 2 |
| Simulation mode | `test_slow_bank_header_triggers_bank_timeout_in_simulation_mode`, `test_simulation_headers_are_ignored_when_simulation_mode_is_off`, `test_invalid_simulation_headers_return_400` (4 cases) | 6 |
| Health check and API docs | `test_health_reports_simulation_mode`, `test_root_redirects_to_api_docs`, `test_api_docs_describe_headers_body_and_responses` | 3 |
| Every validation rule, one by one (`tests/test_validation.py`) | Valid and invalid keys, malformed JSON, duplicate fields, `NaN` and `Infinity`, wrong fields, non-numeric amounts, zero and negative amounts, too many decimal places, unsupported currencies | 39 |
| Settings from environment variables (`tests/test_config.py`) | Defaults, reading every variable, and invalid values stopping the server | 5 |

Several tests check that the simulated bank was called exactly once, which is how they prove that a duplicate did not cause a second charge.

### 7.2 Manual testing

These steps check every behaviour by hand, using the interactive documentation at `http://127.0.0.1:8000/docs`. On that page, open `POST /process-payment` and click **Try it out**. Each step lists what to enter and what the response should be. For how to start the server with a setting in each shell, see 3.5 Configuration.

#### 7.2.1 User stories (normal start)

Start the server normally (3.3).

| Step | Idempotency-Key | Body | Expected response |
|---|---|---|---|
| 1 | `story-1` | `{"amount": 100, "currency": "GHS"}` | After about 2 seconds: `201`, `"Charged 100 GHS"`, `x-cache-hit: false` |
| 2 | `story-1` | `{"amount": 100, "currency": "GHS"}` | Immediately: `201`, the same `transaction_id`, `x-cache-hit: true` |
| 3 | `story-1` | `{"amount": 500, "currency": "GHS"}` | Immediately: `422`, `"Idempotency key already used for a different request body."` |
| 4 | (empty) | `{"amount": 100, "currency": "GHS"}` | `400`, `"Missing Idempotency-Key header."` |
| 5 | `story-2` | `{"amount": "100", "currency": "GHS"}` | `400`, `"amount must be a number."` |
| 6 | `story-2` | `{"amount": 100, "currency": "GHS"}` | After about 2 seconds: `201`, `x-cache-hit: false`. The invalid request in step 5 did not use up the key |
| 7 | `story-3` | `{"amount": 100.555, "currency": "GHS"}` | `400`, at most 2 decimal places |
| 8 | `story-3` | `{"amount": 100, "currency": "JPY"}` | `400`, currency must be one of the supported currencies |
| 9 | `story-4` | `{"amount": 100, "currency": "GHS"}`, with `X-Simulate-Bank-Failure` set to `true` | `201`, because simulation mode is off and the header is ignored |

**In-flight duplicate:** open `/docs` in a second browser tab. Enter the key `story-5` and the same body in both tabs, click **Execute** in the first tab and then straight away in the second. Both return `201` with the same `transaction_id`; the first shows `x-cache-hit: false` and the second `true`. The window is only 2 seconds; 7.2.3 gives a much longer one.

#### 7.2.2 Bank failure and timeout (`SIMULATION_MODE=true`)

| Step | Idempotency-Key | Simulation header | Expected response |
|---|---|---|---|
| 1 | `fail-1` | `X-Simulate-Bank-Failure: true` | After about 2 seconds: `500` |
| 2 | `fail-1` | none | After about 2 seconds: `201`, `x-cache-hit: false`. The failed attempt released the key |
| 3 | `slow-1` | `X-Simulate-Bank-Delay: 8` | After about 5 seconds (the bank timeout, not 8): `500` |
| 4 | `slow-1` | none | After about 2 seconds: `201`, `x-cache-hit: false` |

On `/docs`, the browser refuses a non-numeric delay such as `abc` before sending it. To check that the server also rejects it, use the curl example in 4.3.10.

#### 7.2.3 A duplicate that waits long enough (`SIMULATION_MODE=true`, `BANK_TIMEOUT_SECONDS=20`, `MAX_WAIT_SECONDS=25`)

Use two browser tabs with the same key and body. Only the first tab sends `X-Simulate-Bank-Delay`. Click **Execute** in the first tab, then in the second within a few seconds.

| Step | Idempotency-Key | Delay in first tab | Expected response |
|---|---|---|---|
| 1 | `x-1` | `15` | After about 15 seconds, both tabs: `201` with the same `transaction_id`. First tab `x-cache-hit: false`, second tab `true` |
| 2 | `x-2` | `25` | After about 20 seconds, both tabs: `500`. The first request's bank call timed out and the waiting request was told it failed |

#### 7.2.4 A duplicate that stops waiting (`SIMULATION_MODE=true`, `BANK_TIMEOUT_SECONDS=20`, `MAX_WAIT_SECONDS=5`)

Same two-tab method as 7.2.3.

| Step | Idempotency-Key | Delay in first tab | Expected response |
|---|---|---|---|
| 1 | `y-1` | `15` | Second tab after about 5 seconds: `503` with `retry-after: 1`. First tab after about 15 seconds: `201`. Executing the second tab again then returns immediately with the same `transaction_id` and `x-cache-hit: true` |
| 2 | `y-2` | `25` | Second tab after about 5 seconds: `503`. First tab after about 20 seconds: `500`. Executing the second tab again, without a delay, returns `201` with `x-cache-hit: false` after about 2 seconds: it was processed as a new attempt |

#### 7.2.5 Key expiry (`KEY_TTL_SECONDS=30`)

Follow the steps in 6.5.

#### 7.2.6 Invalid settings (`BANK_TIMEOUT_SECONDS=five`)

The server does not start and prints `BANK_TIMEOUT_SECONDS must be a number of seconds, got 'five'.` In cmd and PowerShell, clear the variable or close the terminal afterwards, because it stays set for the rest of that terminal session.
