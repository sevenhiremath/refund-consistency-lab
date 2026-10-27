# Refund Consistency

A small refund notification processing service. It maintains one record per refund
from the provider notifications you deliver, so duplicate, late and conflicting events
resolve to one answer you can explain. It does **not** move money: no refund execution,
no outgoing notifications, no provider credentials.

The **simulator is the public demonstration** of that service, not a second product.
It runs the same admission and processing rules over twelve fixed scenarios, plus a
deliberately naive baseline that shows what a counting bug looks like.

No real merchant payments are processed and no live provider is contacted.

## What changed, and what it is now

This started as a fault-injection lab where the naive and protected processors were
equals. The direction changed: **the protected processor is the product**, and the
simulator is its demonstration and test harness. The naive baseline stays because it
shows the failure the service exists to prevent, but it is a teaching view, not the
other half of the product.

What that means in practice:

- `/v1` is the product surface. A trusted backend registers a payment and refunds,
  delivers normalized notifications, and reads protected status.
- The twelve scenarios are the demonstration. They are fixtures, not customer data.
- Provider lookup is **simulated and demo only**. The integration API has no lookup
  route, because there is no real provider adapter yet. A lookup exists to supply
  information a dropped notification never carried; it is not how duplicates are
  handled.
- The two surfaces cannot reach each other's data.

## Limitations

- One server wide `INTEGRATION_API_KEY`, no accounts, no per-client revocation.
- `POST /v1/workspaces` is not idempotent: a retried creation makes a second
  workspace. Do not retry it blindly.
- One payment and at most 50 refunds per workspace. No pagination.
- Processing is synchronous inside the request, so ingest latency is request latency
  and retries belong to the caller.
- Identity is scoped to one workspace and one provider. The same provider event in
  two workspaces is two independent observations; deduplication across workspaces is
  not promised.
- Provider lookup, the simulated provider and the fixture scenarios are demo only.
  Nothing here has been run against a real payment provider.
- SQLite with one writer. Run to end holds the write lock for the whole schedule.
- Stored runs do not survive a container replacement, and the free instance sleeps.

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

- `GET /` — overview of the service
- `GET /walkthrough` — the demo console: twelve scenarios plus three guided ones
- `GET /integrate` — the integration API flow and the runnable example
- `GET /runs/{id}` — one demo run: refund status, notification history, evidence
- `GET /api/*` — JSON mirror of the demo, unauthenticated
- `GET /v1/*` — integration API, requires `INTEGRATION_API_KEY`
- `GET /health` — JSON. 200 `ok` when the database is writable and in WAL mode,
  200 `degraded` when it is writable but the volume refused WAL, 503 `error`
  when it cannot be written at all

The demo routes and the integration routes share the tables but not the surface. A
demo route pointed at an integration workspace answers 404, and `/v1` rejects demo
runs, so there is no path to user data without the key and no simulated lookup over it.

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

Live at **https://refund-consistency-lab.onrender.com**. One Render web service running
this Docker image on the free plan, with SQLite inside the container. No managed
database, no second instance. Pushing to `main` redeploys it, because `render.yaml`
sets `autoDeploy`.

Settings, encoded in `render.yaml` as a Blueprint:

- runtime Docker, Dockerfile at the repo root
- one instance, free plan
- health check path `/health`
- listen on `0.0.0.0` and on the port from `PORT`, which Render sets at runtime
- SQLite at `/srv/var/lab.db`, in the container's writable layer
- the commit comes from `RENDER_GIT_COMMIT`, which Render injects, so `/health`
  reports the commit Render actually built

### What has been checked publicly

Independent smoke checks against the public URL have covered the health endpoint and
its reported commit, the scenario listing, duplicate processing and missing-message
recovery.

That is not a full public release audit. Evidence download, the error pages, the
lookup-tick rule, run isolation and behaviour under load were verified locally rather
than against the public instance.

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

The **shape** of public input is bounded: a scenario name that must exist in the
bundled list, a run id, an integer cursor and an integer tick, with no free text and no
upload. The **number** of runs is not bounded. Any visitor can create runs indefinitely,
at roughly 2 KB of database each, and there is no rate limit or eviction. A single
public instance would need one before it faced real traffic; it has none today.

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
evidence. The engine block names the version and the resolved source commit, so a
digest is only comparable within one build. When the commit cannot be resolved it is
marked `unknown` and `commit_known` is false, and a comparison across builds is not
established.

The digest covers logical timing and action order. It excludes the run id and
wall-clock timestamps only.

Every bundled fixture has exactly one payment. The rules and the store can hold more
than one payment in a run, but a report is built per payment while evidence for the
other payments is still passed in, so a second payment would appear partly as
unmatched evidence. That is a known limit, not a supported feature, and the API only
creates single-payment runs.

