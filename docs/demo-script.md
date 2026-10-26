# Demo script

A 90 to 110 second walkthrough of the finished product. Target 100 seconds.

Open on `http://localhost:8031/` before you start recording, and have a second tab on
`/walkthrough`. Nothing is preloaded or pre-run: every number below is produced live
from the backend.

All provider notifications and the provider lookup in this demo are simulated. No real
merchant payment is processed.

| Time | On screen | Say |
|---|---|---|
| 0:00–0:10 | Overview | "This is Refund Consistency. It keeps one defensible record per refund from provider notifications. It doesn't move money." |
| 0:10–0:20 | Click **Try the demo**, then **Open** on the first scenario | "The simulator is the public demo. Same processing rules as the API, over fixed scenarios." |
| 0:20–0:32 | Press **Run to end**. Point at Confirmed ₹300.00 and the Naive baseline line ₹900.00 | "One success notification, delivered three times. The service confirmed three hundred. The naive baseline counted every delivery and recorded nine hundred." |
| 0:32–0:42 | Click the **Naive baseline** row to expand, then collapse it | "That's a local record, not three payouts. Duplicates are settled by the event id; no lookup needed." |
| 0:42–0:50 | Point at the notification history | "Every delivery attempt is in the timeline, with the outcome it produced." |
| 0:50–1:05 | Back to **Demo**, open **The notification that never arrived**, press **Run to end** | "Here the success notification never arrived. Both sides start from not knowing." |
| 1:05–1:18 | Set Query tick to 3 and press **Reconcile**. Confirmed becomes ₹300.00 | "A lookup asks the simulated provider for what the message never carried. That's the only thing lookup is for." |
| 1:18–1:32 | Back to **Demo**, open **When the provider contradicts itself**, **Run to end**, then **Reconcile** | "A failure arrived, then the provider reported success. The refund stays under review. Neither answer is picked, and the amount stays visible as unresolved." |
| 1:32–1:42 | Scroll to the disclosures, open **Evidence** briefly | "Both claims are kept, and the reason is readable. Reconciling doesn't resolve a contradiction." |
| 1:42–1:50 | Visit **Integrate** | "The real API takes a payment, refunds and normalized notifications, and returns protected status. It needs a server configured key, and without one it's simply unavailable." |

## Notes

- Do not read out the naive comparison before pressing **Run to end**. The scenario
  explains itself first; the figures appear after the deliveries apply.
- The protected total reaches ₹300 on the first success and stays there when the same
  event is delivered again. Only the naive baseline keeps counting.
- Do not describe the review case as something reconciliation fixes. It does not.
- The integration page never contains a key; do not paste one on screen.
- If the free instance has gone to sleep, load the page once and wait before recording.

## What to leave out

No activity charts, no customer names, no metrics. The product has none.