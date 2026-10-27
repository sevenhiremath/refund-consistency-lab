# HISAAB submission video

This is a 117-second Remotion assembly at 1920×1080, 30 fps. Explicit missing-evidence cards make unfinished sections visible. It is not ready for submission until real recordings, voice and voice-aligned captions are imported.

## Reproduce

Run `python prepare.py` once from this directory. It copies existing authentic app screenshots without changing their originals, extracts the real test and historical commit, and creates `edit.json` only if absent. It never changes application code or databases.

Install the pinned packages with `pnpm install --frozen-lockfile` (omit the frozen flag before the first lockfile exists). Run `node check.mjs`, then `node render.mjs sample` for a ten-second real-media export or `node render.mjs` for the complete assembly. Use `node render.mjs 720` for the 720p fallback. Renders go to ignored `out/`. The installed Chrome is selected explicitly; no additional browser is downloaded. Override its path with `HISAAB_CHROME` if needed.

Place raw media in ignored `public/media/`. Preserve originals and list actual filenames, source, trims and review status in `asset-manifest.json`. Update `edit.json` scene media and `trimStart` (seconds). Remove the scene's `missing` field and use an accurate label when replacing a placeholder. Screen recordings are always muted. Use separate scenes for different trims of the same recording.

Set `voice` and optional `music` to actual filenames. Music starts at low gain with fades; listening determines the final balance. Set `captions` to `{start,end,text}` phrases measured against the actual voice, in seconds. Never generate timing by equal word spacing. `node check.mjs` exports SRT only after actual timings exist. No provisional SRT is represented as synchronized.

Before final: run `node check.mjs --final`, preserve `hisaab-assembly.mp4` as backup, then `node render.mjs final` (or `node render.mjs final 720`). Inspect H.264/AAC streams and duration with ffprobe. Watch values, labels and links and listen to the entire MP4. This renderer does not establish a listening review.

## Factual review

The API appears before the demo. The dashboard demonstrates shared rules; it is not described as calling the Bearer API. Public integration is not enabled. States are pending, succeeded, failed and review; confirmed is an amount. Recovery/conflict are simulated lookups. Adapter footage must be signed documented sample / local HTTP test and never represented as live provider delivery. Commit ac20202 is historical; its test count is not today's count. Source evidence is labeled as source, not a fresh execution.

## Submission draft

HISAAB — Refund consistency engine. Ek Refund. Sau Nakhre.

A Python/FastAPI backend that maintains refund records, counts matching events once and preserves conflicting evidence for review in SQLite transactions. The dashboard demonstrates the rules. A separate authenticated API accepts normalized observations; Hyperswitch has a signature-verifying adapter.

Repository: https://github.com/sevenhiremath/refund-consistency-lab

Demo: https://refund-consistency-lab.onrender.com

Video link: [add your uploaded video URL]. Organizer/form links pending from the user. Upload and submission remain the user's actions.


The original silent assembly remains in `out/hisaab-backup-assembly.mp4`. The complete voiced 720p fallback is `out/hisaab-complete-rough.mp4`; reproduce with `python rough.py`. Final narration text is `SCRIPT-FINAL.md`. The original `voice.mp4` is audio-only MP4/AAC; FFmpeg converts it to 48 kHz PCM WAV with a 70 Hz high-pass and loudnorm I=-16 TP=-1.5 LRA=9, with no tempo change. The final MP4 uses the original narration timeline, including its natural pauses. No background track has been invented.
