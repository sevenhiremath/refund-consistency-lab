# Decision log

Short record of tradeoffs and corrections. Newest corrections at the end of each
section. This is not a tutorial. It exists so a reader can see why a choice was made
and what it cost.

## Confirmed total is derived

Chosen over an append only ledger with a mutable balance.
Why: a running balance can disagree with the stored status and then nothing decides
which one is true. The total is a sum over immutable intents whose evidence is
unambiguous.
Cost: we cannot claim money moved. A refund under review drops out of confirmed.

## Conflicts stay in review

Chosen over picking the newest message or a rank order.
Why: arrival order is not evidence. A timestamp or sequence key becomes load bearing
exactly when the provider metadata is least reliable.
Cost: some refunds never resolve in a run. Confirmed totals fall. A human step is
needed later and is not built.

## Classify from the evidence set

Chosen over sorting events and applying terminal absorption in arrival order.
Why: the same evidence should produce the same answer regardless of delivery order.
That property is what makes the comparison testable.
Cost: the model cannot represent a provider reopening a refund after success.
Reversal and chargeback semantics are out of contract.

## Cap enforced at admission

Chosen over checking the total when a success is credited.
Why: by credit time the provider has already moved the money so the check can only
report an over refund and not prevent one.
Cost: reservations are never released. A failed refund keeps consuming capacity for
the rest of the run.

## SQLite with WAL

Chosen over Postgres.
Why: no service to deploy and the uniqueness constraints and transactions are what
carry the guarantees here. Two files are also easy to inspect.
Cost: one writer at a time. The concurrency proof shows constraints not throughput.

## One container image on a free host

Chosen over a managed database plus an app service.
Why: fewer moving parts in 24 hours and the lab has no data worth keeping.
Cost: the free host disk is ephemeral so a restart loses in flight runs. We do not
claim durability from the public link. WAL on an attached bucket is unverified and
the health check reports degraded instead of failing when WAL is refused.

## Outbound in doubt is a headless test

Chosen over a second comparison screen.
Why: the request timeout case is where idempotency keys matter but it needs a second
surface and about two more hours of UI.
Cost: the demo will not show a key doing its job. The README has to point at test
evidence instead.

## Event identity is checked across the run

Chosen over checking identity inside one refund only.
Why: one refund's evidence cannot see that the same event id was reused for a
different refund. The check therefore runs once over the whole run and its result is
passed into each refund.
Cost: the run and provider scope is implicit. There is no provider field yet so a
collision is computed over the deliveries handed to one evaluation. M3 has to make
that scope real or the check can compare unrelated runs.

## Unknown refund evidence

Chosen over dropping a delivery whose refund was never admitted.
Why: dropping it hides the disagreement. Counting it would corrupt valid totals. The
report returns it separately in `unmatched` so the evidence stays visible and no
total moves.
Cost: the report grows an untyped bucket. Whether such a delivery is accepted,
rejected or quarantined is not a domain question. Unknown evidence is also excluded
from the run-level identity check, so a reused event id on an unadmitted refund
cannot turn an admitted refund into a review. Ingest owns that cross-boundary case.
Acceptance check for M3 ingest: a delivery for an unadmitted refund creates no refund
intent, changes no confirmed or naive total, and is stored as quarantined evidence
that appears in `report.unmatched`. The domain half is covered by
`tests/test_rules.py::test_unknown_refund_evidence_is_visible_but_changes_no_totals`
and `test_unknown_refund_cannot_change_a_valid_refund`.

## One evidence row per delivery attempt

Chosen over a separate table for accepted claims only.
Why: the same row serves both jobs. Every delivery is evidence, and an `accepted`
marker plus a partial unique index makes at most one claim per event count. Duplicates
and conflicting payloads stay visible in the same place.
Cost: the report replays every row rather than reading only accepted ones. That is
deliberate. A conflicting claim has to be able to put two refunds under review and
accepted-only rows cannot do that.

## Both projections commit with the delivery

Every processed delivery is one BEGIN IMMEDIATE transaction: the delivery row, any
findings, the protected projection and the naive projection. The simulator harness does
not own a separate transaction, and neither processor writes on its own connection.
Why: the comparison is about processing rules, not durability, and one boundary keeps
both sides looking at exactly the same evidence.
Cost: the naive side cannot be made to tear, so the "credit written, status stale"
failure is described in this design but not demonstrated by the lab.

## Transaction boundaries

| Operation | Scope |
|---|---|
| admission | one transaction for the cap check and the insert |
| a single step | one transaction for that one delivery and the cursor |
| run to end | one transaction for every remaining delivery |
| reconcile | one transaction for the lookup evidence, its finding, the action row and the clock |

A step and run to end use different scopes. A failed step loses exactly one delivery
and leaves the cursor where it was, so the same step can be retried. A failed run to
end loses every delivery in that call, because the loop and each cursor update share
one transaction; nothing is half applied, but nothing from that call survives either.

