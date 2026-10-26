# Refund Consistency Lab

A fault-injection lab for refund notification processing. The same duplicate, late,
reordered, missing and conflicting provider events are delivered to a naive processor
and to a protected one, so the point where their accounting diverges is visible
instead of theoretical. No real payments, no provider SDK, no card data.

**Status: M6 page and API.** The lab is usable end to end: pick a scenario, step or
run it, compare both processors, reconcile, replay and download the evidence.

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

Target is one Render web service running this Docker image on the free plan, with
SQLite inside the container. No managed database, no second instance.

**Deployment is blocked on account access.** There is no Render API key or linked
service on this machine, so the service could not be created. The exact next step is
below.

Settings, also encoded in `render.yaml` as a Blueprint:

- runtime Docker, Dockerfile at the repo root
- one instance, free plan
- health check path `/health`
- listen on `0.0.0.0` and on the port from `PORT`, which Render sets at runtime
- SQLite at `/srv/var/lab.db`, in the container's writable layer
- the commit comes from `RENDER_GIT_COMMIT`, which Render injects, so `/health`
  reports the commit Render actually built

### Next step

Either of these, then the service exists:

1. In the Render dashboard choose New, then Blueprint, and select this repository.
   Render reads `render.yaml` and creates the service with the settings above.
2. Create a Render API key and make it available as `RENDER_API_KEY`, then the service
   can be created from the command line.

### What storage does and does not survive

Runtime files live in the container's writable layer, and what happens to that layer
depends on how the container is handled.

- `docker restart` on the same container keeps the layer, so a local restart keeps
  the database.
- Replacing the container does not. A redeploy, a new image, or a platform restart
  that recreates the container starts from the image, so stored runs are gone.
- The free instance sleeps after a period of inactivity and starts again on the next
  request. Do not rely on stored runs surviving that.

Every run is short lived and starts from a bundled fixture, so losing stored runs
costs history, not the demo. A restart during a demo means the link needs one request
to wake, and any run created before it must be started again.

### Other notes

- **One instance, one SQLite file.** No queue, no worker, no external database.
- **WAL on a mounted network volume is unverified.** SQLite needs shared memory for
  WAL, and network-backed volumes can fall back to a rollback journal. That is why
  `/health` distinguishes `degraded` from `error` instead of failing the whole site
  when `journal_mode` is not `wal`.
- **No browser tooling in the image.** Playwright is a dev extra and `tools/` is not
  copied, so the runtime has no test dependency.

Inputs are bounded by construction: the only public inputs are a scenario name that
must exist in the bundled list, a run id, an integer cursor and an integer tick.
There is no free text and no upload.

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

An admitted refund gets a state row immediately, so a refund with no notifications
reads as pending rather than missing.

## Simulator

A fixture declares a payment, its refunds, what the provider knows, and which
notifications were actually delivered. Twelve fixtures live in `fixtures/`.

- `events` is provider truth. `deliveries` is what was sent. Only deliveries become
  work for the processors, so a dropped event has no path into them. A fixture that
  says an event was delivered but schedules no delivery, or the reverse, is rejected.
- A delivery claim must match its referenced event. A deliberate contradiction, such
  as one event id claiming two refunds, needs `intentional_conflict: true` and an
  optional `payload`, so a typo cannot silently become a conflict scenario.
- Ticks are logical. `run_to_end` applies the whole schedule without waiting.
- `step(run_id, expected_cursor)` applies one delivery. Repeating the same expected
  cursor returns the recorded result and changes nothing. A cursor ahead of the run is
  refused.
- The cursor advances in the same transaction as the delivery it consumed, so a failed
  step leaves the schedule where it was.
- `replay(run_id)` starts a new run and replays the stored fixture and the whole
  action log in order. The run id changes, the digest does not.

The report carries the fixture hash, the input payment and refunds, the dropped
provider events, every action with its tick and outcome, the final comparison and the
evidence. The digest covers logical timing and action order. It excludes the run id
and wall-clock timestamps only.

```
python -m app.sim fixtures/duplicate_success.json
```

## Reconciliation

`runner.reconcile(run_id, refund_id, at_tick)` asks the simulated provider about one
refund at a logical tick. The tick defaults to the run clock and cannot move backwards.

The answer goes through the same ingest path as a webhook and is tagged `lookup`, so
the rules decide what it means.

- A success the provider never sent is recovered, and only then does the refund become
  succeeded.
- `unavailable` and `not_found` add a finding and no observation. The refund keeps its
  last known state. Neither one means failed.
- A lookup whose amount differs from the intent is quarantined as `amount_mismatch`.
- A lookup that contradicts an existing terminal observation produces
  `terminal_conflict`, and both observations remain visible.

A lookup never creates a refund and never changes provider truth. Repeating the same
refund and tick returns the recorded outcome and adds no evidence. The naive baseline
is webhook-only, so lookups do not reach it.

## Page and API

`/` lists the twelve scenarios. A run page shows both processors side by side and
offers Step, Run to end, Reconcile, Replay and Download evidence.

Every number on the page comes from a `RunReport` built in `app/lab_service.py`.
Templates format values, they never calculate them, so the page and the evidence
download cannot disagree.

The JSON API mirrors the page:

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/scenarios` | fixture list |
| POST | `/api/runs` | start a run |
| GET | `/api/runs/{id}` | full report |
| POST | `/api/runs/{id}/step` | apply one delivery, needs `expected_cursor` |
| POST | `/api/runs/{id}/run` | apply the rest |
| POST | `/api/runs/{id}/reconcile` | ask the provider about a refund |
| POST | `/api/runs/{id}/replay` | replay into a new run |
| GET | `/api/runs/{id}/evidence` | report download |

Errors return an HTML page for routes and JSON for `/api`, with the same status code.
Unknown runs and scenarios are 404, a stale cursor is 409, an invalid lookup is 400.

The frontend is server-rendered HTML with a small amount of JavaScript that disables
a submitted button and shows a busy note. A separate frontend framework would give
partial updates and richer local state, at the cost of a second toolchain, a build
step, and a second place where a refund number could be computed. This page is a
table and a few buttons, so the reload is cheap and keeping every number in the
backend is worth more.

## Browser check

Development only, not part of the test suite:

```
python -m uvicorn app.main:app --port 8010
python tools/browser_check.py
```

It drives a local browser through the picker, a stepped run, duplicates, a lookup
recovery, an unresolved conflict, the loading state, the evidence download and both
error pages. Set `LAB_BROWSER_CHANNEL` to `chrome` or leave it empty for bundled
Chromium.

## Layout

```
app/config.py        settings, version, git SHA resolution
app/db.py            SQLite connection pragmas and the health probe
app/domain/money.py  paise validation and rendering
app/domain/model.py  payment, intent, observation, delivery, report types
app/domain/rules.py  admission, evidence reducer, comparison
app/domain/naive.py  the deliberately naive baseline
app/store.py         SQLite schema, admission and atomic ingest
app/sim/fixture.py   fixture parsing and validation
app/sim/provider.py  read-only provider view for lookups
app/sim/runner.py    run creation, stepping, reconciliation, replay and report
app/sim/cli.py       headless entry point
app/lab_service.py   the only place routes get lab state from
app/pages.py         HTML routes
app/api.py           JSON routes
app/main.py          app factory, /health, error pages
app/templates/       server-rendered page
fixtures/            twelve deterministic scenarios
tools/browser_check.py  development-only browser walkthrough
docs/decisions.md    tradeoffs and corrections
tests/               health, page, database, money, admission, rule, store, sim and reconcile tests
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