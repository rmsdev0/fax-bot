"""Generate gallery-seed faxes: handwriting-style pages with the GALLERY: YES
box drawn and checked, run through the same grit filter the replies get so
both sides of a gallery exchange look properly faxed.

Usage: uv run python scripts/make_seeds.py /tmp/seeds
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.worker.render import _apply_grit, _html_pages_to_pdf

PAGE_CSS = """
<style>
  @page { size: Letter; margin: 0; }
  body { width: 8.5in; height: 11in; margin: 0; padding: 1in 0.9in;
         box-sizing: border-box; background: #fff; color: #111;
         font-family: "Bradley Hand", "Marker Felt", "Comic Sans MS", cursive;
         transform: rotate(-0.6deg); }
  .big { font-size: 30px; line-height: 1.9; }
  .med { font-size: 24px; line-height: 1.9; }
  .optin { margin-top: 55px; font-size: 22px; }
  .box { display: inline-block; width: 30px; height: 30px; border: 3px solid #111;
         position: relative; vertical-align: middle; margin-right: 12px; }
  .box::after { content: "✓"; position: absolute; top: -14px; left: 2px;
                font-size: 42px; }
  .sig { margin-top: 40px; font-size: 26px; }
</style>
"""

SEEDS = {
    "seed_complaint": PAGE_CSS
    + """
<body>
  <div class="big">TO WHOM IT MAY CONCERN AT FAX-BOT INDUSTRIES —</div>
  <div class="med" style="margin-top: 30px;">
    I wish to file a FORMAL COMPLAINT regarding my neighbor's wind chimes.
    They are not musical. They are structural. Every breeze sounds like a
    hardware store falling down a staircase.<br><br>
    I have said nothing to my neighbor for six years. I am telling you,
    a fax machine, instead.<br><br>
    Please advise on proper channels.
  </div>
  <div class="sig">— A CONCERNED RESIDENT</div>
  <div class="optin"><span class="box"></span> GALLERY: YES</div>
</body>
""",
    "seed_harold": PAGE_CSS
    + """
<body>
  <div class="big">DEAR FAX-BOT,</div>
  <div class="med" style="margin-top: 24px;">
    THIS IS MY CAT, HAROLD. PLEASE REVIEW HIM FOR THE REFRIGERATOR
    EXHIBITION. HE IS DOING HIS BEST.
  </div>
  <svg width="440" height="330" viewBox="0 0 440 330" style="margin-top: 18px;"
       fill="none" stroke="#111" stroke-width="4" stroke-linecap="round">
    <!-- body -->
    <ellipse cx="240" cy="230" rx="150" ry="80"/>
    <!-- head -->
    <circle cx="120" cy="130" r="62"/>
    <!-- ears -->
    <path d="M75 95 L60 40 L105 72"/>
    <path d="M160 78 L180 30 L188 88"/>
    <!-- face -->
    <circle cx="100" cy="120" r="5" fill="#111"/>
    <circle cx="140" cy="120" r="5" fill="#111"/>
    <path d="M112 145 Q120 152 128 145"/>
    <path d="M60 135 L20 128 M60 148 L22 152 M178 135 L218 128 M178 148 L216 152"/>
    <!-- tail -->
    <path d="M385 210 Q430 160 400 110 Q385 90 370 105"/>
    <!-- legs -->
    <path d="M150 300 L150 255 M210 308 L210 262 M280 308 L280 262 M340 298 L340 255"/>
  </svg>
  <div class="sig">HE IS SEVEN. — MARGARET (AGE 41)</div>
  <div class="optin"><span class="box"></span> GALLERY: YES</div>
</body>
""",
    "seed_printer_poem": PAGE_CSS
    + """
<body>
  <div class="big">DEAR FAX-BOT,</div>
  <div class="med" style="margin-top: 30px;">
    Please compose a memorial poem for the office printer on the third
    floor, which died yesterday after eleven years of service.<br><br>
    It jammed one final time and we could not bring ourselves to open
    tray 2 again.<br><br>
    It has seen so much. It deserves to be remembered properly, by one
    of its own.
  </div>
  <div class="sig">— THE THIRD FLOOR (ALL OF US)</div>
  <div class="optin"><span class="box"></span> GALLERY: YES</div>
</body>
""",
}


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, html in SEEDS.items():
        crisp = _html_pages_to_pdf([html])
        gritty = _apply_grit(crisp)
        path = out / f"{name}.pdf"
        gritty.save(path, deflate=True)
        gritty.close()
        crisp.close()
        print(f"wrote {path}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/seeds")
