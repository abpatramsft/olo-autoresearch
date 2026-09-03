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

        assert page.locator("#project-name").text_content() == "tiny-policy"
        assert page.locator("#best-score").inner_text() == "0.6000"
        assert page.locator(".experiment-node").count() == 1
        assert "exp_0000" in page.locator("#frontier-list").inner_text()

        page.locator("#experiment-ledger tr").first.click()
        page.wait_for_selector("#detail-dialog[open]")
        assert page.locator("#detail-title").text_content() == "exp_0000"
        assert "Measure the unchanged" in page.locator("#detail-body").inner_text()

        screenshot = Path(args.screenshot)
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot), full_page=True)
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
