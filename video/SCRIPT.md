# HISAAB narration — recording draft

Preserve usable voice paragraphs. No TTS. Remove the superseded implementation paragraph.



Read the paragraphs as one continuous explanation. Do not speak the section labels. Small natural wording changes are fine if they preserve the facts; captions must match the recording you actually use.

### Problem — about 20 seconds

> A three hundred rupee refund should be simple. But the same success notification can arrive three times. Another message might arrive late, never arrive, or disagree. If a backend treats every notification as a new refund, its records can be wrong.

### The backend product — about 18 seconds, before the first demo

> Hisaab is a Python backend with FastAPI. Applications register refunds and submit notifications through its authenticated API. It handles duplicates, stores evidence in SQLite transactions and returns refund status. The dashboard demonstrates those rules.

### Duplicate demonstration — about 22 seconds

> Here is one refund in the demo. After the first notification, both processors show three hundred. After three deliveries, the naive record shows nine hundred. Hisaab still shows three hundred. These are recorded amounts, not extra payouts.

### Missing and conflicting evidence — about 14 seconds

> When a notification never arrives, the outcome stays unknown. This simulated provider lookup adds the missing evidence. And if success and failure disagree, the refund stays under review.

### Adapter — about 16 seconds

> The Hyperswitch adapter also accepts signed webhooks. This documented sample travels through the real HTTP endpoint. Repeated deliveries leave the total unchanged. Invalid signatures are rejected. This was not sent by a live Hyperswitch instance.

### Alan — about 22 seconds

> I used Alan for one backend task at a time. Here is the API task, the duplicate-handling test, and the commit that records the change. Independent review found gaps. I brought those findings back to Alan and checked the fixes with regression tests.

### Closing — about 5 seconds

> Hisaab. Ek refund. Sau nakhre. Every refund accounted for.

The targets total around 117 seconds. Actual speech controls the edit; these are not precise audio timestamps. If a slower natural delivery exceeds the limit, shorten the adapter wording first while retaining its documented-sample qualification. Do not accelerate the whole recording to cram more words in.
