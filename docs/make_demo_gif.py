"""Record docs/images/demo.gif - a ~30 second demo: search -> fill -> you review.

Like the screenshots, it runs the real tool on the demo pages from the tests with a
demo applicant (John Doe) and fictional companies. Needs ffmpeg on PATH.

    python docs/make_demo_gif.py
"""
from __future__ import annotations

import asyncio
import html
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests"), str(ROOT / "docs")]

from playwright.async_api import async_playwright  # noqa: E402

import make_screenshots as shots  # noqa: E402
import test_forms as form_mock  # noqa: E402
from wdautofill.answer_bank import AnswerBank  # noqa: E402
from wdautofill.forms import FormFiller  # noqa: E402
from wdautofill.jobs import Job  # noqa: E402
from wdautofill.prompter import Reply, TerminalPrompter  # noqa: E402
from wdautofill.resolver import Resolver  # noqa: E402
from wdautofill.workday import STATUS_PAGE  # noqa: E402

W, H = 1000, 620
OUT = ROOT / "docs" / "images" / "demo.gif"

CAPTION_CSS = """
#cap { position: fixed; top: 0; left: 0; right: 0; z-index: 99999; height: 44px; display: flex;
  align-items: center; gap: 12px; padding: 0 18px; background: #1f6feb; color: #fff;
  font: 600 18px 'Segoe UI', Roboto, sans-serif; box-shadow: 0 2px 8px #0005; }
#cap b { background: #fff; color: #1f6feb; border-radius: 50%; width: 28px; height: 28px;
  display: inline-flex; align-items: center; justify-content: center; }
body { padding-top: 44px !important; }
"""


def caption_html(step: int, text: str) -> str:
    return f'<div id="cap"><b>{step}</b>{html.escape(text)}</div>'


def animated_terminal(title: str, text: str, step: int, caption: str) -> str:
    """Terminal page that types the command, then prints the real output line by line."""
    lines = text.splitlines()
    command, rest = (lines[0][2:], lines[1:]) if lines and lines[0].startswith("$ ") else ("", lines)
    rendered = [shots.terminal_html("", ln).split("<pre>")[1].split("</pre>")[0] for ln in rest]
    return f"""<html><head><style>{shots.TERMINAL_CSS}{CAPTION_CSS}
      .win {{ width: {W - 40}px; height: {H - 90}px; }} pre {{ height: {H - 140}px; overflow: hidden; }}
    </style></head><body>{caption_html(step, caption)}
    <div class="win"><div class="bar"><i style="background:#ff5f56"></i><i style="background:#ffbd2e"></i>
    <i style="background:#27c93f"></i>{html.escape(title)}</div><pre id="t"></pre></div>
    <script>
      const cmd = {json.dumps(command)}, lines = {json.dumps(rendered)};
      const t = document.getElementById('t');
      let out = '<span class="cmd">$ </span>', i = 0;
      function typeCmd() {{
        if (i < cmd.length) {{ out += cmd[i++]; t.innerHTML = out + '█'; setTimeout(typeCmd, 45); }}
        else {{ out += '\\n'; setTimeout(() => printLine(0), 350); }}
      }}
      function printLine(n) {{
        if (n >= lines.length) {{ t.innerHTML = out; window.done = true; return; }}
        out += lines[n] + '\\n'; t.innerHTML = out; t.scrollTop = t.scrollHeight;
        setTimeout(() => printLine(n + 1), lines[n].trim() ? 110 : 40);
      }}
      typeCmd();
    </script></body></html>"""


async def record(browser, tmp: Path, name: str, scene) -> Path:
    ctx = await browser.new_context(viewport={"width": W, "height": H},
                                    record_video_dir=str(tmp / name), record_video_size={"width": W, "height": H})
    page = await ctx.new_page()
    await scene(page)
    await ctx.close()
    return next((tmp / name).glob("*.webm"))


