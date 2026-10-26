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

## Corrections

### The conflict flip is status level

The v2 plan said the naive side would show 300 rupees in one order and 0 in the
other. That was wrong. The naive total is a sum over successes so it stays at 300 in
both orders. Only the status flips. The stronger fact is that naive then contradicts
itself by reporting failed while holding 300. The protected side reports review in
both orders.

### Naive writes need their own commit boundary

The plan first committed the naive projection inside the same transaction as
everything else. That made the naive side durable and hid the torn write the lab is
meant to show. The harness now commits its own attempt row and cursor first and each
engine owns its own transaction. The same fault is injected at the same point for
both engines. The only difference is whether the two writes were one unit.

### FastAPI validation claim

M1 justified FastAPI by strict request model validation. M2 put validation in the
domain dataclasses because the reducer must be testable without HTTP. FastAPI now
carries routing and ASGI test ergonomics only. Flask would be an equally good choice
if the remaining work stops needing ASGI tests. Dependency count would not change.