SQLite allows one writer, so a step holds the write lock for one delivery while a run
to end holds it for the whole remaining schedule. Readers in WAL mode are not blocked,
so page loads keep working, but a second writer waits or hits the busy timeout. At
fixture size the schedule is a handful of deliveries, which is why the simpler single
transaction is kept instead of committing per delivery.

## Transport retry is gated by the attempt id

Chosen over a timestamp or a header flag.
Why: the attempt id is the only identity that separates our own retry of one delivery
from a provider retry of one event. The primary key decides, so the check cannot race.
Cost: the caller must reuse the attempt id when it retries. A client that invents a
new id per transport retry looks like a new provider delivery.

## Fixed fixtures before random failures

Chosen over seeded random fault injection for the first simulator.
Why: a fixture is a claim with a known answer. `duplicate_success` says one event
arrives three times and the protected total stays 300 rupees. When that fails the
cause is narrow. Random faults mostly produce cases nobody wrote an expected answer
for, so a failure says something broke without saying what.
Cost: fixtures only cover failure modes someone thought of. They miss interleavings
across refunds, clusters of faults, tick boundaries and any trigger not already in a
file. A seeded fuzz run needs an invariant checker to judge it and that is later work.

## Provider truth is not delivery

Chosen over deriving deliveries from provider events at run time.
Why: the protected processor must not be able to read a notification that was never
sent or one that has not happened yet. Only the delivery list becomes work. Provider
events exist for the report and for the future lookup path.
Cost: the fixture repeats the event identity in two lists, so loading rejects a file
where the two disagree.

## The cursor commits with the delivery

Chosen over advancing the cursor after processing, in its own transaction.
Why: a failure between the two would either lose a scheduled delivery or apply it
twice. Sharing the transaction makes a repeated step safe to retry with the same
expected cursor.
Cost: a whole run-to-end holds the write lock for the length of the schedule. Fine at
this size, not a throughput claim.

## Admission writes a pending projection

Chosen over writing projections only when a notification arrives.
Why: a refund with no notifications is a real state the UI has to show. Without this
it reads as missing rather than pending.
Cost: one extra projection pass per admission. It also made the M3 rollback test
stricter, since the surviving state row is now proof that the failed ingest added
nothing.

## A lookup is evidence, not an override

Chosen over letting the query result replace local state.
Why: a lookup is another observation and it goes through the same reducer. If it
contradicts a terminal result both stay visible and the refund stays in review. A
provider that changes its story cannot erase what it already told us.
Cost: more refunds end in review instead of resolving, and confirmed totals drop when
that happens.

## The naive baseline never sees lookups

Chosen over giving both processors the same recovery path.
Why: the baseline's documented defect is counting notifications. Giving it a lookup
would turn the comparison into a capability difference and the missing-notification
case would stop diverging.
Cost: the comparison is asymmetric. The report has to keep saying which side had a
lookup, or a reader will think the baseline was simply beaten.

## Every action goes in the digest

Chosen over hashing only the final state.
Why: the same fixture can be queried at different ticks and that changes the answer.
Hashing state alone would hide which query produced it.
Cost: an extra action log to keep in order, and a run that is stepped by hand must
produce the same actions as a run to end for the digests to agree.

## Delivery claims must match their event

Chosen over free-form delivery payloads.
Why: otherwise a typo in a fixture looks exactly like an intentional contradiction.
Cost: a deliberate conflict needs an extra flag and a payload, so the conflict fixture
is slightly more verbose than the others.

## Corrections

### The conflict flip is status level

The v2 plan said the naive side would show 300 rupees in one order and 0 in the
other. That was wrong. The naive total is a sum over successes so it stays at 300 in
both orders. Only the status flips. The stronger fact is that naive then contradicts
itself by reporting failed while holding 300. The protected side reports review in
both orders.

### Naive writes never got their own commit boundary

The plan called for the harness to commit its attempt row and cursor first, with each
engine writing in its own transaction, so the naive side could be shown tearing. That
was never built. M3 deferred it until the simulator cursor existed, M4 added the
cursor without splitting the boundary, and the ingest transaction still covers both
projections. An earlier version of this entry said the split had happened. It had not.

What it would take: a second connection for the naive projection, two statements
without an explicit transaction, and a fault injected between them. Until then the
torn write is a documented limit rather than a demonstrated behavior.

### FastAPI validation claim

M1 justified FastAPI by strict request model validation. M2 put validation in the
domain dataclasses because the reducer must be testable without HTTP. FastAPI now
carries routing and ASGI test ergonomics only. Flask would be an equally good choice
if the remaining work stops needing ASGI tests. Dependency count would not change.

### The naive baseline does no database work

The M1 README said the auto-commit connection setting was the failure the naive
baseline would exhibit. That was wrong. The baseline is a pure function over
deliveries that makes two mistakes only: it adds on every success notification and it
takes the last arriving status. It never writes to a database. Auto-commit still
matters for the ingest layer, where a write outside a transaction is a real defect,
but nothing in the current code relies on it and the torn write case is not built.