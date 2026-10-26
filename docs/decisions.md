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
Cost: runtime files sit in the container writable layer. Replacing the container
loses them, so a redeploy or a platform restart that recreates it clears stored runs.
A plain `docker restart` on the same container does not. We claim no durability from
the public link. WAL on an attached network volume is unverified and the health check
reports degraded instead of failing when WAL is refused.

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

A step and run to end use different scopes. A failed step leaves exactly one delivery
unapplied and the cursor where it was, so the same step can be retried. A failed run to
end leaves every delivery in that call unapplied, because the loop and each cursor
update share one transaction. Nothing is half applied, and each of those deliveries is
still available for retry.

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

## A lookup cannot overtake a pending delivery

Chosen over letting the lookup move the clock freely.
Why: the run clock only moves forward and each delivery is checked against it. If a
lookup at tick 5 landed while the delivery at tick 2 was still unapplied, applying tick
2 would fail the ordering check forever and the run could never advance. Refusing the
query keeps one ordering rule instead of a special case.
Cost: a lookup cannot ask about a time after the next delivery until that delivery is
applied. A tick equal to the next delivery is allowed, since level is not backwards,
and once nothing is left to deliver any later tick works.

## A reused attempt id is compared before it is trusted

Chosen over trusting the attempt id alone.
Why: the attempt id is what separates our own transport retry from a new provider
delivery, so reusing it with different content is either a client bug or a fabricated
delivery. Both matter. Treating it as an identical retry would drop the claim with no
trace.
Cost: one extra claim comparison on a path that is normally a cheap no-op. The
conflicting claim is kept in the finding detail rather than as a delivery row, so it
cannot reach either projection and no total moves.

The response is a 200 with an `attempt_conflict` outcome, not a 409, because a reused
event id with a different payload is already recorded rather than rejected. 409 stays
for a conflict with the run's current state, where the caller can retry against fresh
state.

Each distinct claim gets its own finding key, built from the attempt id and a digest of
the claim. One key per attempt id was wrong: a second conflict overwrote the first
finding's detail while the row's event id still named the earlier claim, so the
evidence contradicted itself. Repeating the same claim produces the same key, which
keeps the retry idempotent.

## A lookup is validated again inside the write transaction

Chosen over trusting the check made before the provider call.
Why: the provider call must not hold the database lock, so the run can advance between
the check and the write. That used to store an action whose tick was behind the run,
leaving replay with a tick order it refuses. The recheck costs one row read inside a
transaction that is already open.
Cost: two places express the same rule, so they must stay in agreement. A stale result
is discarded after the provider was already asked, which wastes that query but never
stores anything. Repeated conflict findings also mean the finding count grows with the
number of distinct bad claims, which is intended but means the count is not bounded by
the number of attempts.

## The digest carries the build

Chosen over hashing a hardcoded version string.
Why: the docs promise a digest is comparable within one engine, and a constant
version could not tell two builds apart. A matching digest across changed code would
read as proof of determinism when it is not.
Cost: a digest is only comparable within one commit. Anything that changes the commit
changes every digest, which is intended but means stored digests age quickly.

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

## What the mutation checks actually caught

Three temporary mutations were applied in a separate git worktree and the full suite
was run against each. Results are what the tests detected, not what we hoped.

| Mutation | Detected | By |
|---|---|---|
| `BEGIN IMMEDIATE` changed to `BEGIN` | yes | both concurrency tests: two 600 requests on 1000, and two threads delivering one event |
| partial unique index made non-unique | partly | the direct constraint test only; both concurrency tests still passed |
| explicit `ROLLBACK` removed | no | nothing failed |

What that means:

- The write lock is load bearing and tested. Without it both concurrency invariants
  break.
- The unique index is a real backstop, proven by a direct insert test, but the
  concurrency tests do not depend on it because the lock serializes writers first.
  Defence in depth, with only the outer layer exercised by the race tests.
- The explicit `ROLLBACK` is not what saves us. Closing the connection rolls an open
  transaction back on its own, so removing the statement changes nothing observable.
  The rollback test proves the effect, not that this line produces it.

## Integration workspaces share the tables and carry a kind

Chosen over separate integration tables or a second processor.
Why: admission and ingest already carry the guarantees that matter, so a fixture free
workspace only needs a payment and a flag. The status view reads the same report the
demo reads, minus the naive side.
Cost: product rows carry demo shaped columns (`fixture_text`, the cursor, the action
log), and every demo route now depends on an explicit kind check to stay out of
integration data. `runs.kind` is added with an additive migration so an existing
database keeps its rows as demo runs.

## A workspace and its payment are created in one transaction

Chosen over reusing `create_run` then `add_payment`.
Why: those each open their own transaction, so a failure between them would leave a
workspace with no payment, which the service cannot serve and nothing would clean up.
Cost: one more store method beside the demo path, and the two paths share private row
writers to avoid drifting.

## The integration API is absent without a key

Chosen over mounting the routes and answering 401.
Why: an absent surface is indistinguishable from a path that does not exist, so
without a configured key there is nothing to discover or probe. The demonstration
routes are unaffected, so the public lab keeps working on its own.
Cost: the routes are always registered, so the check is a dependency rather than a
mounting decision. One key for the whole server means no per-client revocation.

## The key is read from the header only

Chosen over accepting a query parameter.
Why: a key in a URL ends up in proxy logs and browser history. A header does not.
Cost: the key cannot be used from a plain browser address bar, which is fine for a
backend integration.

## The delivery origin is fixed on the server

Chosen over accepting an origin field.
Why: a caller could otherwise claim an observation came from a provider lookup, and
the naive baseline skips lookups. Letting that be caller controlled would make the
naive comparison meaningless.
Cost: there is no way to submit a lookup observation, which is correct until a real
provider adapter exists.
