#!/usr/bin/env python3
"""Build Nikita's pick file for NIK-696 from the preview screenshots (19 Nov 2026).

Usage: python3 previews/nik-696/make_pick_file.py [--out ~/ui-picks/invoicesnap/2026-11-19-homepage-real.md]
Needs: previews/nik-696/shots/<option>-1440.png and -390.png (run shoot.py first) and Pillow.
Every image is embedded as a base64 JPEG data URI: the file must open with no network and no image folder (ui-lego rule).
Gotcha: full-page PNGs are 6-10k px tall and several MB. They are resized (desktop to 1000 px wide, phone to 390 px)
and saved as JPEG q=72 to keep the file small enough for the Markdown viewer. The script decodes each embedded image
again and stops if one fails.
"""
import argparse, base64, io, re
from pathlib import Path
from PIL import Image

D = Path(__file__).resolve().parent
OPTIONS = [  # (number Nikita knows from the 17 Nov shortlist, name, 21st id, one-line reason)
    (1, "Design-canvas hero (Figma style, your bookmark)", "45695",
     "Bold stacked headline with selection frames and live cursors; the most memorable look."),
    (2, "Split hero", "1831",
     "Message and buttons on the left, the real app screens on the right; the visitor sees the product at once."),
    (4, "Underline hero", "8501",
     "Calm, type-led page with one clear action; the closest to a classic trusted brand site."),
    (5, "Live product preview", "45141",
     "A large app window the visitor can click through; it explains InvoiceSnap before sign-up."),
]


def data_uri(png: Path, width: int) -> str:
    im = Image.open(png).convert("RGB")
    if im.width != width:
        im = im.resize((width, round(im.height * width / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=72, optimize=True, progressive=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(Path.home() / "ui-picks/invoicesnap/2026-11-19-homepage-real.md"))
    out = Path(ap.parse_args().out).expanduser()
    parts = [
        "# InvoiceSnap homepage: 4 real versions",
        "",
        "**Choice pending.** Tick one box, or reply with the number.",
        "",
        "Each version is the full invoicesnap.work homepage with our own text, app screens, logo and colors.",
        "Only the design changes. Nothing is live: the current site stays as it is until you choose.",
        "Each version shows the computer view (1440 px) first, then the phone view (390 px).",
        "",
        "For comparison, the current homepage is at the end of this file.",
        "",
    ]
    for n, name, src, why in OPTIONS:
        parts += [f"## {n}. {name}", "", f"- [ ] Select option {n}", "", why, "",
                  f"Source: 21st.dev component ID {src}, adapted to InvoiceSnap.", "",
                  "**Computer**", "", f"![Option {n} computer]({data_uri(D / f'shots/option-{n}-1440.png', 1000)})", "",
                  "**Phone**", "", f"![Option {n} phone]({data_uri(D / f'shots/option-{n}-390.png', 390)})", ""]
    parts += ["## Current homepage (for comparison, not an option)", "",
              f"![Current computer]({data_uri(D / 'shots/current-1440.png', 1000)})", "",
              f"![Current phone]({data_uri(D / 'shots/current-390.png', 390)})", ""]
    text = "\n".join(parts)
    uris = re.findall(r"data:image/jpeg;base64,([A-Za-z0-9+/=]+)", text)
    for u in uris:  # every embedded image must decode
        Image.open(io.BytesIO(base64.b64decode(u))).verify()
    assert "http" not in re.sub(r"data:image/jpeg;base64,[A-Za-z0-9+/=]+", "", text), "external link in pick file"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"{out} images={len(uris)} decoded_ok={len(uris)} boxes={text.count('- [ ] Select option')} bytes={len(text)}")


if __name__ == "__main__":
    main()