async def main() -> None:
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg is needed to make the GIF")
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        search_text = (await shots.search_run(tmp)).replace("Start filling them now? [Y/n] n", "")
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()

            # 1. search
            async def scene_search(page):
                await page.set_content(animated_terminal("wdautofill search", search_text, 1,
                                                         "Find jobs that match your skills"))
                await page.wait_for_function("window.done === true", timeout=60000)
                await page.wait_for_timeout(1800)

            # 2. the real form filler on the Lever-shaped demo page, slowed down to watch
            async def scene_fill(page):
                (tmp / "lever.html").write_text(form_mock.LEVER_HTML, encoding="utf-8")
                resume = tmp / "John_Doe_CV.pdf"
                resume.write_bytes(b"%PDF-1.4 demo")
                prompter = TerminalPrompter()
                prompter._blocking_ask = lambda label, *a: Reply("Yes" if "internet" in label else None)
                resolver = Resolver(shots.DEMO, AnswerBank(tmp / "a.yaml"), prompter=prompter)
                await page.add_init_script(f"""document.addEventListener('DOMContentLoaded', () => {{
                    const s = document.createElement('style'); s.textContent = {json.dumps(shots.FORM_CSS + CAPTION_CSS)};
                    document.head.appendChild(s);
                    document.body.insertAdjacentHTML('afterbegin', {json.dumps(caption_html(2, "It fills every application from your profile and rules"))});
                }});""")
                slow = page.locator  # every action below is slowed by the context's slow_mo
                filler = FormFiller(page, resolver, Job((tmp / "lever.html").as_uri(), "IN"), resume_path=resume,
                                    log_path=tmp / "log.jsonl")
                filler.say = lambda msg: None
                await filler.run()
                await page.evaluate("window.scrollTo({top: document.body.scrollHeight, behavior: 'smooth'})")
                await page.wait_for_timeout(1500)
                del slow

            # 3. you review: status window + the rule that matters
            async def scene_review(page):
                rows = [("Acme Payments: Senior SDET", "ready_for_review - waiting for you to submit"),
                        ("Globex: QA Automation Lead", "ready_for_review - waiting for you to submit"),
                        ("Lever: Senior Quality Engineer", "ready_for_review - waiting for you to submit"),
                        ("Initech: Test Automation Engineer", "filling")]
                table = "".join(f"<tr><td>{html.escape(a)}</td><td><b>{html.escape(b)}</b></td></tr>" for a, b in rows)
                body = STATUS_PAGE.format(rows=table).replace(
                    "<body ", f"<body ").replace("<h2>", caption_html(3, "You review and click Submit - always you") + "<h2>")
                await page.set_content(body.replace("</head>", f"<style>{CAPTION_CSS} body{{font-size:18px}}</style></head>"))
                await page.wait_for_timeout(2500)
                await page.set_content(f"""<html><head><style>{CAPTION_CSS}
                  body {{ margin:0; height:100vh; display:flex; align-items:center; justify-content:center;
                         background:linear-gradient(135deg,#0d1117,#1f2a44); color:#fff; font-family:'Segoe UI',Roboto,sans-serif; }}
                  </style></head><body><div style="text-align:center">
                  <div style="font-size:46px;font-weight:800">workday-autofill</div>
                  <div style="font-size:24px;margin-top:14px;color:#c9d1d9">Answer each question once. You click Submit.</div>
                  <div style="font-size:20px;margin-top:26px;color:#79c0ff">pip install workday-autofill</div>
                  </div></body></html>""")
                await page.wait_for_timeout(2500)

            v1 = await record(browser, tmp, "s1", scene_search)
            await browser.close()
            browser = await pw.chromium.launch(slow_mo=220)
            v2 = await record(browser, tmp, "s2", scene_fill)
            await browser.close()
            browser = await pw.chromium.launch()
            v3 = await record(browser, tmp, "s3", scene_review)
            await browser.close()

        # stitch + convert: 10 fps, 800 px wide, shared palette
        listing = tmp / "list.txt"
        listing.write_text("".join(f"file '{v}'\n" for v in (v1, v2, v3)))
        joined = tmp / "joined.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
                        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(joined)], check=True)
        palette = tmp / "palette.png"
        vf = "fps=10,scale=800:-1:flags=lanczos"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(joined), "-vf", f"{vf},palettegen=stats_mode=diff",
                        str(palette)], check=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(joined), "-i", str(palette), "-lavfi",
                        f"{vf}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle",
                        str(OUT)], check=True)
        dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(joined)],
                             capture_output=True, text=True).stdout.strip()
        print(f"wrote {OUT.relative_to(ROOT)}  ({OUT.stat().st_size / 1e6:.1f} MB, {float(dur):.0f} s)")


if __name__ == "__main__":
    asyncio.run(main())
