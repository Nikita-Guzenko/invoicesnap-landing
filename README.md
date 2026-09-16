# InvoiceSnap — Official Landing Page

[![Search Indexation & Health Watch](https://github.com/Nikita-Guzenko/invoicesnap-landing/actions/workflows/index-watch.yml/badge.svg)](https://github.com/Nikita-Guzenko/invoicesnap-landing/actions/workflows/index-watch.yml)
[![Website](https://img.shields.io/badge/Website-invoicesnap.work-blue?style=flat&logo=safari)](https://invoicesnap.work/)
[![App Store](https://img.shields.io/badge/App_Store-5.0_★-black?style=flat&logo=apple)](https://apps.apple.com/app/id6759242022)

Production repository for the official marketing and conversion landing page of **InvoiceSnap** — AI-powered invoicing and estimate maker for contractors.

- **Live URL:** [https://invoicesnap.work/](https://invoicesnap.work/)
- **Hosting:** GitHub Pages + Fastly Edge CDN + Custom Domain DNS (Namecheap)
- **SSL:** Let's Encrypt automated TLS certificate
- **Analytics:** Google Analytics 4 (`G-X4TCVJMVEP`)
- **Automated Watcher:** Runs daily via GitHub Actions (`.github/workflows/index-watch.yml`) to ping Google and Bing search crawlers and monitor live indexing.

## Features
- **Apple-Aesthetic Design:** Light ambient gradients, SF Pro / Plus Jakarta typography, dark titanium iPhone mockup.
- **Interactive Estimate Calculator:** Real-time trade presets (Bathroom Re-Tile, 200A Electrical Panel, Copper Pipe Repair) with working "Preview PDF Invoice" modal.
- **Multi-Currency & Regional Tax:** Dynamic recalculation for USD ($), CAD ($), GBP (£), EUR (€), and AUD ($).
- **Competitor Comparison Table (`#compare`):** Side-by-side comparison against Joist ($180–$348/yr) and Jobber ($588–$1,188/yr).
- **Semantic Trade Pillars (`#trades`):** Targeted keyword clusters for Plumbing, Electrical, HVAC, Remodeling, Landscaping, and Handyman trades.
- **Rich Schema.org (JSON-LD):** `@type: SoftwareApplication` and `@type: FAQPage` structured data.
- **Founder Lifetime Deal:** $39 one-time license with `#redeem` voucher activation portal for AppSumo / Lifetime Deal campaigns.

## Deploying Updates
Every push to `main` automatically deploys live to `https://invoicesnap.work/` in ~30 seconds via GitHub Pages.
