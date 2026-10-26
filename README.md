# Refund Consistency Lab

A fault-injection lab for refund notification processing. The same duplicate, late,
reordered, missing and conflicting provider events are delivered to a naive processor
and to a protected one, so the point where their accounting diverges is visible
instead of theoretical. No real payments, no provider SDK, no card data.

**Status: M3 persistence.** Refund rules, the naive baseline, admission and delivery
processing are implemented and tested against SQLite. There is no scenario scheduler,
provider lookup or comparison page yet: `/` still lists planned scenarios and runs
nothing.

## Requirements

- Python 3.10 or newer
- Docker, only if you want to build the image

## Setup

```
python -m venv .venv
.venv\Scripts\activate          # PowerShell: .\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## Test

```
python -m pytest
```

Tests run against the ASGI app in-process; no server and no network are needed.

## Run

```
uvicorn app.main:app --reload --port 8000
```

- `GET /` — skeleton page: version, git SHA, database state, planned scenarios
- `GET /health` — JSON. 200 `ok` when the database is writable and in WAL mode,
  200 `degraded` when it is writable but the volume refused WAL, 503 `error`
  when it cannot be written at all

## Docker

```
docker build --build-arg GIT_SHA=$(git rev-parse --short HEAD) -t refund-lab .
docker run --rm -p 7860:7860 -v refund-lab-data:/srv/var refund-lab
```

The image listens on `PORT` (default `7860`) and writes SQLite to
`LAB_DB_PATH` (default `/srv/var/lab.db`). It runs as UID 1000, which is what
container space hosts expect. Mount a volume at `/srv/var` if you need runs to
survive a container restart.

## Hosting

The target is a single Docker image on a card-free container host. Checked
against Hugging Face Spaces, which is the current candidate:

- **Disk is ephemeral.** The Space filesystem is discarded on restart or rebuild.
  Runs must therefore be self-contained and short-lived; a restart loses in-flight
  demo runs, not the product. Persistent runs need a Storage Bucket mounted as a
  volume, which is a separate decision and not required for the lab.
- **WAL on a mounted bucket is unverified.** SQLite needs shared memory for WAL,
  and network-backed volumes can fall back to a rollback journal. That is why
  `/health` distinguishes `degraded` from `error` instead of failing the whole
  site when `journal_mode` is not `wal`.
- **Port and user.** The platform exposes one port (default 7860) and runs the
  container as UID 1000; both are already matched.
- **Cold start.** Free tiers sleep when idle, so the first request after a quiet
  period is slow. This is a demo-latency fact, not a correctness one.

## Domain rules

`app/domain/` is pure: no database, no HTTP, no clock. Money is integer paise and
INR only; booleans, floats and non-positive amounts are rejected rather than coerced.

Three identities are kept apart because confusing any two produces a different bug:

| Identity | Example | Meaning |
|---|---|---|
| Refund ID | `R1` | The business operation. Its amount is immutable. |
| Event ID | `E8` | One provider claim. Retries reuse it. |
| Attempt ID | `a2` | One HTTP delivery of one event. |

**Classification comes from the evidence set, never arrival order.** For one refund:

| Evidence | Result |
|---|---|
| nothing, or pending only | `pending` |
| succeeded, with or without pending | `succeeded`, confirmed = intent amount |
| failed, with or without pending | `failed`, confirmed = 0 |
| succeeded and failed | `review` (`terminal_conflict`), confirmed = 0 |
| any claim whose amount or currency differs from the intent | `review` (`amount_mismatch`, `currency_mismatch`), confirmed = 0 |
| one event ID carrying conflicting claims, including across two refunds | `review` (`event_identity_conflict`) |

`review` keeps the amount visible as `review_paise` and contributes nothing to
`confirmed`. There is no automatic resolution: an unresolved refund stays unresolved
for the run.

Evidence for a refund that was never admitted is returned separately in
`report.unmatched`. It changes no total and is not dropped. Deciding whether such a
delivery is accepted or quarantined belongs to the ingest layer, which does not exist
yet.

**Admission is where over-refunds are prevented**, before the provider is asked. The
sum of all admitted intents plus the new one must not exceed the captured amount, so
a check at credit time — after money has already moved — can only report an
over-refund, never prevent one. Reservations are never released during a run, so a
failed refund keeps consuming capacity.

**The naive baseline** (`app/domain/naive.py`) makes two ordinary mistakes: it adds
the amount on every success notification, and it lets the most recent arrival decide
the status. It is the obvious first implementation, not scenario-specific sabotage.

## Persistence

`app/store.py` keeps one SQLite file. Seven tables: `runs`, `payments`,
`refund_intents`, `deliveries`, `findings`, `refund_states` and `naive_states`.

Two guarantees are carried by the database rather than by careful code.

- `PRIMARY KEY (run_id, provider, attempt_id)` makes a transport retry a no-op. The
  same attempt id returns the recorded outcome and adds no delivery, so the naive
  side does not count it twice.
- A partial unique index on `(run_id, provider, event_id)` where `accepted = 1` allows
  at most one accepted claim per event. A provider retry with a new attempt id is
  still recorded and still counted by the naive side, which is the bug being shown.

Admission and ingest each run inside one `BEGIN IMMEDIATE` transaction. For ingest
that transaction covers the delivery row, its findings and both projections, so a
failure leaves nothing partial and the same request can be retried.

Every delivery attempt stays as a row, including duplicates and conflicting payloads.
A delivery for a refund that was never admitted creates a finding and no refund.
Foreign key enforcement is per connection in SQLite, so every connection goes through
`app/db.py`, which turns it on.

## Layout

```
app/config.py        settings, version, git SHA resolution
app/db.py            SQLite connection pragmas and the health probe
app/domain/money.py  paise validation and rendering
app/domain/model.py  payment, intent, observation, delivery, report types
app/domain/rules.py  admission, evidence reducer, comparison
app/domain/naive.py  the deliberately naive baseline
app/store.py         SQLite schema, admission and atomic ingest
app/main.py          app factory, / and /health
app/templates/       server-rendered page
docs/decisions.md    tradeoffs and corrections
tests/               health, page, database, money, admission, rule and store tests
```

## Design notes

Dependencies are deliberately three: FastAPI, Uvicorn and Jinja2, with pytest and
httpx for tests. FastAPI is kept over Flask or the stdlib server for in-process ASGI
tests and cheap routing; Jinja2 renders the comparison page so the UI cannot compute
a business result the tests never see.

One earlier justification did not survive contact with the code: payload validation
belongs in the domain dataclasses, not in FastAPI request models, because the reducer
must be testable without HTTP at all. FastAPI therefore carries routing and test
ergonomics, not validation. If the remaining milestones stop needing ASGI tests,
Flask would be an equally good choice and the dependency count would not change.

`app/db.py` opens SQLite with `isolation_level=None`, so a statement outside an
explicit `BEGIN IMMEDIATE` commits on its own. The setting exists so that a future
write path has to opt into a transaction rather than inheriting one by accident. No
such write path exists yet. The naive baseline is a pure function that counts success
notifications and takes the last arriving status, and it performs no database writes.
The torn write case is planned for the ingest layer and is not implemented.