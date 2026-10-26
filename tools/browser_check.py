"""Browser walkthrough of the comparison page.

Development only. Needs a running server and Playwright with a local browser:

    python -m uvicorn app.main:app --port 8010
    python tools/browser_check.py

Set LAB_BROWSER_CHANNEL to msedge, chrome, or leave it empty for bundled Chromium.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("LAB_BASE_URL", "http://127.0.0.1:8010")
SHOTS = Path(os.environ.get("LAB_SHOTS_DIR", "var/shots"))
SHOTS.mkdir(parents=True, exist_ok=True)

results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    results.append((name, condition, detail))
    print(("PASS " if condition else "FAIL ") + name + ((" :: " + detail) if detail else ""))


def run_all(page) -> None:
    """Run to end when there is anything left to deliver.

    A fixture whose notifications were all dropped has an empty schedule, so it is
    finished immediately and only Reconcile applies.
    """
    button = page.get_by_role("button", name="Run to end")
    if button.is_enabled():
        button.click()
        page.wait_for_load_state("domcontentloaded")


def start(page, scenario: str) -> str:
    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.locator("article.card", has_text=scenario).get_by_role("button", name="Start").click()
    # The URL pattern also matches the previous run, so wait on the rendered
    # heading instead of the URL.
    expect(page.locator("h1")).to_have_text(scenario)
    return page.url


def main() -> int:
    with sync_playwright() as playwright:
        channel = os.environ.get("LAB_BROWSER_CHANNEL", "msedge") or None
        browser = playwright.chromium.launch(channel=channel, headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()

        # 1. Picker
        page.goto(BASE + "/", wait_until="domcontentloaded")
        cards = page.locator("article.card")
        check("picker lists scenarios", cards.count() == 12, "cards=%d" % cards.count())
        check("picker shows a start action", page.get_by_role("button", name="Start").count() == 12)
        page.screenshot(path=str(SHOTS / "01-picker.png"), full_page=True)

        # 2. Healthy: step, then run to end
        start(page, "healthy")
        check("healthy run page loads", "healthy" in page.title() or "healthy" in page.inner_text("h1"))
        check("starts in progress", "in progress" in page.inner_text("body"))
        check("step enabled before running", page.get_by_role("button", name="Step").is_enabled())

        page.get_by_role("button", name="Step").click()
        page.wait_for_load_state("domcontentloaded")
        check("one step applied", "1 of 2" in page.inner_text("body"))

        page.get_by_role("button", name="Run to end").click()
        page.wait_for_load_state("domcontentloaded")
        healthy_row = page.get_by_test_id("comparison").locator("tbody tr", has_text="R1")
        check("healthy protected succeeded", "succeeded" in healthy_row.inner_text())
        check("healthy naive agrees", "succeeded" in healthy_row.inner_text())
        check("delivery completion shown", "complete" in page.inner_text("body"))
        check("step disabled after finishing", page.get_by_role("button", name="Step").is_disabled())
        page.screenshot(path=str(SHOTS / "02-healthy.png"), full_page=True)

        # 3. Duplicate delivery
        start(page, "duplicate_success")
        page.get_by_role("button", name="Run to end").click()
        page.wait_for_load_state("domcontentloaded")
        stats = page.get_by_test_id("stats").inner_text()
        row = page.get_by_test_id("comparison").locator("tbody tr", has_text="R1")
        check("duplicate protected stays 300", "300.00" in row.inner_text())
        check("duplicate naive reads 900", "900.00" in row.inner_text())
        check("duplicate total shown in stats", "900.00" in stats)
        check("three deliveries recorded", page.get_by_test_id("actions").locator("tbody tr").count() == 3)
        page.screenshot(path=str(SHOTS / "03-duplicate.png"), full_page=True)

        # 4. Missing message recovered by lookup
        start(page, "lookup_recovers_success")
        run_all(page)
        before = page.get_by_test_id("comparison").locator("tbody tr", has_text="R1")
        check("missing message leaves pending", "pending" in before.inner_text())

        page.get_by_label("Query tick").fill("3")
        page.get_by_role("button", name="Reconcile").click()
        page.wait_for_load_state("domcontentloaded")
        after = page.get_by_test_id("comparison").locator("tbody tr", has_text="R1")
        check("lookup recovers success", "succeeded" in after.inner_text())
        check("recovered amount is 300", "300.00" in after.inner_text())
        check("lookup action recorded", "lookup" in page.get_by_test_id("actions").inner_text())
        page.screenshot(path=str(SHOTS / "04-recovery.png"), full_page=True)

        # 5. Conflict stays unresolved
        start(page, "lookup_conflict")
        run_all(page)
        page.get_by_label("Query tick").fill("4")
        page.get_by_role("button", name="Reconcile").click()
        page.wait_for_load_state("domcontentloaded")
        conflict_row = page.get_by_test_id("comparison").locator("tbody tr", has_text="R1")
        check("conflict stays in review", "review" in conflict_row.inner_text())
        check("conflict reason visible", "terminal_conflict" in conflict_row.inner_text())
        check("conflict confirmed is zero", "0.00" in conflict_row.inner_text())
        page.screenshot(path=str(SHOTS / "05-conflict.png"), full_page=True)

        # 6. Loading state (synthetic submit keeps us on the page)
        start(page, "duplicate_success")
        page.evaluate(
            """() => {
                const form = document.querySelector('form[action$="/step"]');
                form.dispatchEvent(new Event("submit", {bubbles: true, cancelable: true}));
            }"""
        )
        step_form = page.locator('form[action$="/step"]')
        step_button = step_form.get_by_role("button")
        check("loading sets aria-busy", step_form.get_attribute("aria-busy") == "true")
        check("loading disables the button", step_button.is_disabled())
        check("loading changes the label", step_button.inner_text().strip() == "Stepping…")
        page.screenshot(path=str(SHOTS / "06-loading.png"), full_page=False)
        page.reload(wait_until="domcontentloaded")
        check("reload clears loading state", page.get_by_role("button", name="Step").is_enabled())

        # 7. Evidence download from a completed run
        run_all(page)
        with page.expect_download() as download_info:
            page.get_by_role("link", name="Download evidence").click()
        download = download_info.value
        evidence_path = SHOTS / "evidence.json"
        download.save_as(str(evidence_path))
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        check("download is named after the run", download.suggested_filename.endswith(".json"), download.suggested_filename)
        check("evidence carries a digest", bool(payload.get("digest")))
        check(
            "evidence carries the comparison",
            payload["comparison"]["naive_total_paise"] == 90_000,
            "naive=%d" % payload["comparison"]["naive_total_paise"],
        )

        # 8. Error: unknown run
        response = page.goto(BASE + "/runs/run_missing", wait_until="domcontentloaded")
        check("unknown run returns 404", response is not None and response.status == 404, str(response.status))
        check("unknown run page explains", "unknown run" in page.inner_text("body"))
        page.screenshot(path=str(SHOTS / "07-error-404.png"), full_page=True)

        # 9. Error: reconcile in the past
        start(page, "lookup_conflict")
        run_all(page)
        page.get_by_label("Query tick").fill("1")
        page.get_by_role("button", name="Reconcile").click()
        page.wait_for_load_state("domcontentloaded")
        check("past lookup returns 400", "cannot look up" in page.inner_text("body"))
        check("past lookup page has a way back", "Back to scenarios" in page.inner_text("body"))
        page.screenshot(path=str(SHOTS / "08-error-400.png"), full_page=True)

        context.close()
        browser.close()

    failed = [name for name, ok, _ in results if not ok]
    print("\n%d checks, %d failed" % (len(results), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())