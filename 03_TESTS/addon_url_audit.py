"""HKS addon URL audit - run BEFORE every release (Ammar round 66).
Checks every ADDON_*.bat against the LIVE upstream:
  GitHub-asset bats: does /releases/latest have an asset matching the bat regex?
  Direct-URL bats:   does the URL answer HTTP 200/30x?
Exit code 1 if any bat is distressed. Network required - never part of the
offline build gates by design (builds must work without internet).
Run: python addon_url_audit.py
"""
import glob
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request

BAD = ("NO-MATCH", "BAD-URL", "NO-REGEX")


def _github_latest_assets(repo):
    """Fallback for GitHub's unauthenticated API rate limit: inspect the
    official latest-release page, following its normal redirect to the tag."""
    import urllib.parse
    u = f"https://github.com/{repo}/releases/latest"
    rq = urllib.request.Request(u, headers={"User-Agent": "hks-addon-audit"})
    page = urllib.request.urlopen(rq, timeout=25).read().decode("utf-8", "replace")
    # GitHub renders large asset lists in an include-fragment request rather
    # than in the initial release HTML.
    frag = re.search(r'src=["\']([^"\']*/releases/expanded_assets/[^"\']+)', page)
    if frag:
        fu = urllib.parse.urljoin(rq.full_url, html.unescape(frag.group(1)))
        frq = urllib.request.Request(fu, headers={"User-Agent": "hks-addon-audit"})
        page = urllib.request.urlopen(frq, timeout=25).read().decode("utf-8", "replace")
    urls = re.findall(r"href=[\"']([^\"']*/releases/download/[^\"']+)[\"']", page)
    assets = []
    for link in urls:
        path = urllib.parse.urlsplit(html.unescape(link)).path
        name = path.rsplit("/", 1)[-1]
        if name and name not in assets:
            assets.append(name)
    return assets


def _probe_url(url):
    """Use a one-byte ranged GET because many release hosts reject HEAD."""
    rq = urllib.request.Request(url, headers={"User-Agent": "hks-addon-audit",
                                               "Range": "bytes=0-0"})
    with urllib.request.urlopen(rq, timeout=25) as r:
        r.read(1)
        return str(r.status)


def main():
    results = []
    here = os.path.abspath(os.path.dirname(__file__))
    candidates = (os.path.join(here, "..", "04_SOURCE", "HKS_ADDONS"),
                  os.path.join(here, "..", "..", "HKS_ADDONS"))
    addon_dir = next((os.path.abspath(p) for p in candidates if os.path.isdir(p)), "")
    paths = sorted(glob.glob(os.path.join(addon_dir, "ADDON_*.bat"))) if addon_dir else []
    if not paths:
        print(f"ERROR: no addon scripts found at {addon_dir}")
        return 2
    for p in paths:
        s = open(p, encoding="utf-8", errors="replace").read()
        name = p.replace("\\", "/").split("/")[-1]
        m = re.search(r"api\.github\.com/repos/([\w.-]+/[\w.-]+)/releases/latest", s)
        rx = re.search(r"assets\)\|Where-Object\{\$_.name -match '([^']+)'\}", s)
        directs = [u for u in re.findall(r"Invoke-WebRequest\s+(?:-UseBasicParsing\s+)?'([^']+)'", s)
                   if not u.startswith("https://api.github.com")]
        if m and rx:
            try:
                rq = urllib.request.Request(
                    f"https://api.github.com/repos/{m.group(1)}/releases/latest",
                    headers={"User-Agent": "hks-addon-audit"})
                assets = [a["name"] for a in json.loads(urllib.request.urlopen(rq, timeout=25).read()).get("assets") or []]
                hits = [a for a in assets if re.search(rx.group(1), a, re.I)]
                results.append((name, "OK" if hits else "NO-MATCH",
                                hits[0] if hits else str(assets[:4])))
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    try:
                        assets = _github_latest_assets(m.group(1))
                        hits = [a for a in assets if re.search(rx.group(1), a, re.I)]
                        results.append((name, "OK" if hits else "NO-MATCH",
                                        ("official release page: " + hits[0]) if hits else
                                        "official release page had no matching asset"))
                    except Exception as fallback:
                        results.append((name, "UNVERIFIED", f"GitHub rate-limit; page fallback: {type(fallback).__name__}"))
                else:
                    results.append((name, "UNVERIFIED", f"GitHub HTTP {e.code}"))
            except Exception as e:
                results.append((name, "UNVERIFIED", type(e).__name__ + ": " + str(e)[:40]))
        elif directs:
            # cmd escaping: %% in a bat is a literal % at runtime (round 66)
            directs = [u.replace("%%", "%") for u in directs]
            st = []
            for u in directs:
                try:
                    st.append(_probe_url(u))
                except urllib.error.HTTPError as e:
                    st.append(f"HTTP{e.code}")
                except Exception as e:
                    st.append(type(e).__name__ + ":" + str(e)[:30])
            good = all(x in ("200", "206", "302", "303") for x in st)
            temporary = any(x.startswith(("URLError:", "TimeoutError:", "ConnectionError:",
                                          "SSLError:", "HTTP403")) for x in st)
            state = "OK" if good else ("UNVERIFIED" if temporary else "BAD-URL")
            results.append((name, state, str(st)))
        else:
            results.append((name, "MANUAL", "winget/docker/clone - self-resolving"))
    for n, st, info in results:
        print(f"{'!! ' if st in BAD else '   '}{n}: {st} {info[:70]}")
    bad = [r for r in results if r[1] in BAD]
    unverified = [r for r in results if r[1] == "UNVERIFIED"]
    print(f"\n{len(results)} checked, {len(bad)} distressed, {len(unverified)} unverified")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
