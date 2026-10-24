"""Build a single-file preview of the cockpit (demo mode) for sharing.

Usage: python tools/build_preview.py out.html
Fonts come from Google Fonts and three.js from cdnjs in the preview build.
"""
import pathlib, re, sys

root = pathlib.Path(__file__).resolve().parent.parent / "web"
html = (root / "index.html").read_text(encoding="utf-8")
css = (root / "css" / "astra.css").read_text(encoding="utf-8")
css = re.sub(r"@font-face\{[^}]*\}\n?", "", css)
fonts = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
         '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Instrument+Serif&family=JetBrains+Mono:wght@300..700&display=swap">')
head = re.search(r"<title>.*?</title>\s*<meta name=\"claude:viewport-fit-sides\"[^>]*>", html, re.S).group(0)
body = re.search(r"<body>(.*)</body>", html, re.S).group(1)
scripts = re.findall(r'<script src="([^"]+)"></script>', body)
body = re.sub(r'<script src="[^"]+"></script>\s*', "", body)
js = []
for src in scripts:
    if "three.min.js" in src:
        js.append('<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>')
    else:
        js.append("<script>\n" + (root / src).read_text(encoding="utf-8") + "\n</script>")
out = head + "\n" + fonts + "\n<style>\n" + css + "\n</style>\n" + body.strip() + "\n" + "\n".join(js) + "\n"
pathlib.Path(sys.argv[1]).write_text(out, encoding="utf-8")
print("wrote", sys.argv[1], len(out), "bytes")
