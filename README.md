# InvoiceSnap — Official Landing Page

[![Search Indexation & Health Watch](https://github.com/Nikita-Guzenko/invoicesnap-landing/actions/workflows/index-watch.yml/badge.svg)](https://github.com/Nikita-Guzenko/invoicesnap-landing/actions/workflows/index-watch.yml)
[![Website](https://img.shields.io/badge/Website-invoicesnap.work-blue?style=flat&logo=safari)](https://invoicesnap.work/)
[![App Store](https://img.shields.io/badge/App_Store-5.0_★-black?style=flat&logo=apple)](https://apps.apple.com/app/id6759242022)

Production repository for the official marketing and conversion landing page of **InvoiceSnap** — AI-powered invoicing and estimate maker for contractors.

- **Live URL:** [https://invoicesnap.work/](https://invoicesnap.work/)
- **Hosting:** GitHub Pages + Fastly Edge CDN + Custom Domain DNS (Namecheap)
- **SSL:** Let's Encrypt automated TLS certificate
- **Analytics:** Google Analytics 4 (`G-X4TCVJMVEP`)
- **Automated Watcher:** `.github/workflows/index-watch.yml` crawls production daily at 12:00 UTC with `python3 check_site.py --base https://invoicesnap.work`. It fails on broken pages, sitemaps, robots rules or page gates. The summary records the crawler outcome and output; indexing stays unknown without Search Console evidence. Actions cannot start during the account billing lock. Gate 9 needs the repository secret `SITE_WATCH_APP_TOKEN` with read-only access to `Nikita-Guzenko/invoicesnap-expo`; the workflow checks out the evidence revision from `data/claims.json`. Missing access fails setup and reports unknown site health.

## Features
- **Apple-Aesthetic Design:** Light ambient gradients, SF Pro / Plus Jakarta typography, dark titanium iPhone mockup.
- **Interactive Estimate Calculator:** Real-time trade presets (Bathroom Re-Tile, 200A Electrical Panel, Copper Pipe Repair) with working "Preview PDF Invoice" modal.
- **Multi-Currency & Regional Tax:** Dynamic recalculation for USD ($), CAD ($), GBP (£), EUR (€), and AUD ($).
- **Competitor Comparison Table (`#compare`):** Side-by-side comparison against Joist ($180–$348/yr) and Jobber ($588–$1,188/yr).
- **Semantic Trade Pillars (`#trades`):** Targeted keyword clusters for Plumbing, Electrical, HVAC, Remodeling, Landscaping, and Handyman trades.
- **Rich Schema.org (JSON-LD):** `@type: SoftwareApplication` and `@type: FAQPage` structured data.
- **Founder Lifetime Deal:** $39 one-time license with `#redeem` voucher activation portal for AppSumo / Lifetime Deal campaigns.

## Add a page or deploy
The site is generated from `data/` by `build.py` (no dependencies). `python3 build.py --help` says how to add a page.
Free tool pages (form, live paper, PDF, Word, Excel): one JSON per page in `data/tool-pages/`, sources in `data/sources.json`, country facts in `data/law/`. `python3 verify_sources.py` opens each source; `gate.py` and `gate_browser.py` hold the go-live gate. Tests: `python3 tests/test_tool_gate.py`.
Deploy with `python3 publish.py`: it runs the checks, pushes `dist/` to branch `gh-pages`, crawls production and tells IndexNow. A push to `main` does not deploy.

## Product artwork

All site product marks and browser icons use `src/static/assets/invoicesnap-icon.png`, the unchanged artwork from the [official App Store listing](https://apps.apple.com/us/app/invoice-snap-estimate-maker/id6759242022), verified October 1, 2026.
SHA-256: `c84afeaf6c3ef9f53a1afc4ff6966f1d4fb135c8a65c7627a72a71c29971de6d`.
