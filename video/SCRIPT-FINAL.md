# Final narration transcript

Technical names normalized from local speech recognition; listening review remains necessary.

Imagine a three hundred rupee refund.

Simple, right?

Now the same success notification arrives three times.

A naive backend records nine hundred rupees.

Not three payouts. Just one very wrong number.

And that’s before notifications start going missing or contradicting each other.

That’s why I built HISAAB.

It’s a Python backend using FastAPI.

Applications register refunds and send notifications through an authenticated API.

HISAAB handles duplicates, stores evidence using SQLite transactions and returns a refund status.

The dashboard?

That’s just a demo.

Watch this.

First notification: both processors show three hundred rupees.

Second: the naive processor says six hundred.

Third: nine hundred.

HISAAB? Still three hundred.

Three deliveries. One event. One correctly recorded refund.

What if a notification never arrives?

HISAAB keeps the outcome unknown until a simulated provider lookup supplies evidence.

And when success and failure disagree, it doesn’t guess.

It keeps both claims under review.

I also built a Hyperswitch webhook adapter.

This signed sample goes through the actual HTTP endpoint.

Duplicates leave the total unchanged, and invalid signatures get rejected.

It’s a local test, not a live provider delivery.

Alan helped me build this in smaller checkpoints.

Here’s the integration API task, the duplicate-handling test and its Git commits.

Independent review caught a few gaps.

I brought these findings back to Alan and verified the fixes with regression tests.

HISAAB. Ek refund. Sau nakhre.

Every refund accounted for.
