#!/usr/bin/env python3
"""
Build a self-contained snapshot of the dashboard for publishing as an Artifact.

The live board fetches data.json/backtest.json and loads fonts from ./fonts/.
An Artifact is served under a strict CSP with no sibling files, so this inlines
all three: fonts as @font-face data URIs, data as JS literals.
"""
import base64
import json
import os
import re

SRC = "docs/index.html"
OUT = "/tmp/claude-0/-home-user-reddit-stock-radar/23a844aa-8564-517a-bbb7-5903567581ed/scratchpad/radar-artifact.html"
F = "docs/fonts/"


def b64(path):
    return base64.b64encode(open(path, "rb").read()).decode()


def font_css():
    # Google serves one variable file for all four Sans weights — declare it once
    # with a weight range instead of embedding the same payload four times.
    sans = b64(F + "IBMPlexSans-400.woff2")
    css = [f"@font-face{{font-family:'IBM Plex Sans';font-style:normal;"
           f"font-weight:400 700;font-display:swap;"
           f"src:url(data:font/woff2;base64,{sans}) format('woff2');}}"]
    for w in (400, 500, 600):          # Mono ships a distinct file per weight
        css.append(f"@font-face{{font-family:'IBM Plex Mono';font-style:normal;"
                   f"font-weight:{w};font-display:swap;"
                   f"src:url(data:font/woff2;base64,{b64(F + f'IBMPlexMono-{w}.woff2')})"
                   f" format('woff2');}}")
    return "\n".join(css)


def main():
    html = open(SRC).read()
    data = json.load(open("docs/data.json"))
    back = json.load(open("docs/backtest.json"))

    # swap the stylesheet link for inlined faces
    html = html.replace('<link rel="stylesheet" href="./fonts/plex.css">',
                        f"<style>\n{font_css()}\n</style>")

    # replace both fetches with the data itself
    fetch_block = re.search(
        r'// backtest\.json is optional.*?\n\s*\}\);\s*\n(?=</script>)', html, re.S)
    if not fetch_block:
        raise SystemExit("could not locate the fetch block — did index.html change?")
    boot = (
        "const D = " + json.dumps(data, separators=(",", ":")) + ";\n"
        "const B = " + json.dumps(back, separators=(",", ":")) + ";\n"
        "(function(){\n"
        "  const when = new Date(D.generated_at);\n"
        "  document.getElementById('stamp').textContent = when.toLocaleString(undefined,\n"
        "    {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) + ' · snapshot';\n"
        "  document.getElementById('hStamp').textContent = when.toLocaleTimeString(undefined,\n"
        "    {hour:'2-digit',minute:'2-digit'});\n"
        "  document.getElementById('hTracked').textContent =\n"
        "    (D.session && D.session.tracked) ?? (D.buzz||[]).length;\n"
        "  render();\n"
        "})();\n"
    )
    html = html[:fetch_block.start()] + boot + html[fetch_block.end():]

    # D and B are now consts, so drop the original `let` declaration
    html = html.replace("let D = null, B = null;\n", "")

    # strip the document wrapper — an Artifact supplies its own
    title = re.search(r"<title>.*?</title>", html, re.S).group(0)
    style = "\n".join(re.findall(r"<style>.*?</style>", html, re.S))
    body = re.search(r"<body>(.*)</body>", html, re.S).group(1)
    out = f"{title}\n{style}\n{body.strip()}\n"

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w").write(out)
    print(f"wrote {OUT}  ({len(out)/1024:.0f} KB)")
    for bad in ("<!doctype", "<html", "<head>", "</body>", "fetch("):
        if bad in out.lower():
            print(f"  [warn] output still contains {bad!r}")


if __name__ == "__main__":
    main()
