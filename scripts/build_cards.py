"""Build the profile's stat cards as SVGs from live data.

Two cards, each in a dark and a light version: a metrics-style dashboard and a
neofetch-style terminal. Sources: GitHub GraphQL, LeetCode, Chess.com and
TryHackMe. Every source is
optional: when one fails (rate limit, bot wall, outage) its last good data is
reused from data.json, so a bad run never blanks a card.

usage: python scripts/build_cards.py <out_dir> [--seed-thm thm.json]
env:   GITHUB_TOKEN  public data (contribution calendar, followers)
       STATS_TOKEN   optional token that can see private repos; without it the
                     repo stats, languages and commit hours keep their saved values
"""
import base64
import datetime as dt
import json
import math
import os
import sys
import urllib.request
from html import escape

USER = "alexeybe1kin"
UA = f"{USER}-profile-cards (+https://github.com/{USER})"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif"
# GitHub's own Primer colours, so the cards look like native GitHub boxes.
# Every card is rendered once per theme; the README picks one with <picture>.
THEMES = {
    "dark": {
        "C": {"bg": "#0d1117", "box": "#161b22", "title": "#e6edf3", "text": "#e6edf3", "muted": "#9198a1",
              "faint": "#6e7681", "track": "#21262d", "line": "#30363d", "grid": "#21262d",
              "green": "#3fb950", "accent": "#4493f8", "yellow": "#d29922", "red": "#f85149", "gray": "#6e7681"},
        "HEAT": ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"],
    },
    "light": {
        "C": {"bg": "#ffffff", "box": "#f6f8fa", "title": "#1f2328", "text": "#1f2328", "muted": "#59636e",
              "faint": "#818b98", "track": "#eaeef2", "line": "#d1d9e0", "grid": "#eaeef2",
              "green": "#1a7f37", "accent": "#0969da", "yellow": "#9a6700", "red": "#d1242f", "gray": "#818b98"},
        "HEAT": ["#ebedf0", "#9be9a8", "#40c463", "#30a14e", "#216e39"],
    },
}
C = THEMES["dark"]["C"]
HEAT = THEMES["dark"]["HEAT"]


def use_theme(name):
    global C, HEAT
    C, HEAT = THEMES[name]["C"], THEMES[name]["HEAT"]


# --------------------------------------------------------------- fetching ---

