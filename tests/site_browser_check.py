from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the static Olo landing page.")
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--screenshots", type=Path)
    args = parser.parse_args()
    base_url = args.url.rstrip("/") + "/"
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)

    errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            permissions=["clipboard-read", "clipboard-write"],
        )
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "console",
            lambda message: errors.append(message.text) if message.type == "error" else None,
        )
        response = page.goto(base_url)
        assert response is not None and response.ok, "Landing page failed to load"
        page.wait_for_load_state("networkidle")
        assert page.title() == "Olo — Better code. Evidence first."
        assert page.locator("h1").count() == 1
        assert "Illustrative run, not benchmark results." in page.locator(".workbench-caption").inner_text()
        assert page.locator(".wordmark").all_text_contents() == ["OLO", "OLO"]
        assert page.locator(".brand-dot").count() == 0
        assert page.locator('link[href*="fonts.googleapis"]').count() == 0
        assert page.locator("[data-experiment]").count() == 8
        assert page.locator(".round-labels > span").all_text_contents() == [
            "BASELINE", "ROUND 1", "ROUND 2", "ROUND 3"
        ]

        for asset in ("styles.css", "app.js", "assets/olo.svg"):
            asset_response = page.request.get(urljoin(base_url, asset))
            assert asset_response.ok, f"Asset failed: {asset}"

        for anchor in page.locator('a[href^="#"]').all():
            fragment = anchor.get_attribute("href")
            assert fragment and page.locator(fragment).count() == 1, f"Missing anchor: {fragment}"
        for anchor in page.locator("a[href]").all():
            href = anchor.get_attribute("href")
            assert href and not href.startswith(("javascript:", "http://")), f"Invalid link: {href}"
            if href.startswith("https://"):
                assert urlparse(href).netloc == "github.com", f"Unexpected external destination: {href}"

        expected = {
            "baseline": ("exp_0000", "BASELINE"),
            "approved": ("exp_0001", "APPROVED"),
            "retained": ("exp_0002", "RETAINED"),
            "rejected": ("exp_0003", "NOT PROMOTED"),
            "refined": ("exp_0004", "APPROVED"),
            "donor": ("exp_0005", "APPROVED DONOR"),
            "regressed": ("exp_0006", "NOT PROMOTED"),
            "combined": ("exp_0007", "APPROVED RECOMBINATION"),
        }
        for name, (experiment_id, status) in expected.items():
            page.locator(f'[data-experiment="{name}"]').click()
            assert page.locator("#detail-id").inner_text() == experiment_id
            assert page.locator("#detail-status").inner_text() == status
            assert page.locator('[data-experiment][aria-pressed="true"]').count() == 1
            assert page.locator("#detail-checks > span").count() == 3

        assert page.locator("#detail-sources").inner_text() == "Base: exp_0004 · Donor: exp_0005"
        assert set(page.locator(".experiment-node.is-ancestor").evaluate_all(
            "nodes => nodes.map(node => node.dataset.experiment)"
        )) == {"baseline", "approved", "retained", "refined", "donor", "combined"}
        assert page.locator(".tree-link.is-related").count() == 6
        assert page.locator('.donor-line[data-from="donor"][data-to="combined"]').evaluate(
            "element => getComputedStyle(element).strokeDasharray !== 'none'"
        ), "Donor contribution is not visually distinguished"
        assert page.locator('.tree-link[data-from="rejected"], .tree-link[data-from="regressed"]').count() == 0

        page.locator('[data-experiment="baseline"]').focus()
        page.keyboard.press("Space")
        assert page.locator("#detail-id").inner_text() == "exp_0000", "Keyboard selection failed"
        assert page.locator(".tree-link.is-related").count() == 0
        assert page.locator("#detail-sources").inner_text() == "Source: unchanged repository"
        assert page.locator('[data-experiment="baseline"]').evaluate(
            "element => getComputedStyle(element).outlineStyle !== 'none'"
        ), "Keyboard focus is not visible"

        for source_id in ("clone-command", "explore-prompt"):
            page.locator(f'[data-copy="{source_id}"]').click()
            feedback = "Commands copied." if source_id == "clone-command" else "Prompt copied."
            page.get_by_role("status").filter(has_text=feedback).wait_for()
            copied = page.evaluate("navigator.clipboard.readText()").replace("\r\n", "\n")
            expected_text = page.locator(f"#{source_id}").inner_text().strip()
            assert copied == expected_text, f"Clipboard contents differ: {source_id}"
            assert "copied." in page.locator("#copy-status").inner_text()

        page.evaluate(
            """() => {
                Object.defineProperty(navigator.clipboard, "writeText", {
                    configurable: true,
                    value: () => Promise.reject(new Error("Permission denied for test"))
                });
            }"""
        )
        page.locator('[data-copy="explore-prompt"]').click()
        page.get_by_role("status").filter(has_text="copy it manually").wait_for()
        assert page.locator("#copy-status").get_attribute("class") == "copy-status is-error"

        page.reload()
        page.wait_for_load_state("networkidle")
        for width, height in ((1440, 1000), (1024, 900), (768, 1024), (390, 844), (320, 740)):
            page.set_viewport_size({"width": width, "height": height})
            assert page.evaluate(
                "document.documentElement.scrollWidth <= innerWidth"
            ), f"Horizontal overflow at {width}px"
            for selector in (".experiment-node", ".command-card", ".prompt-card", "h1"):
                assert page.locator(selector).evaluate_all(
                    "elements => elements.every(el => el.scrollWidth <= el.clientWidth + 1)"
                ), f"Clipped content in {selector} at {width}px"
            for node in page.locator("[data-experiment]").all():
                assert node.is_visible(), f"Missing tree control at {width}px"
            graph = page.locator(".graph-viewport")
            if width >= 1024:
                assert graph.evaluate("element => element.scrollWidth <= element.clientWidth"), "Desktop graph is clipped"
            if width <= 390:
                assert graph.evaluate("element => element.scrollWidth > element.clientWidth")
                page.locator('[data-experiment="combined"]').focus()
                page.keyboard.press("Enter")
                assert graph.evaluate("element => element.scrollLeft > 0"), "Keyboard focus did not reveal the final round"
                assert page.locator("#detail-id").inner_text() == "exp_0007"
                assert page.locator('[data-experiment="combined"]').evaluate(
                    """node => {
                        const viewport = node.closest('.graph-viewport').getBoundingClientRect();
                        const bounds = node.getBoundingClientRect();
                        return bounds.left >= viewport.left && bounds.right <= viewport.right;
                    }"""
                ), "Focused experiment is clipped on mobile"
                graph.evaluate("element => element.scrollLeft = 0")
            page.locator("summary").first.click()
            assert page.locator("details").first.get_attribute("open") is not None
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.locator("summary").first.click()
            if args.screenshots:
                page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
                page.screenshot(path=str(args.screenshots / f"landing-{width}.png"), full_page=True)
                page.locator(".workbench").screenshot(path=str(args.screenshots / f"workbench-{width}.png"))

        page.emulate_media(reduced_motion="reduce")
        assert page.evaluate(
            "getComputedStyle(document.documentElement).scrollBehavior"
        ) == "auto"
        assert page.locator(".button").first.evaluate(
            "element => getComputedStyle(element).transitionDuration"
        ) == "0s"

        no_script_context = browser.new_context(java_script_enabled=False)
        no_script_page = no_script_context.new_page()
        no_script_page.goto(base_url)
        assert no_script_page.locator("#hero-title").is_visible()
        assert no_script_page.locator("#clone-command").is_visible()
        assert no_script_page.locator("#explore-prompt").is_visible()
        assert no_script_page.locator('[data-experiment="combined"]').is_disabled()
        assert no_script_page.locator("#detail-sources").inner_text() == "Base: exp_0004 · Donor: exp_0005"
        assert not no_script_page.locator("[data-copy]").first.is_visible()
        no_script_page.keyboard.press("Tab")
        assert no_script_page.locator(".skip-link").evaluate(
            "element => element === document.activeElement"
        ), "Skip link is not the first keyboard stop"
        no_script_context.close()
        context.close()
        browser.close()

    assert not errors, "Browser errors: " + " | ".join(errors)
    print("Landing page passed: OLO branding, eight experiments, parent/donor lineage, links, keyboard, clipboard, five viewports, reduced motion, no JavaScript.")


if __name__ == "__main__":
    main()
