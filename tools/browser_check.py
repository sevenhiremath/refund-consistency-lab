"""Browser walkthrough of the current interface.

Development only. Needs a running server and Playwright with a local browser:

    python -m uvicorn app.main:app --port 8031
    python tools/browser_check.py

Set LAB_BASE_URL to point elsewhere, and LAB_BROWSER_CHANNEL to msedge, chrome, or
leave it empty for bundled Chromium.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("LAB_BASE_URL", "http://127.0.0.1:8031")
SHOTS = Path(os.environ.get("LAB_SHOTS_DIR", "var/shots"))
SHOTS.mkdir(parents=True, exist_ok=True)

results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    results.append((name, condition, detail))
    print(("PASS " if condition else "FAIL ") + name + ((" :: " + detail) if detail else ""))


def api(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def run_id(url: str) -> str:
    return url.split("/runs/")[1].split("?")[0]


def start(page, scenario: str) -> None:
    page.goto(BASE + "/walkthrough", wait_until="domcontentloaded")
    row = page.locator("table tbody tr", has_text=scenario.split("_")[0])
    page.locator("table tbody tr").filter(has_text=scenario).get_by_role("button", name="Start").click()
    page.wait_for_load_state("domcontentloaded")
    expect(page.locator("h1")).not_to_be_empty()


def run_all(page) -> None:
    button = page.get_by_role("button", name="Run to end")
    if button.is_enabled():
        button.click()
        page.wait_for_load_state("domcontentloaded")


def main() -> int:
    with sync_playwright() as playwright:
        channel = os.environ.get("LAB_BROWSER_CHANNEL", "msedge") or None
        browser = playwright.chromium.launch(channel=channel, headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))

        # Overview and navigation
        page.goto(BASE + "/", wait_until="domcontentloaded")
        nav = page.locator("nav[aria-label=Primary] a")
        check("three destinations", nav.count() == 3, str(nav.count()))
        check(
            "nav labels",
            [nav.nth(i).inner_text().strip() for i in range(3)]
            == ["Overview", "Demo", "Integrate"],
        )
        check("overview heading is not empty", page.locator("h1").inner_text().strip() != "")
        page.screenshot(path=str(SHOTS / "overview.png"))

        # Demo lists all twelve
        page.goto(BASE + "/walkthrough", wait_until="domcontentloaded")
        rows = page.locator("section table tbody tr")
        check("twelve scenarios listed", rows.count() == 12, str(rows.count()))
        labels = [
            rows.nth(i).locator("td").first.inner_text().strip().split("\n")[0]
            for i in range(rows.count())
        ]
        check("every scenario row has a label", all(labels), str(labels[:3]))

        # Every scenario opens with a heading and a title
        names = [item["name"] for item in api("/api/scenarios")["scenarios"]]
        empty = []
        for name in names:
            page.goto(BASE + "/walkthrough", wait_until="domcontentloaded")
            page.locator("table tbody tr").filter(has_text=name).get_by_role("button", name="Start").click()
            page.wait_for_load_state("domcontentloaded")
            if not page.locator("h1").inner_text().strip():
                empty.append(name)
            if page.title().strip().startswith("·"):
                empty.append(name + ":title")
        check("every scenario has a heading and title", not empty, ",".join(empty))

        # Duplicates: displayed totals come from the backend
        page.goto(BASE + "/walkthrough", wait_until="domcontentloaded")
        page.locator("table tbody tr").filter(has_text="duplicate_success").get_by_role("button", name="Start").click()
        page.wait_for_load_state("domcontentloaded")
        rid = run_id(page.url)
        run_all(page)
        refund = api("/api/runs/%s" % rid)["comparison"]["refunds"][0]
        summary = page.locator(".summary").first.inner_text()
        check("protected 300 shown", "₹300.00" in summary, summary.replace("\n", " | "))
        check("naive total reachable", str(refund["naive_total_paise"] // 100) in page.content())
        check("comparison is a disclosure", page.locator("details.compare").count() == 1)
        page.screenshot(path=str(SHOTS / "duplicates.png"))

        # Partials: comparison summary uses whole run totals, breakdown stays
        page.goto(BASE + "/walkthrough", wait_until="domcontentloaded")
        page.locator("table tbody tr").filter(has_text="partials_duplicate").get_by_role("button", name="Start").click()
        page.wait_for_load_state("domcontentloaded")
        rid = run_id(page.url)
        run_all(page)
        report = api("/api/runs/%s" % rid)
        whole = page.locator("details.compare > summary").inner_text()
        check(
            "comparison summary shows whole run naive total",
            "₹800.00" in whole,
            whole.replace("\n", " "),
        )
        check(
            "comparison summary shows whole run protected total",
            "₹500.00" in whole,
        )
        page.locator("details.compare > summary").click()
        breakdown = page.locator("details.compare").inner_text()
        check("per refund breakdown kept", "₹600.00" in breakdown and "₹300.00" in breakdown)
        check(
            "breakdown matches the report",
            any(row["protected_confirmed_paise"] == 30000 for row in report["comparison"]["refunds"]),
        )
        page.screenshot(path=str(SHOTS / "partials.png"))

        # Missing recovery and conflict still work
        page.goto(BASE + "/walkthrough", wait_until="domcontentloaded")
        page.locator("table tbody tr").filter(has_text="lookup_recovers_success").get_by_role("button", name="Start").click()
        page.wait_for_load_state("domcontentloaded")
        check("guidance names tick 3", "Query tick to 3" in page.inner_text("body"))
        page.get_by_label("Query tick").fill("3")
        page.get_by_role("button", name="Reconcile").click()
        page.wait_for_load_state("domcontentloaded")
        check("recovery confirms 300", "₹300.00" in page.locator(".summary").first.inner_text())

        page.goto(BASE + "/walkthrough", wait_until="domcontentloaded")
        page.locator("table tbody tr").filter(has_text="lookup_conflict").get_by_role("button", name="Start").click()
        page.wait_for_load_state("domcontentloaded")
        run_all(page)
        page.get_by_label("Query tick").fill("4")
        page.get_by_role("button", name="Reconcile").click()
        page.wait_for_load_state("domcontentloaded")
        check("conflict stays under review", "review" in page.locator(".summary").first.inner_text())

        # Narrow layout
        narrow = browser.new_context(viewport={"width": 390, "height": 844})
        small = narrow.new_page()
        for path in ("/", "/walkthrough", "/integrate"):
            small.goto(BASE + path, wait_until="domcontentloaded")
            overflow = small.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
            check("no overflow on %s" % path, overflow <= 1, str(overflow))

        check("no page errors", not errors, "; ".join(errors[:2]))

        context.close()
        narrow.close()
        browser.close()

    failed = [name for name, ok, _ in results if not ok]
    print("\n%d checks, %d failed" % (len(results), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())