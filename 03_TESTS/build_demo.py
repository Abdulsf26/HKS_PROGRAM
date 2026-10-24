#!/usr/bin/env python3
"""Rebuild HKS_DEMO_OFFLINE.html from current source static assets.
Swaps the <style> block (style.css) and the app <script> block (app.js),
then bumps visible version strings. Usage: python3 build_demo.py 7.2.53
"""
import sys, re
VER = sys.argv[1] if len(sys.argv) > 1 else "7.2.53"
OLD = ".".join(VER.split(".")[:3 - 1] + [str(int(VER.split(".")[-1]) - 1)])  # previous patch
root = "/home/user"
demo_p = root + "/HKS_DEMO_OFFLINE.html"
css = open(root + "/04_SOURCE/HKS_AdminPanel/server/static/style.css").read()
js = open(root + "/04_SOURCE/HKS_AdminPanel/server/static/app.js").read()
h = open(demo_p).read()

# swap style block: first <style> ... first </style> after it
i = h.find("<style>"); assert i > 0, "no <style>"
j = h.find("</style>", i)
h = h[:i] + "<style>\n" + css + "\n</style>" + h[j + len("</style>"):]

# swap the app script block: the one containing a signature string from app.js
sig = "showSection"
start = 0
while True:
    s = h.find("<script>", start)
    assert s > 0, "no app script found"
    e = h.find("</script>", s)
    if sig in h[s:e] and len(h[s:e]) > 100000:
        h = h[:s] + "<script>\n" + js + "\n</script>" + h[e + len("</script>"):]
        break
    start = s + 1

# version strings: previous patch -> new (word-boundary safe on 7.2.NN)
h = re.sub(r"\b7\.2\.\d{2,}\b", VER, h)
h = h.replace(f'v={VER}', f'v={VER}')  # pins (if present) stay
open(demo_p, "w").write(h)
print("demo rebuilt:", VER, "bytes:", len(h))