def http(url, data=None, headers=None):
    h = {"User-Agent": UA, "Accept": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def gh_graphql(token, query, variables=None):
    j = http("https://api.github.com/graphql", json.dumps({"query": query, "variables": variables or {}}).encode(),
             {"Authorization": f"bearer {token}", "Content-Type": "application/json"})
    if j.get("errors"):
        raise RuntimeError(j["errors"][0].get("message"))
    return j["data"]


def israel_hour(stamp):
    """Local hour in Israel for an ISO UTC timestamp."""
    t_utc = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    try:
        from zoneinfo import ZoneInfo
        return t_utc.astimezone(ZoneInfo("Asia/Jerusalem")).hour
    except Exception:  # noqa: BLE001  (no tz database: Israel DST rule by hand)
        import calendar

        def last_sunday(y, m):
            last = dt.date(y, m, calendar.monthrange(y, m)[1])
            return last - dt.timedelta(days=(last.weekday() + 1) % 7)

        d = t_utc.date()
        summer = last_sunday(d.year, 3) - dt.timedelta(days=2) <= d < last_sunday(d.year, 10)
        return (t_utc + dt.timedelta(hours=3 if summer else 2)).hour


def fetch_commit_hours(token):
    """Commits per local hour over the last year, across every repo committed to."""
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
    d = gh_graphql(token, f"""query{{user(login:"{USER}"){{id contributionsCollection{{
        commitContributionsByRepository(maxRepositories:100){{repository{{name owner{{login}}}}}}}}}}}}""")["user"]
    repos = [(r["repository"]["owner"]["login"], r["repository"]["name"])
             for r in d["contributionsCollection"]["commitContributionsByRepository"]]
    q = """query($o:String!,$n:String!,$id:ID!,$since:GitTimestamp!,$after:String){repository(owner:$o,name:$n){
        defaultBranchRef{target{... on Commit{history(first:100,after:$after,author:{id:$id},since:$since){
        pageInfo{hasNextPage endCursor} nodes{committedDate}}}}}}}"""
    hours = [0] * 24
    for owner, name in repos:
        after = None
        for _ in range(20):
            r = gh_graphql(token, q, {"o": owner, "n": name, "id": d["id"], "since": since, "after": after})
            ref = (r["repository"] or {}).get("defaultBranchRef")
            if not ref:
                break
            h = ref["target"]["history"]
            for node in h["nodes"]:
                hours[israel_hour(node["committedDate"])] += 1
            if not h["pageInfo"]["hasNextPage"]:
                break
            after = h["pageInfo"]["endCursor"]
    return hours, len(repos)


def fetch_github_public(token):
    d = gh_graphql(token, f"""query{{user(login:"{USER}"){{
        login name createdAt followers{{totalCount}}
        contributionsCollection{{contributionCalendar{{totalContributions
          weeks{{contributionDays{{date contributionCount}}}}}}}}}}}}""")["user"]
    cal = d["contributionsCollection"]["contributionCalendar"]
    days = [[x["date"], x["contributionCount"]] for w in cal["weeks"] for x in w["contributionDays"]]
    return {"created_at": d["createdAt"], "followers": d["followers"]["totalCount"],
            "total": cal["totalContributions"], "days": days}


def fetch_github_private(token):
    d = gh_graphql(token, f"""query{{user(login:"{USER}"){{
        pullRequests{{totalCount}} issues{{totalCount}}
        contributionsCollection{{totalCommitContributions totalPullRequestReviewContributions}}
        repositories(ownerAffiliations:OWNER, first:100){{totalCount nodes{{isFork stargazerCount
          languages(first:10, orderBy:{{field:SIZE, direction:DESC}}){{edges{{size node{{name color}}}}}}}}}}}}}}""")["user"]
    langs = {}
    for repo in d["repositories"]["nodes"]:
        if repo["isFork"]:
            continue
        for e in repo["languages"]["edges"]:
            n = e["node"]["name"]
            x = langs.setdefault(n, {"repos": 0, "bytes": 0, "color": e["node"]["color"] or "#888"})
            x["repos"] += 1
            x["bytes"] += e["size"]
    cc = d["contributionsCollection"]
    hours, contributed = fetch_commit_hours(token)
    return {  # counts only: no private repo names are stored
        "commits": cc["totalCommitContributions"], "reviews": cc["totalPullRequestReviewContributions"],
        "prs": d["pullRequests"]["totalCount"], "issues": d["issues"]["totalCount"],
        "repos": d["repositories"]["totalCount"],
        "stars": sum(r["stargazerCount"] for r in d["repositories"]["nodes"]),
        "languages": langs, "hours": hours, "contributed_to": contributed,
    }


def fetch_leetcode():
    q = f"""query{{allQuestionsCount{{difficulty count}}
      matchedUser(username:"{USER}"){{profile{{ranking}}
        submitStatsGlobal{{acSubmissionNum{{difficulty count}}}}
        languageProblemCount{{languageName problemsSolved}}
        userCalendar{{streak totalActiveDays}}}}}}"""
    d = http("https://leetcode.com/graphql", json.dumps({"query": q}).encode(),
             {"Content-Type": "application/json", "Referer": "https://leetcode.com"})["data"]
    u = d["matchedUser"]
    solved = {x["difficulty"]: x["count"] for x in u["submitStatsGlobal"]["acSubmissionNum"]}
    total = {x["difficulty"]: x["count"] for x in d["allQuestionsCount"]}
    langs = sorted(u["languageProblemCount"], key=lambda x: -x["problemsSolved"])
    return {"solved": solved, "total": total, "ranking": u["profile"]["ranking"],
            "streak": u["userCalendar"]["streak"], "active_days": u["userCalendar"]["totalActiveDays"],
            "language": langs[0]["languageName"] if langs else None}


def fetch_chess():
    p = http(f"https://api.chess.com/pub/player/{USER}")
    s = http(f"https://api.chess.com/pub/player/{USER}/stats")
    modes = {}
    for key, name in (("chess_blitz", "Blitz"), ("chess_rapid", "Rapid"),
                      ("chess_bullet", "Bullet"), ("chess_daily", "Daily")):
        if key in s:
            m = s[key]
            modes[name] = {"rating": m["last"]["rating"], "best": m.get("best", {}).get("rating"),
                           "win": m["record"]["win"], "loss": m["record"]["loss"], "draw": m["record"]["draw"]}
    return {"modes": modes, "league": p.get("league"), "followers": p.get("followers"),
            "puzzles": s.get("tactics", {}).get("highest", {}).get("rating"),
            "rush": s.get("puzzle_rush", {}).get("best", {}).get("score")}


def fetch_tryhackme():
    d = http(f"https://tryhackme.com/api/v2/public-profile?username={USER}")["data"]
    return pick_thm(d)


def pick_thm(d):
    return {k: d.get(k) for k in ("level", "totalPoints", "rank", "topPercentage", "completedRoomsNumber",
                                   "badgesNumber", "streak", "largestStreak", "leagueTier", "dateSignUp")}


# ---------------------------------------------------------------- helpers ---

def n(x):
    return f"{x:,}" if isinstance(x, int) else str(x)


def fdate(s, year=True):
    d = dt.date.fromisoformat(s[:10])
    return f"{d:%b} {d.day}, {d.year}" if year else f"{d:%b} {d.day}"


def t(x, y, s, cls, anchor="start", extra=""):
    return f'<text x="{x:.1f}" y="{y:.1f}" class="{cls}" text-anchor="{anchor}" {extra}>{escape(str(s))}</text>'


TODAY = dt.datetime.now(dt.timezone.utc).date()


class Card:
    def __init__(self, w, h, title=None, sub=None, updated=None, border=True):
        self.w, self.h, self.parts, self.css, self.k = w, h, [], [], 0
        self.border = border
        if title:
            self.parts.append(t(24, 34, title, "title"))
        # Only flag data that has gone stale (its source kept failing).
        if updated and (TODAY - dt.date.fromisoformat(updated)).days > 2:
            sub = f"{sub + ' · ' if sub else ''}updated {fdate(updated)}"
        if sub:
            self.parts.append(t(w - 24, 34, sub, "sub", "end"))

    def add(self, s):
        self.parts.append(s)

    def fade(self, inner, delay):
        return f'<g class="fade" style="animation-delay:{delay}ms">{inner}</g>'

    def ring(self, cx, cy, r, frac, width, color, delay=0, track=None):
        track = track or C["track"]
        frac = max(0.0, min(1.0, frac))
        circ = 2 * math.pi * r
        off = circ * (1 - frac)
        self.k += 1
        name = f"r{self.k}"
        self.css.append(f"@keyframes {name}{{from{{stroke-dashoffset:{circ:.2f}}}to{{stroke-dashoffset:{off:.2f}}}}}"
                        f".{name}{{animation:{name} 1.1s ease-out {delay}ms both}}")
        return (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{track}" stroke-width="{width}"/>'
                + (f'<circle class="{name}" cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{color}" '
                   f'stroke-width="{width}" stroke-linecap="round" stroke-dasharray="{circ:.2f}" '
                   f'stroke-dashoffset="{off:.2f}" transform="rotate(-90 {cx} {cy})"/>' if frac > 0 else ""))

    def svg(self):
        w, h = self.w, self.h
        css = f"""
.title{{font:600 15px {FONT};fill:{C['title']}}}
.sub{{font:400 12px {FONT};fill:{C['muted']}}}
.upd{{font:400 10px {FONT};fill:{C['faint']}}}
.big{{font:700 28px {FONT};fill:{C['text']}}}
.mid{{font:700 20px {FONT};fill:{C['text']}}}
.val{{font:600 15px {FONT};fill:{C['text']}}}
.lab{{font:400 12px {FONT};fill:{C['muted']}}}
.sm{{font:400 11px {FONT};fill:{C['muted']}}}
.tiny{{font:400 10px {FONT};fill:{C['faint']}}}
.fade{{opacity:0;animation:fade .7s ease-out forwards}}
@keyframes fade{{from{{opacity:0;transform:translateY(5px)}}to{{opacity:1;transform:none}}}}
{''.join(self.css)}
@media (prefers-reduced-motion:reduce){{.fade{{animation:none;opacity:1}}*{{animation:none!important}}}}"""
        return f"""<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{w}" height="{h}" viewBox="0 0 {w} {h}" fill="none" role="img">
<style>{css}</style>
<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="6" fill="{C['bg']}" stroke="{C['line'] if self.border else C['bg']}"/>
{''.join(self.parts)}
</svg>
"""


# ------------------------------------------------------- calendar maths ---

def cal_stats(days):
    counts = [c for _, c in days]
    dates = [d for d, _ in days]
    out = {"active": sum(1 for c in counts if c), "len": len(days), "total": sum(counts)}

    def runs(pred):
        best, cur, best_end, start = 0, 0, None, None
        for i, c in enumerate(counts):
            if pred(c):
                cur += 1
                if cur > best:
                    best, best_end = cur, i
            else:
                cur = 0
        return best, (dates[best_end - best + 1], dates[best_end]) if best else None

    out["longest"], out["longest_range"] = runs(lambda c: c > 0)
    out["gap"], out["gap_range"] = runs(lambda c: c == 0)
    i = len(counts) - 1
    if counts[i] == 0:  # today not started yet: streak still alive from yesterday
        i -= 1
    cur = 0
    while i >= 0 and counts[i] > 0:
        cur, i = cur + 1, i - 1
    out["current"] = cur
    out["current_range"] = (dates[i + 1], dates[i + cur]) if cur else None
    wk = sum(c for d, c in days if dt.date.fromisoformat(d).weekday() >= 5)
    out["weekend_pct"] = wk / out["total"] if out["total"] else 0
    bi = max(range(len(counts)), key=lambda k: counts[k])
    out["best"], out["best_date"] = counts[bi], dates[bi]
    out["per_active"] = out["total"] / out["active"] if out["active"] else 0
    by_wd = [0] * 7
    for d, c in days:
        by_wd[dt.date.fromisoformat(d).weekday()] += c
    out["by_weekday"] = by_wd
    last30, prev30 = counts[-30:], counts[-60:-30]
    out["m_total"], out["m_prev"] = sum(last30), sum(prev30)
    out["m_active"] = sum(1 for c in last30 if c)
    out["m_best"] = max(last30) if last30 else 0
    out["m_best_date"] = dates[-30:][last30.index(out["m_best"])] if last30 else None
    out["last30"] = list(zip(dates[-30:], last30))
    return out


def grade(p):
    """github-readme-stats style rank: weighted CDFs -> percentile -> letter."""
    def ecdf(x):
        return 1 - 2 ** (-x)

    def lcdf(x):
        return x / (1 + x)

    parts = [(2, ecdf(p["commits"] / 250)), (3, ecdf(p["prs"] / 50)), (1, ecdf(p["issues"] / 25)),
             (1, ecdf(p.get("reviews", 0) / 2)), (4, lcdf(p["stars"] / 50)), (1, lcdf(p["followers"] / 10))]
    score = sum(w * v for w, v in parts) / sum(w for w, _ in parts)
    pct = (1 - score) * 100
    for th, lv in zip([1, 12.5, 25, 37.5, 50, 62.5, 75, 87.5, 100], ["S", "A+", "A", "A-", "B+", "B", "B-", "C+", "C"]):
        if pct <= th:
            return lv, score
    return "C", score


# ------------------------------------------------------------------ cards ---

# Pixel shiba for the neofetch card. Tones: + outline, o fur, x inner ear,
# . white fur, # eyes/nose/mouth. Drawn as dither patterns, so it looks like
# ANSI shade art (░▒▓█) but renders the same in every browser.
DOGE = [
    '',
    '       +                  +',
    '       +                  +',
    '       ++                ++',
    '       +o+              +o+',
    '      +oxo+            +oxo+',
    '      +oxxo++++++++++++oxxo+',
    '      +xxxxooooooooooooxxxx+',
    '      +xxxxooooooooooooxxxx+',
    '     +oxxxxxooooooooooxxxxxo+',
    '     +oxxx..oooooooooo..xxxo+',
    '     +oxxo..oooooooooo..oxxo+',
    '     +oooooooooooooooooooooo+',
    '    +oooo##...oooooo##...oooo+',
    '   +ooooo##...oooooo##...ooooo+',
    '  +oooooooooooooooooooooooooooo+',
    '  +oooooooooo........oooooooooo+',
    '  +oo....oo.....##.....oo....oo+',
    '  +o...........####...........o+',
    '  +............####............+',
    '  +.............##.............+',
    '   +........##......##........+',
    '    +.........######.........+',
    '     ++..oo............oo..++',
    '       +++ooo........ooo+++',
    '          +++++oooo+++++',
    '               ++++',
]
# Shade per tone and theme: 1 solid, .75/.5/.25 dither, 0 empty. Dark mode
# lights the white fur; light mode inks the outline and features.
SHADES = {
    "dark": {"+": 0.75, "o": 0.5, "x": 0.25, ".": 1, "#": 0},
    "light": {"+": 1, "o": 0.5, "x": 0.75, ".": 0.25, "#": 1},
}


def card_neofetch(data, s, theme):
    W, H = 880, 440
    c = Card(W, H)
    g, p = data.get("github", {}), data.get("github_private", {})
    ch, lc = data.get("chess", {}), data.get("leetcode", {})
    mono = "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace"
    c.css.append(f".m{{font:400 13px {mono};fill:{C['text']}}}.k{{font:600 13px {mono};fill:{C['accent']}}}"
                 f".pr{{font:400 13px {mono};fill:{C['green']}}}.pa{{font:400 13px {mono};fill:{C['accent']}}}"
                 f".mt{{font:400 12px {FONT};fill:{C['muted']}}}"
                 ".cur{animation:blink 1.1s steps(1) infinite}@keyframes blink{50%{opacity:0}}")

    # window chrome
    c.add(f'<path d="M0.5,36 V6.5 A6,6 0 0 1 6.5,0.5 H{W - 6.5} A6,6 0 0 1 {W - 0.5},6.5 V36 Z" fill="{C["box"]}"/>'
          f'<line x1="0.5" y1="36" x2="{W - 0.5}" y2="36" stroke="{C["line"]}"/>')
    for i in range(3):
        c.add(f'<circle cx="{20 + i * 18}" cy="18" r="5.5" fill="none" stroke="{C["faint"]}"/>')
    c.add(t(W / 2, 23, "alexey@belkin: ~", "mt", "middle"))

    def prompt(y, cmd):
        return (f'<text x="24" y="{y}" xml:space="preserve"><tspan class="pr">alexey@belkin</tspan>'
                f'<tspan class="m">:</tspan><tspan class="pa">~</tspan><tspan class="m">$ {escape(cmd)}</tspan></text>')

    c.add(prompt(66, "neofetch"))

    # the doge
    px, x0, y0 = 9, 34, 96
    pat = []
    for name, cells in (("d25", ((0, 0),)), ("d50", ((0, 0), (2, 2))), ("d75", ((0, 0), (2, 2), (2, 0)))):
        rects = "".join(f'<rect x="{a}" y="{b}" width="2" height="2" fill="{C["text"]}"/>' for a, b in cells)
        pat.append(f'<pattern id="{name}" width="4" height="4" patternUnits="userSpaceOnUse">{rects}</pattern>')
    fills = {1: C["text"], 0.75: "url(#d75)", 0.5: "url(#d50)", 0.25: "url(#d25)"}
    shade = SHADES[theme]
    art = []
    for row, line in enumerate(DOGE):
        col = 0
        while col < len(line):
            ch_ = line[col]
            run = 1
            while col + run < len(line) and line[col + run] == ch_:
                run += 1
            v = shade.get(ch_, 0)
            if ch_ != " " and v:
                art.append(f'<rect x="{x0 + col * px}" y="{y0 + row * px}" width="{run * px}" height="{px}" fill="{fills[v]}"/>')
            col += run
    c.add("<defs>" + "".join(pat) + "</defs>")
    c.add(c.fade('<g shape-rendering="crispEdges">' + "".join(art) + "</g>", 150))

    # system info
    created = g.get("created_at")
    if created:
        j = dt.date.fromisoformat(created[:10])
        months = (TODAY.year - j.year) * 12 + TODAY.month - j.month - (TODAY.day < j.day)
        uptime = (f"{months // 12} year{'s' * (months // 12 != 1)}, " if months >= 12 else "") + \
                 f"{months % 12} month{'s' * (months % 12 != 1)}"
    else:
        uptime = "since 2025"
    modes = ch.get("modes", {})
    rows = [
        ("OS", "Israel x86_64"),
        ("Host", "Alexey Belkin, 16"),
        ("Kernel", "self-taught 2020-lts"),
        ("Uptime", f"{uptime} on GitHub"),
        ("Packages", f"{n(p.get('commits', 0))} commits, {p.get('repos', 0)} repos (git)"),
        ("Shell", "python, dart, c#"),
        ("DE", "Flutter"),
        ("WM", "judo"),
        ("Theme", f"doge [{theme}]"),
        ("CPU", "AI agents (overclocked)"),
        ("Memory", f"{n(s['total'])} contributions / {s['len']} days" if s else "—"),
        ("Streak", f"{s['current']} days (best {s['longest']})" if s else "—"),
        ("Chess", " · ".join(f"{m['rating']} {k.lower()}" for k, m in list(modes.items())[:2]) or "—"),
        ("LeetCode", f"{sum(lc.get('solved', {}).get(k, 0) for k in ('Easy', 'Medium', 'Hard'))} solved"),
        ("Status", "such code. much wow."),
    ]
    xk, xv, y = 380, 470, 100
    head = (f'<text x="{xk}" y="{y}"><tspan class="k">alexeybe1kin</tspan><tspan class="m">@</tspan>'
            f'<tspan class="k">github</tspan></text>'
            f'<text x="{xk}" y="{y + 17}" class="m">{"-" * 19}</text>')
    c.add(c.fade(head, 200))
    for i, (k, v) in enumerate(rows):
        yy = y + 34 + i * 17
        c.add(c.fade(t(xk, yy, f"{k}:", "k") + t(xv, yy, v, "m"), 250 + i * 45))
    by = y + 34 + len(rows) * 17 + 2
    blocks = HEAT + [C["gray"], C["text"]]
    c.add(c.fade("".join(f'<rect x="{xk + i * 24}" y="{by}" width="22" height="14" fill="{col}"/>'
                         for i, col in enumerate(blocks)), 250 + len(rows) * 45))
    c.add(prompt(H - 22, ""))
    c.add(f'<rect class="cur" x="{24 + 7.8 * 16:.1f}" y="{H - 34}" width="8" height="15" fill="{C["text"]}"/>')
    return c.svg()


# --------------------------------------------------------------- dashboard ---
# One combined panel in the style of lowlighter/metrics: GitHub octicons,
# small text, blue section headers, two columns.

OCT = json.load(open(os.path.join(ROOT, "assets", "octicons.json"), encoding="utf-8"))
STACK = ["dart", "flutter", "python", "cs", "unity", "html", "css", "js", "git"]


def octicon(name, x, y, color, size=16):
    paths = "".join(f'<path d="{d}"/>' for d in OCT.get(name, []))
    return f'<svg x="{x}" y="{y}" width="{size}" height="{size}" viewBox="0 0 16 16" fill="{color}">{paths}</svg>'


def logo(path, x, y, size, prefix):
    """Inline a 256x256 logo file, prefixing its ids so several can share a document."""
    import re
    inner = re.search(r"<svg[^>]*>(.*)</svg>", open(path, encoding="utf-8").read(), re.S).group(1)
    inner = re.sub(r'id="([^"]+)"', lambda m: f'id="{prefix}-{m.group(1)}"', inner)
    inner = re.sub(r"url\(#([^)]+)\)", lambda m: f"url(#{prefix}-{m.group(1)})", inner)
    inner = re.sub(r'href="#([^"]+)"', lambda m: f'href="#{prefix}-{m.group(1)}"', inner)
    return f'<svg x="{x}" y="{y}" width="{size}" height="{size}" viewBox="0 0 256 256" fill="none">{inner}</svg>'


def shade(hexcol, k):
    r, g, b = (int(hexcol[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % (int(r * k), int(g * k), int(b * k))


def ago(created):
    j = dt.date.fromisoformat(created[:10])
    months = (TODAY.year - j.year) * 12 + TODAY.month - j.month - (TODAY.day < j.day)
    if months >= 12:
        y = months // 12
        return f"{y} year{'s' * (y != 1)} ago"
    return f"{months} month{'s' * (months != 1)} ago" if months else "this month"


def card_dashboard(data, s, avatar):
    W, H = 880, 556
    c = Card(W, H, border=False)
    g, p = data.get("github", {}), data.get("github_private", {})
    ch, lc, thm = data.get("chess", {}), data.get("leetcode", {}), data.get("tryhackme", {})
    c.css.append(
        f".nm{{font:600 22px {FONT};fill:{C['accent']}}}"
        f".h{{font:400 16px {FONT};fill:{C['accent']}}}"
        f".b{{font:400 13px {FONT};fill:{C['text']}}}"
        f".bm{{font:400 13px {FONT};fill:{C['muted']}}}"
        f".ct{{font:400 12.5px {FONT};fill:{C['accent']}}}"
        f".ax{{font:400 9.5px {FONT};fill:{C['muted']}}}")
    mut, acc = C["muted"], C["accent"]

    def line(icon, x, y, text, cls="bm"):
        return octicon(icon, x, y - 12, mut) + t(x + 22, y, text, cls)

    def header(icon, x, y, text):
        return octicon(icon, x, y - 13, mut) + t(x + 22, y, text, "h")

    # ---- left column -------------------------------------------------------
    L, R = 20, 460
    if avatar:
        c.add(f'<clipPath id="av"><circle cx="{L + 12}" cy="28" r="12"/></clipPath>'
              f'<circle cx="{L + 12}" cy="28" r="12" fill="{C["box"]}"/>'
              f'<image href="data:image/png;base64,{avatar}" x="{L}" y="16" width="24" height="24" clip-path="url(#av)"/>')
    c.add(t(L + 32, 36, "Alexey Belkin", "nm"))
    c.add(c.fade(line("clock", L, 64, f"Joined GitHub {ago(g['created_at'])}" if g else "Joined GitHub")
                 + line("people", L, 85, f"Followed by {g.get('followers', 0)} users")
                 + line("location", L, 106, "Israel · 16 · judo"), 80))
    last14 = [v for _, v in s["last30"][-14:]]
    sq = "".join(
        f'<rect x="{L + 226 + i * 15}" y="20" width="12" height="12" rx="2" '
        f'fill="{HEAT[0 if v == 0 else 1 if v < 5 else 2 if v < 15 else 3 if v < 40 else 4]}"/>'
        for i, v in enumerate(last14))
    c.add(c.fade(sq + line("repo-push", L + 226, 64, f"Contributed to {p.get('contributed_to', '—')} repositories")
                 + line("zap", L + 226, 85, f"{n(s['m_total'])} contributions in 30 days")
                 + line("flame", L + 226, 106, f"{s['current']} day streak"), 140))

    c.add(header("repo", L, 146, f"{p.get('repos', 0)} Repositories"))
    lv, _ = grade({**p, "followers": g.get("followers", 0)}) if p else ("—", 0)
    left = [("git-commit", f"{n(p.get('commits', 0))} commits this year"),
            ("git-pull-request", f"{n(p.get('prs', 0))} pull requests"),
            ("issue-opened", f"{n(p.get('issues', 0))} issues"),
            ("star", f"{n(p.get('stars', 0))} stars earned")]
    right = [("pulse", f"{n(s['total'])} contributions"),
             ("calendar", f"{s['active']} active days"),
             ("rocket", f"{s['best']} on busiest day"),
             ("trophy", f"rank {lv}")]
    for i, ((ia, ta), (ib, tb)) in enumerate(zip(left, right)):
        y = 170 + i * 21
        c.add(c.fade(line(ia, L, y, ta) + line(ib, L + 226, y, tb), 200 + i * 50))

    c.add(header("light-bulb", L, 278, "Coding habits and recent activity"))
    hours = p.get("hours") or [0] * 24
    c.add(t(L + 210, 298, "Commit activity per time of the day", "ct", "middle"))
    top = max(1, max(hours))
    bars = ""
    for i, v in enumerate(hours):
        x = L + i * 17.5 + 3
        h = 38 * v / top
        if v:
            bars += (f'<rect x="{x:.1f}" y="{362 - h:.1f}" width="9" height="{h:.1f}" rx="4" fill="{C["green"]}"/>'
                     + t(x + 4.5, 356 - h, v, "ax", "middle"))
        else:
            bars += f'<rect x="{x:.1f}" y="360" width="9" height="2" rx="1" fill="{C["track"]}"/>'
        bars += t(x + 4.5, 376, f"{i:02d}", "ax", "middle")
    c.add(c.fade(bars, 300))

    wd = s["by_weekday"]
    wd = [wd[6]] + wd[:6]  # Sunday first, like GitHub
    c.add(t(L + 100, 400, "Contributions per weekday", "ct", "middle"))
    top = max(1, max(wd))
    bars = ""
    for i, v in enumerate(wd):
        x = L + 6 + i * 28
        h = max(2, 34 * v / top)
        bars += (f'<rect x="{x}" y="{458 - h:.1f}" width="14" height="{h:.1f}" rx="4" fill="{C["green"] if v else C["track"]}"/>'
                 + t(x + 7, 452 - h, v, "ax", "middle") + t(x + 7, 472, "SMTWTFS"[i], "ax", "middle"))
    c.add(c.fade(bars, 350))
    c.add(t(L + 320, 400, "Last 30 days", "ct", "middle"))
    vals = [v for _, v in s["last30"]]
    top = max(1, max(vals))
    bars = "".join(
        f'<rect x="{L + 220 + i * 6.7:.1f}" y="{458 - max(2, 34 * v / top):.1f}" width="5" '
        f'height="{max(2, 34 * v / top):.1f}" rx="1.5" fill="{C["green"] if v else C["track"]}"/>' for i, v in enumerate(vals))
    bars += t(L + 220, 472, fdate(s["last30"][0][0], False), "ax") + t(L + 420, 472, "today", "ax", "end")
    c.add(c.fade(bars, 400))

    c.add(header("telescope", L, 506, "Mastered technologies and topics"))
    icons = "".join(logo(os.path.join(ROOT, "assets", "stack", f"{k}.svg"), L + i * 28, 520, 24, f"st{i}")
                    for i, k in enumerate(STACK))
    c.add(c.fade(icons, 450))

    # ---- right column ------------------------------------------------------
    c.add(header("pulse", R, 36, "Streaks & habits"))
    rings = [("Active days", s["active"], s["active"] / s["len"], C["green"]),
             ("Current streak", s["current"], s["current"] / max(1, s["longest"]), C["green"]),
             ("Best streak", s["longest"], 1.0 if s["longest"] else 0, acc),
             ("Weekend", f"{round(s['weekend_pct'] * 100)}%", s["weekend_pct"], acc)]
    for i, (lab, val, frac, col) in enumerate(rings):
        cx = R + 50 + i * 102
        c.add(c.fade(c.ring(cx, 82, 22, frac, 4, col, 150 + i * 100) + t(cx, 87, n(val), "val", "middle")
                     + t(cx, 126, lab, "bm", "middle"), 120 + i * 100))

    c.add(header("calendar", R, 166, "Contributions calendar"))
    days = g.get("days", [])
    first = dt.date.fromisoformat(days[0][0]) if days else TODAY
    pad = (first.weekday() + 1) % 7
    cells = [((i + pad) // 7, (i + pad) % 7, v) for i, (_, v) in enumerate(days)]
    last_col = cells[-1][0] if cells else 0
    cells = [(col - (last_col - 25), row, v) for col, row, v in cells if col > last_col - 26]
    top = max(1, max((v for *_, v in cells), default=1))
    sz, ox, oy = 6.5, R + 52, 230
    iso = []
    for col, row, v in sorted(cells, key=lambda k: (k[0] + k[1], k[0])):
        lvl = 0 if v == 0 else 1 if v < 5 else 2 if v < 15 else 3 if v < 40 else 4
        topc = C["track"] if lvl == 0 else HEAT[lvl]
        h = 2 + 44 * math.sqrt(v / top)
        px, py = ox + (col - row) * sz, oy + (col + row) * sz / 2

        def poly(pts, fill):
            return f'<polygon points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in pts)}" fill="{fill}"/>'

        iso.append(poly([(px - sz, py + sz / 2 - h), (px, py + sz - h), (px, py + sz), (px - sz, py + sz / 2)], shade(topc, 0.78))
                   + poly([(px, py + sz - h), (px + sz, py + sz / 2 - h), (px + sz, py + sz / 2), (px, py + sz)], shade(topc, 0.62))
                   + poly([(px, py - h), (px + sz, py + sz / 2 - h), (px, py + sz - h), (px - sz, py + sz / 2 - h)], topc))
    c.add(c.fade("".join(iso), 250))
    c.add(c.fade(line("flame", R + 252, 214, f"Current streak {s['current']} days")
                 + line("graph", R + 252, 236, f"~{s['total'] / max(1, s['len']):.2f} per day")
                 + line("rocket", R + 252, 258, f"Best day: {s['best']}"), 300))

    c.add(header("code-square", R, 372, "Most used languages"))
    langs = sorted(p.get("languages", {}).items(), key=lambda kv: (-kv[1]["repos"], -kv[1]["bytes"]))[:6]
    total = sum(v["repos"] for _, v in langs) or 1
    x, bar, legend = R, "", ""
    for i, (name, v) in enumerate(langs):
        bw = 400 * v["repos"] / total
        bar += f'<rect x="{x:.1f}" y="384" width="{max(bw - 2, 1):.1f}" height="8" fill="{v["color"]}"/>'
        x += bw
        lx = R + 6 + i * 110
        legend += f'<circle cx="{lx}" cy="410" r="5" fill="{v["color"]}"/>' + t(lx + 12, 414, f"{name} {100 * v['repos'] / total:.0f}%", "b")
    c.add(c.fade(f'<clipPath id="lb"><rect x="{R}" y="384" width="400" height="8" rx="4"/></clipPath>'
                 f'<g clip-path="url(#lb)">{bar}</g>' + legend, 350))

    c.add(header("globe", R, 452, "Elsewhere"))
    modes = ch.get("modes", {})
    solved = lc.get("solved", {})
    rows = [
        ("chessdotcom", "Chess.com", " · ".join(f"{m['rating']} {k.lower()}" for k, m in list(modes.items())[:3])
         + (f" · {ch['puzzles']} puzzles" if ch.get("puzzles") else "")),
        ("leetcode", "LeetCode", f"{sum(solved.get(k, 0) for k in ('Easy', 'Medium', 'Hard'))} solved · "
                                 f"{solved.get('Easy', 0)} easy · {solved.get('Medium', 0)} medium · {solved.get('Hard', 0)} hard"),
        ("tryhackme", "TryHackMe", f"level {thm.get('level', '—')} · {(thm.get('rank') or '').lower()} · "
                                   f"{thm.get('completedRoomsNumber', 0)} rooms · {thm.get('badgesNumber', 0)} badges"),
    ]
    for i, (ic, name, text) in enumerate(rows):
        y = 478 + i * 26
        c.add(c.fade(logo(os.path.join(ROOT, "assets", "icons", f"{ic}.svg"), R, y - 14, 18, f"el{i}")
                     + t(R + 26, y, name, "b") + t(R + 108, y, text, "bm"), 400 + i * 60))
    return c.svg()


# ------------------------------------------------------------------- main ---

def main():
    out = sys.argv[1]
    cards = os.path.join(out, "cards")
    os.makedirs(cards, exist_ok=True)
    data_path = os.path.join(cards, "data.json")
    try:
        data = json.load(open(data_path, encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    now = dt.datetime.now(dt.timezone.utc).date().isoformat()

    if "--seed-thm" in sys.argv:
        seed = json.load(open(sys.argv[sys.argv.index("--seed-thm") + 1], encoding="utf-8"))
        data["tryhackme"] = {**pick_thm(seed.get("data", seed)), "updated": now}

    gh_token = os.environ.get("GITHUB_TOKEN", "")
    stats_token = os.environ.get("STATS_TOKEN", "")

    def refresh(key, fn, allowed=True):
        if not allowed:
            print(f"[{key}] kept saved data (no token that can see it)")
            return
        try:
            data[key] = {**fn(), "updated": now}
            print(f"[{key}] updated")
        except Exception as e:  # noqa: BLE001
            print(f"[{key}] FAILED, keeping saved data: {e}")

    refresh("github", lambda: fetch_github_public(stats_token or gh_token), bool(stats_token or gh_token))
    refresh("github_private", lambda: fetch_github_private(stats_token or gh_token),
            bool(stats_token) or ("github_private" not in data and bool(gh_token)))
    refresh("leetcode", fetch_leetcode)
    refresh("chess", fetch_chess)
    refresh("tryhackme", fetch_tryhackme)

    avatar_path = os.path.join(ROOT, "assets", "avatar.png")
    avatar = base64.b64encode(open(avatar_path, "rb").read()).decode() if os.path.exists(avatar_path) else None

    def write(name, svg):
        open(os.path.join(cards, name), "w", encoding="utf-8").write(svg)
        print("  wrote", name)

    for f in os.listdir(cards):
        if f.endswith(".svg"):
            os.remove(os.path.join(cards, f))
    # name.svg is the dark-theme card, name-light.svg the light-theme one.
    for theme, suffix in (("dark", ""), ("light", "-light")):
        use_theme(theme)
        if "github" in data:
            s = cal_stats(data["github"]["days"])
            write(f"dashboard{suffix}.svg", card_dashboard(data, s, avatar))
            write(f"neofetch{suffix}.svg", card_neofetch(data, s, theme))
    json.dump(data, open(data_path, "w", encoding="utf-8"), indent=1)


if __name__ == "__main__":
    main()
