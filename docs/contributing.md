# Contributing and maintaining the website

Start with the [README](../README.md) and [operating guide](guide.md).
Keep changes focused: controller behavior lives in `src\olo`, Copilot workflows
in `.github`, and the public landing page in `site`.

## Local development

Python 3.10+ and Git are sufficient for the controller tests:

```powershell
python -m unittest discover -s tests -v
```

Tests create isolated temporary repositories. Do not run experimental research
against the project's core just to validate a documentation or website edit.
Never commit `.olo`, `.olo-history`, credentials, generated build output, or
personal machine paths.

For core changes, add focused regression coverage and preserve the benchmark
contract. For documentation changes, verify commands against CLI help and keep
the README concise; detailed behavior belongs in `guide.md`.

Existing dashboard browser checks are separate from controller tests:

```powershell
python -m pip install playwright
python -m playwright install chromium
python tests\dashboard_browser_check.py --url http://127.0.0.1:8765 --screenshot .olo\dashboard-smoke.png
```

Start an initialized local dashboard first and replace the example URL with
the one it prints. Playwright is a development-only dependency.

Dashboard vendor assets are already bundled. Only when updating the renderer
or icons, run:

```powershell
npm ci --prefix src\olo\web\vendor
npm run sync --prefix src\olo\web\vendor
```

Keep the bundled third-party license notices.

## Preview the landing page

The site is plain HTML, CSS, JavaScript, and an original SVG favicon.
No bundler, package install, API, account, or Olo state is needed.

```powershell
python -m http.server 8000 --bind 127.0.0.1 --directory site
```

Open `http://127.0.0.1:8000`. Stop the foreground server with Ctrl+C.
The typography uses the dashboard's local system-font stacks: Bahnschrift for
display, Segoe UI for body text, and Cascadia Mono for data, with platform
fallbacks. No font downloads, analytics, or live experiment-data requests occur.

The experiment graph is an **illustration**, not a live dashboard or claimed
benchmark result. Its eight invented experiments show parallel branches,
follow-up rounds, a retained specialist, failed gates, and a remeasured
combination with an explicit base and donor. Solid stepped paths show parent
lineage; the dashed path records the donor contribution. Selecting a node
highlights its source lineage. Keep the illustrative label visible and never
copy private local run data into this page.
The tree controls and copy buttons are progressive enhancements; the workflow
and setup instructions remain readable without JavaScript.

Run the landing-page browser check against a running preview:

```powershell
python -m pip install playwright
python -m playwright install chromium
python tests\site_browser_check.py --url http://127.0.0.1:8000 --screenshots .olo\site-check
```

It checks desktop/mobile layout, keyboard navigation, the interactive
experiment tree, copy feedback, reduced motion, and no-JavaScript content.

## GitHub Pages

Public URL: **https://abpatramsft.github.io/olo-autoresearch/**

`.github\workflows\pages.yml` deploys **only `site`** with GitHub's official
Pages actions. It does not upload the repository root, Python dashboard,
local evidence, or generated experiment results.

Repository settings:

1. Open **Settings > Pages**.
2. Set **Build and deployment > Source** to **GitHub Actions**.
3. Push website changes to `main`, or run **Deploy landing page** from Actions.
4. Inspect the deployment job and the `github-pages` environment for the URL.

The workflow uses read access to repository contents plus Pages write and
OIDC permissions for the deployment job. Concurrent deployments share one
concurrency group. Relative asset paths support the project URL prefix
`/olo-autoresearch/`; do not change them to root-relative `/assets/...` paths.

For a fork, enable Pages on that repository and update the canonical URL,
social metadata, repository/documentation links, and README website link.

## Presentation checklist

- Keep attribution to [Evo](https://github.com/evo-hq/evo) visible and accurate.
- Do not copy third-party website artwork, copy, or brand assets.
- Do not imply GitHub sponsorship or automatic production reliability.
- Keep focus visible, controls keyboard-operable, and reduced motion respected.
- Test at narrow mobile widths and with JavaScript disabled.
- Confirm the deployed site, not only the local preview.
- Review the staged diff so experiment evidence and unrelated files stay out.
