from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--screenshot", required=True)
    args = parser.parse_args()

    console_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on(
            "console",
            lambda message: (
                console_errors.append(message.text)
                if message.type == "error"
                else None
            ),
        )
        page.goto(args.url)
        page.wait_for_load_state("networkidle")
        page.wait_for_selector("#connection.live")
        page.wait_for_selector("#experiment-ledger tr")

        state = page.request.get(args.url + "/api/state").json()
        assert page.locator("#project-name").text_content() == state["status"]["project_name"]
        assert page.locator(".experiment-node").count() == sum(node.get("score") is not None for name, node in state["graph"]["nodes"].items() if name != "root")
        assert page.locator("#summary-open").count() == 1, "Missing run-summary button"
        assert page.locator("#experiment-search").count() == 1, "Missing experiment filter"

        page.locator("#experiment-search").fill("exp_0000")
        assert page.locator("#experiment-ledger tr").count() == 1

        page.locator("#experiment-ledger tr").first.click()
        page.wait_for_selector("#detail-dialog[open]")
        assert page.locator("#detail-title").text_content() == "exp_0000"
        page.locator("#detail-body").get_by_role("heading", name="Evidence files").wait_for()
        page.keyboard.press("Escape")
        page.locator("#experiment-search").fill("")

        page.locator("#summary-open").click()
        page.wait_for_selector("#summary-dialog[open] #summary-content h2")
        assert "What Was Explored" in page.locator("#summary-content").inner_text()
        assert "Final Test" in page.locator("#summary-content").inner_text()
        with page.expect_download() as download_info:
            page.locator("#summary-download").click()
        assert download_info.value.suggested_filename.endswith(".md")
        page.keyboard.press("Escape")

        screenshot = Path(args.screenshot)
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        for width, height in ((1440, 960), (390, 844), (320, 740)):
            page.set_viewport_size({"width": width, "height": height})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Page overflows at {width}px"
            if width <= 720:
                row_height = page.locator("#experiment-ledger tr").evaluate_all("rows => Math.max(0, ...rows.map(row => row.getBoundingClientRect().height))")
                assert row_height <= 120, f"Experiment rows are too tall at {width}px: {row_height}px"
            for selector in ("#project-name", "#mode-status", "#experiment-counts"):
                assert page.locator(selector).evaluate("element => element.scrollWidth <= element.clientWidth + 1"), f"Text overflows: {selector} at {width}px"
            assert page.locator(".experiment-node circle").first.is_visible()
            for image in page.locator("img").all():
                assert image.evaluate("element => element.complete && element.naturalWidth > 0"), "Missing icon asset"
            page.screenshot(path=str(screenshot.with_name(f"{screenshot.stem}-{width}.png")), full_page=True)
            page.locator("#summary-open").click()
            page.wait_for_selector("#summary-dialog[open] #summary-content h2")
            assert page.locator("#summary-dialog").evaluate("element => element.scrollWidth <= element.clientWidth + 1"), "Report dialog overflows"
            page.screenshot(path=str(screenshot.with_name(f"{screenshot.stem}-summary-{width}.png")), full_page=True)
            page.locator("#summary-close").click()

        longest_node = max((node for name, node in state["graph"]["nodes"].items() if name != "root"), key=lambda node: len(node.get("hypothesis") or ""))
        page.locator("#experiment-search").fill(longest_node["id"])
        page.locator("#experiment-ledger tr").first.click()
        page.wait_for_selector("#detail-dialog[open]")
        page.locator("#detail-body").get_by_role("heading", name="Hypothesis", exact=True).wait_for()
        assert longest_node["hypothesis"] in page.locator("#detail-body").inner_text()
        page.keyboard.press("Escape")
        page.locator("#experiment-search").fill("")

        page.route("**/api/report", lambda route: route.fulfill(json={"markdown": "# Safety test\n<img src=x onerror=window.__oloXss=1><script>window.__oloXss=1</script>"}))
        page.locator("#summary-open").click()
        page.wait_for_selector("#summary-content h1")
        assert not page.evaluate("Boolean(window.__oloXss)")
        assert page.locator("#summary-content script, #summary-content [onerror]").count() == 0
        browser.close()

    if console_errors:
        raise RuntimeError("browser console errors: " + " | ".join(console_errors))
    print(
        json.dumps(
            {
                "ok": True,
                "url": args.url,
                "screenshot": str(Path(args.screenshot).resolve()),
            }
        )
    )


if __name__ == "__main__":
    main()
