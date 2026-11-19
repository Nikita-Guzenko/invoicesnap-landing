# NIK-696 — homepage direction previews (brief for each builder)

Goal: Nikita (the owner, not a coder) picks one homepage direction. He rejected raw 21st.dev demos. He must see the END PRODUCT:
the full invoicesnap.work homepage, with our real copy, real product screens, logo and colors, rebuilt in one direction.

## Repo facts
- Repo: /home/nikita/.cache/NIK-696-landing (branch nik-696-homepage-options). The live homepage source is
  `src/pages/index.html` (static HTML, ~80 KB of markup + 21 inline webp images). Read it fully first (strip the base64
  when you read: `python3 -c "import re;print(re.sub(r'base64,[A-Za-z0-9+/=]+','base64,…',open('src/pages/index.html').read()))"`).
- Site is plain static HTML/CSS/vanilla JS. No React, no npm, no CDN (production CSP is `'self'` only). Write vanilla code.
- Product screens (real App Store screenshots, 540x1170 webp, each already has a caption + phone frame):
  `previews/nik-696/assets/screens/*.webp` → reference them as `../assets/screens/<name>.webp`.
  Names: ai-estimate-drafts, your-cash-flow-at-a-glance, manage-invoices-easily, flexible-payment-splits, rebill-expense-markups,
  understand-your-cash-flow, manage-your-clients, speed-up-invoicing, track-billable-hours, ai-business-logos.
- Logo: `/assets/invoicesnap-icon.png` (absolute path; the preview server maps /assets/ to src/static/assets/).
- Brand tokens (from the live page): ink #0B1220, ink-secondary #475569, ink-muted #64748B, brand blue #2563EB,
  brand-hover #1D4ED8, brand-light #EFF6FF, green #10B981, canvas #F8FAFC, system font stack
  (-apple-system, "SF Pro Display", "Segoe UI", Roboto, …). Brand blue stays the primary action color.
- 21st.dev source code for each direction: `previews/nik-696/sources/src-<id>.txt` (React + Tailwind + framer-motion).

## What to build
One file: `previews/nik-696/option-<N>/index.html` (inline `<style>` and `<script>` are fine; no other new files unless
needed inside your own option folder).
1. Adapt the 21st component for your direction into the HERO + header: same layout, structure and visual signature;
   port its framer-motion animations to CSS/vanilla JS with the SAME durations, delays and easing curves
   (e.g. `cubic-bezier(0.16,1,0.3,1)`); replace the demo text and mock data with InvoiceSnap copy and real product screens.
   Honour `prefers-reduced-motion`.
2. Carry the direction through the WHOLE page: every section of the live page (app tour/showcase with the 10 screens,
   compare, trades, calculator, pricing, FAQ, footer) restyled so the page looks like one designed product.
   Keep the section order unless your direction clearly needs a change; keep all section content.
3. Copy: use the live page's text VERBATIM (headlines, body, bullets, prices, FAQ, disclaimers, comparison numbers).
   You may shorten or split a hero line to fit the layout, but never invent claims, numbers, reviews, ratings,
   testimonials, customer counts, logos of customers or features. Legal/disclaimer lines stay.
4. Keep the working parts working: showcase tab switching, the estimate calculator, FAQ open/close, nav anchors,
   App Store / Web App links (same hrefs as live).
5. Preview hygiene: add `<meta name="robots" content="noindex,nofollow">`; remove GA/gtag/PostHog/consent/analytics scripts
   and the JSON-LD; no external network requests at all.
6. Responsive: must look designed at 1440 px and at 390 px (phone). No horizontal scroll at 390 px. Tap targets ≥ 44 px.
7. Quality bar: this is shown to the owner as the final look. No lorem, no placeholder boxes, no broken images,
   no console errors, no AI-slop gradients-for-nothing. Contrast AA for text.

## Check your work (loop until good)
`cd /home/nikita/.cache/NIK-696-landing && python3 previews/nik-696/shoot.py option-<N>` → writes
`previews/nik-696/shots/option-<N>-1440.png` and `-390.png`, prints height, overflow, errors.
Open the PNGs (crop tall images into ~1800 px slices with PIL to look at them) and fix what looks wrong. Repeat.

## Do not
- Do not edit any file outside `previews/nik-696/option-<N>/` (other builders work in parallel on other options).
- Do not commit, push, deploy or run publish.py. Do not touch src/, data/, templates/.