```
python -m app.sim fixtures/duplicate_success.json
```

## Reconciliation (demo only)

This section describes the simulator. There is no lookup in the integration API; see
[Integration API](#integration-api).

`runner.reconcile(run_id, refund_id, at_tick)` asks the simulated provider about one
refund at a logical tick. The tick defaults to the run clock and cannot move backwards.

A lookup also cannot move the clock past a scheduled delivery that has not been applied
yet. Example: a fixture has deliveries at tick 2 and tick 3, and the run is at tick 0
with neither applied. Only ticks up to the **next** delivery count, so a lookup at tick
2 is allowed and a lookup at tick 3 is refused, even though tick 3 is a delivery tick
later in the schedule. Allowing tick 3 would leave the run unable to apply the delivery
at tick 2, because that would move the clock backwards. After the delivery at tick 2 is
applied, the next unapplied delivery is tick 3, so a lookup at tick 3 becomes valid.
Once nothing is left to deliver any later tick works. A refused lookup writes nothing:
no observation, action, finding or
clock change. The page and the API both answer with a 400 that names the next delivery
tick.

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

## Integration API

A trusted backend can register its own payment and refunds and submit already
normalized refund observations, then read the protected status. This is **not** a
payment provider webhook endpoint. It does not verify provider signatures, it does not
execute refunds, and it sends no outgoing notifications.

Every route needs the configured key in an `Authorization: Bearer` header. The key
comes from `INTEGRATION_API_KEY`. It is never accepted in a URL, never echoed in a
response, and never written to a tracked file. With no key configured the whole
surface answers 404, and the public demonstration keeps working without one.

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/workspaces` | create a workspace and its payment |
| POST | `/v1/workspaces/{id}/refunds` | register a refund intent |
| POST | `/v1/workspaces/{id}/deliveries` | submit one normalized delivery |
| GET | `/v1/workspaces/{id}` | protected status with evidence |

```bash
curl -X POST https://<host>/v1/workspaces \
  -H "Authorization: Bearer $INTEGRATION_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"provider":"psp_x","payment_id":"pay_1","captured_paise":100000}'
```

A delivery returns the recorded ingestion outcome and the refund's resulting status
separately, because a recorded delivery does not mean the refund succeeded:

```json
{"outcome":"accepted","recorded":true,
 "refund":{"refund_id":"R1","status":"succeeded","confirmed_paise":30000,
           "review_paise":0,"reasons":[]}}
```

Amounts are integer paise and must be sent as a JSON number. Strings, floats and
booleans are rejected. Identifiers are bounded to 64 characters and unknown fields
are refused. The delivery origin is fixed to a webhook on the server, so a caller
cannot claim a provider lookup happened.

Registering the same refund twice is a safe retry and answers 200 with
`already_admitted`. Changing the amount for an existing refund id is a 409 with
`refund_id_conflict`.

There is no lookup route. Provider lookup stays inside the demonstration until there
is a real adapter, and the API never answers from fixture truth.

### Example client

`examples/integration_client.py` is a second application using the service. It imports
only `httpx`, `os` and `sys`, never the lab itself. Its notifications are synthetic,
but they travel through the real `/v1` API, so the rules, the cap, duplicate handling
and stored evidence are the ones the service actually applies.

```
python -m pip install httpx
set LAB_BASE_URL=http://127.0.0.1:8031
set INTEGRATION_API_KEY=your-key
python examples/integration_client.py
```

The key is read from the environment and sent only in the `Authorization` header. It
is never printed, never placed in a URL, and never echoed from an error. The client
walks one workspace through a captured payment of 100000 paise, a 30000 paise refund,
the same success event delivered three times under three attempt ids, an identical
retry, and a later pending event, printing the confirmed amount after each step.

`tests/test_integration_client.py` drives this exact flow through the real `/v1` routes
in process, so the client logic is covered by the test suite. A run against a live
server additionally needs that server started with `INTEGRATION_API_KEY` set; without
it every `/v1` path answers 404 and the example exits with a clear message rather than
fabricating a result.

### Hyperswitch adapter

`POST /webhooks/hyperswitch` accepts a Hyperswitch refund webhook, verifies it and
hands it to the same processing service every other delivery uses. No refund rules are
duplicated in the adapter.

- **Signature.** HMAC-SHA512 over the exact raw request body with
  `HYPERSWITCH_WEBHOOK_SECRET`, hex encoded in `X-Webhook-Signature-512`, compared in
  constant time before the body is parsed. A tampered body and a wrong digest are both
  401 with nothing written.
- **Availability.** The route answers 404 unless the secret, workspace, merchant and
  payment are all configured. `/v1` bearer auth is unchanged and separate.
- **Binding.** `HYPERSWITCH_WORKSPACE_ID`, `HYPERSWITCH_MERCHANT_ID` and
  `HYPERSWITCH_PAYMENT_ID` are configuration, not payload. A body naming another
  merchant or payment is 403, and a `workspace_id` field in the body is ignored.
- **Identity.** `event_id`, `refund_id`, amount and currency come from the payload
  unchanged. One attempt id is generated per received delivery, `hs:<event_id>:<n>`,
  so three deliveries of one event are recorded as three attempts and one event.
- **Preconditions.** The payment and refund intent must already exist. A notification
  never registers either and never rewrites an intended amount.
- **Statuses and currency.** Only `refund_succeeded` and `refund_failed` are accepted,
  and `event_type` must agree with `content.object.status`. `refund_review`, any other
  status, and any currency other than INR are refused with 422 rather than coerced.
  An INR amount that differs from the intent is passed through, so the existing
  processor records it as review evidence.

`docs/samples/hyperswitch-refund-succeeded.json` is a **documented sample**, built from
the official outgoing webhook envelope and refund object schema. It was not captured
from a live Hyperswitch instance. The adapter is verified against that documented
payload and is **not** live Hyperswitch verified. A genuine sandbox delivery still
needs an account, an INR capable connector and a public HTTPS endpoint.

### Measured against a live server

Run over HTTP against a local server started with a disposable test key, one
workspace, no fixtures. The server's own records, read back through `GET
/v1/workspaces/{id}` and from its database, showed:

| Claim | Evidence |
|---|---|
| Registration succeeds | one intent `R1` of 30000, `admitted_paise` 30000, `accepted` |
| Three deliveries confirm once | `a1` accepted, `a2` and `a3` `duplicate_event`; confirmed 30000, not 90000 |
| Identical retry not recorded | four delivery rows total, `a1` present once; the retry returned `recorded: false` |
| Later pending does not undo success | `a4` with event `E9` accepted as `pending`; refund still `succeeded`, confirmed 30000 |
| No fixture involved | `fixture_text` empty, zero demo action rows, run kind `integration` |
| Surfaces stay separate | `/runs/{id}` and `/api/runs/{id}` both 404 for this workspace |
| Key enforced | no key 401, wrong key 401 |

Final status: one refund, 30000 confirmed, 0 under review, no findings and no
unmatched deliveries.

The demo pages and the demonstration API cannot reach integration data. A demo route
pointed at an integration workspace answers 404, so there is no path to user data
without the key, and no path to a simulated lookup over it.

Identity is scoped to one workspace and one provider. The same provider event id in
two workspaces is two independent observations. Deduplication is not promised across
workspaces.

## Pages

Three destinations: **Overview** (`/`), **Demo** (`/walkthrough`) and **Integrate**
(`/integrate`). The visual system is a dark charcoal canvas with a navigation rail on
desktop that becomes a top bar on narrow screens, one accent green for confirmed
amounts and amber for amounts under review.

The demo console leads with one refund summary: status, confirmed amount and amount
under review. Processing controls and the notification history follow, so both fit on
the first screen at a normal desktop size. The naive comparison is a collapsed panel
whose summary line already shows both totals, so the divergence is one click away
during a demo without pushing the timeline down. Longer explanations, evidence,
identifiers and the full comparison sit behind disclosures.

Every figure is rendered from the same report the JSON API returns. Nothing is
precomputed for the walkthrough, and a scenario's explanation never asserts a current
total before the deliveries run.

A run page offers Step, Run to end, Reconcile, Replay and Download evidence.

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
Unknown runs and scenarios are 404, a stale cursor is 409, an invalid lookup including
one past the next delivery is 400, and a lookup that goes stale while the provider is
being asked is 409.

A step response carries an `outcome`. Repeating a delivery attempt with identical
content returns the recorded outcome with `applied: false`. Reusing an attempt id with
different content returns `attempt_conflict`: the original row is untouched, neither
projection moves, and the attempted payload is kept as finding evidence. That is a 200
rather than a 409, matching how a reused event id with a different payload is already
handled. A 409 is reserved for a conflict with the run's current state, where retrying
against fresh state is the right advice.

Each distinct conflicting claim gets its own finding, keyed by the attempt id and a
digest of the claim, so a second and third conflict do not overwrite the first.
Repeating an identical conflicting claim is idempotent.

A lookup result is used only if the run has not moved past it. The check runs twice:
before asking the provider, and again inside the write transaction that records the
result. The provider call happens outside the database lock, so a delivery can be
applied in between; when that happens the result is stale, nothing is written, and the
caller gets a 409.

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
docs/demo-script.md  a 90 to 110 second walkthrough of the finished product
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