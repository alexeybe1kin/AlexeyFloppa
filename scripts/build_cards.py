"""Build the profile's stat cards as SVGs from live data.

Sources: GitHub GraphQL, LeetCode, Chess.com and TryHackMe. Every source is
optional: when one fails (rate limit, bot wall, outage) its last good data is
reused from data.json, so a bad run never blanks a card.

usage: python scripts/build_cards.py <out_dir> [--seed-thm thm.json]
env:   GITHUB_TOKEN  public data (contribution calendar, followers)
       STATS_TOKEN   optional token that can see private repos; without it the
                     stats/languages cards keep their last saved values
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
MONO = "'DejaVu Sans Mono', Menlo, 'Liberation Mono', 'Courier New', monospace"
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


def gh_graphql(token, query):
    j = http("https://api.github.com/graphql", json.dumps({"query": query}).encode(),
             {"Authorization": f"bearer {token}", "Content-Type": "application/json"})
    if j.get("errors"):
        raise RuntimeError(j["errors"][0].get("message"))
    return j["data"]


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
    return {  # counts only: no private repo names are stored
        "commits": cc["totalCommitContributions"], "reviews": cc["totalPullRequestReviewContributions"],
        "prs": d["pullRequests"]["totalCount"], "issues": d["issues"]["totalCount"],
        "repos": d["repositories"]["totalCount"],
        "stars": sum(r["stargazerCount"] for r in d["repositories"]["nodes"]),
        "languages": langs,
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
    def __init__(self, w, h, title=None, sub=None, updated=None):
        self.w, self.h, self.parts, self.css, self.k = w, h, [], [], 0
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
<rect x="0.5" y="0.5" width="{w - 1}" height="{h - 1}" rx="6" fill="{C['bg']}" stroke="{C['line']}"/>
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

def card_calendar(g, upd):
    days = g["days"]
    c = Card(880, 214, "Contribution calendar", f"{n(g['total'])} contributions in the last year", upd)
    step, cell, x0, y0 = 15, 12, 50, 82
    first = dt.date.fromisoformat(days[0][0])
    pad = (first.weekday() + 1) % 7  # GitHub columns start on Sunday
    cols, last_month = {}, None
    for i, (d, cnt) in enumerate(days):
        k = i + pad
        col, row = k // 7, k % 7
        lvl = 0 if cnt == 0 else 1 if cnt < 5 else 2 if cnt < 15 else 3 if cnt < 40 else 4
        cols.setdefault(col, []).append(
            f'<rect x="{x0 + col * step}" y="{y0 + row * step}" width="{cell}" height="{cell}" rx="3" fill="{HEAT[lvl]}"><title>{d}: {cnt}</title></rect>')
        dd = dt.date.fromisoformat(d)
        if row == 0 and dd.month != last_month and dd.day <= 7:
            c.add(t(x0 + col * step, y0 - 8, f"{dd:%b}", "sm"))
            last_month = dd.month
    for col, rects in cols.items():
        c.add(c.fade("".join(rects), 120 + col * 12))
    for row, lab in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        c.add(t(x0 - 8, y0 + row * step + 10, lab, "tiny", "end"))
    lx = 880 - 24 - 5 * 15 - 34
    c.add(t(lx - 6, 200, "less", "tiny", "end"))
    for i, col in enumerate(HEAT):
        c.add(f'<rect x="{lx + i * 15}" y="191" width="11" height="11" rx="3" fill="{col}"/>')
    c.add(t(lx + 5 * 15 + 2, 200, "more", "tiny"))
    return c.svg()


def card_rings(s, upd):
    c = Card(880, 200, "Streaks & habits", "last 12 months", upd)
    tiles = [
        ("Active days", s["active"], s["active"] / s["len"], C["green"], f"of {s['len']} days"),
        ("Current streak", s["current"], s["current"] / max(1, s["longest"]), C["green"],
         f"{fdate(s['current_range'][0], False)} – today" if s["current"] else "starts today"),
        ("Longest streak", s["longest"], 1.0 if s["longest"] else 0, C["accent"],
         f"{fdate(s['longest_range'][0], False)} – {fdate(s['longest_range'][1], False)}" if s["longest"] else "—"),
        ("Longest break", s["gap"], s["gap"] / s["len"], C["gray"],
         f"{fdate(s['gap_range'][0], False)} – {fdate(s['gap_range'][1], False)}" if s["gap"] else "never stopped"),
        ("Weekend share", f"{round(s['weekend_pct'] * 100)}%", s["weekend_pct"], C["accent"], "of all contributions"),
    ]
    tw = 880 / 5
    for i, (lab, val, frac, col, sub) in enumerate(tiles):
        cx = tw * i + tw / 2
        g = c.ring(round(cx), 98, 34, frac, 7, col, 150 + i * 120)
        g += t(cx, 106, n(val), "mid", "middle")
        g += t(cx, 156, lab, "val", "middle") + t(cx, 174, sub, "sm", "middle")
        c.add(c.fade(g, 100 + i * 120))
    return c.svg()


def card_highlights(g, s, upd):
    c = Card(880, 140, "Highlights", None, upd)
    joined = dt.date.fromisoformat(g["created_at"][:10])
    today = dt.date.fromisoformat(g["days"][-1][0])
    months = (today.year - joined.year) * 12 + today.month - joined.month - (today.day < joined.day)
    age = (f"{months // 12} year{'s' * (months // 12 != 1)} " if months >= 12 else "") + \
          (f"{months % 12} month{'s' * (months % 12 != 1)}" if months % 12 else "")
    tiles = [
        ("Joined GitHub", fdate(g["created_at"]), f"{age.strip() or 'this month'} ago"),
        ("Contributions", n(s["total"]), "in the last 12 months"),
        ("Busiest day", f"{s['best']} contributions", fdate(s["best_date"])),
        ("Per active day", f"{s['per_active']:.1f}", "average contributions"),
    ]
    tw = 880 / 4
    for i, (lab, val, sub) in enumerate(tiles):
        x = tw * i + 24
        if i:
            c.add(f'<line x1="{tw * i:.0f}" y1="56" x2="{tw * i:.0f}" y2="118" stroke="{C["line"]}"/>')
        c.add(c.fade(t(x, 68, lab, "lab") + t(x, 96, val, "mid") + t(x, 116, sub, "sm"), 100 + i * 110))
    return c.svg()


def card_activity(s_days, upd):
    days = s_days
    weeks = []
    for i in range(0, len(days), 7):
        chunk = days[i:i + 7]
        weeks.append((chunk[0][0], sum(x for _, x in chunk)))
    c = Card(880, 250, "Contributions over time", "weekly totals · last 12 months", upd)
    x0, x1, y0, y1 = 56, 856, 60, 206
    top = max(1, max(v for _, v in weeks))
    nice = next(m for m in (5, 10, 20, 25, 50, 100, 150, 200, 250, 300, 400, 500, 750, 1000, 2000, 5000, 10**6) if m >= top)
    for k in range(5):
        y = y1 - (y1 - y0) * k / 4
        c.add(f'<line x1="{x0}" y1="{y:.1f}" x2="{x1}" y2="{y:.1f}" stroke="{C["grid"]}"/>')
        c.add(t(x0 - 10, y + 4, n(round(nice * k / 4)), "tiny", "end"))
    pts = [(x0 + (x1 - x0) * i / (len(weeks) - 1), y1 - (y1 - y0) * v / nice) for i, (_, v) in enumerate(weeks)]

    def smooth_path(p):
        d = f"M{p[0][0]:.1f},{p[0][1]:.1f}"
        for i in range(1, len(p)):
            (xa, ya), (xb, yb) = p[i - 1], p[i]
            mx = (xa + xb) / 2
            d += f" C{mx:.1f},{ya:.1f} {mx:.1f},{yb:.1f} {xb:.1f},{yb:.1f}"
        return d

    line = smooth_path(pts)
    c.add(c.fade(f'<path d="{line} L{x1},{y1} L{x0},{y1} Z" fill="{C['green']}" fill-opacity="0.15"/>', 300))
    c.css.append(".draw{stroke-dasharray:2400;stroke-dashoffset:2400;animation:draw 1.8s ease-out .2s forwards}"
                 "@keyframes draw{to{stroke-dashoffset:0}}")
    c.add(f'<path class="draw" d="{line}" stroke="{C['green']}" stroke-width="2" fill="none" stroke-linejoin="round"/>')
    last_m, last_x = None, -99
    for i, (d, _) in enumerate(weeks):
        dd = dt.date.fromisoformat(d)
        if dd.month != last_m and i < len(weeks) - 2:
            last_m = dd.month
            if pts[i][0] - last_x < 40:  # partial first month: skip its label
                continue
            c.add(t(pts[i][0], y1 + 20, f"{dd:%b}", "tiny", "middle"))
            last_x = pts[i][0]
    bi = max(range(len(weeks)), key=lambda k: weeks[k][1])
    bx, by = pts[bi]
    c.add(c.fade(f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="5" fill="{C["green"]}" stroke="{C["bg"]}" stroke-width="2"/>'
                 + t(bx, by - 12, f"{n(weeks[bi][1])} · week of {fdate(weeks[bi][0], False)}", "sm",
                     "end" if bx > 700 else "middle"), 1600))
    return c.svg()


def card_stats(p, g, upd, avatar):
    c = Card(430, 214, "GitHub stats", None, upd)
    lv, score = grade({**p, "followers": g["followers"]})
    rows = [("Commits", p["commits"], "last 12 months"), ("Pull requests", p["prs"], None),
            ("Issues", p["issues"], None), ("Repositories", p["repos"], None),
            ("Stars earned", p["stars"], None), ("Followers", g["followers"], None)]
    for i, (lab, val, hint) in enumerate(rows):
        y = 66 + i * 23
        c.add(c.fade(t(24, y, lab, "lab") + (t(24 + 7.2 * len(lab) + 8, y, hint, "tiny") if hint else "")
                     + t(236, y, n(val), "val", "end"), 100 + i * 80))
    cx, cy = 334, 112
    ring = c.ring(cx, cy, 52, score, 7, C["accent"], 300)
    clip = f'<clipPath id="av"><circle cx="{cx}" cy="{cy}" r="42"/></clipPath>'
    img = (f'<image href="data:image/png;base64,{avatar}" x="{cx - 42}" y="{cy - 42}" width="84" height="84" '
           f'clip-path="url(#av)" preserveAspectRatio="xMidYMid slice"/>' if avatar else "")
    pill = (f'<rect x="{cx - 34}" y="{cy + 58}" width="68" height="24" rx="12" fill="{C["box"]}" stroke="{C["line"]}"/>'
            + t(cx, cy + 75, f"rank {lv}", "val", "middle", 'style="font-size:13px"'))
    c.add(c.fade(clip + f'<circle cx="{cx}" cy="{cy}" r="42" fill="{C["box"]}"/>' + img + ring + pill, 200))
    return c.svg()


def card_languages(p, upd):
    langs = sorted(p["languages"].items(), key=lambda kv: (-kv[1]["repos"], -kv[1]["bytes"]))[:6]
    total = sum(v["repos"] for _, v in langs) or 1
    c = Card(430, 214, "Languages", "by repository", upd)
    x, y, w = 24, 60, 382
    bar = ""
    for i, (name, v) in enumerate(langs):
        bw = w * v["repos"] / total
        bar += f'<rect x="{x:.1f}" y="{y}" width="{max(bw - 2, 1):.1f}" height="12" rx="3" fill="{v["color"]}"/>'
        x += bw
    c.add(c.fade(f'<clipPath id="lb"><rect x="24" y="{y}" width="{w}" height="12" rx="6"/></clipPath><g clip-path="url(#lb)">{bar}</g>', 150))
    for i, (name, v) in enumerate(langs):
        col, row = i % 2, i // 2
        lx, ly = 24 + col * 196, 104 + row * 30
        pct = 100 * v["repos"] / total
        c.add(c.fade(f'<circle cx="{lx + 6}" cy="{ly - 4}" r="6" fill="{v["color"]}"/>'
                     + t(lx + 20, ly, name, "val") + t(lx + 182, ly, f"{pct:.0f}%", "lab", "end"), 250 + i * 90))
    return c.svg()


def card_rhythm(s, upd):
    c = Card(430, 214, "Weekly rhythm", "by weekday", upd)
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    vals = s["by_weekday"]
    top = max(1, max(vals))
    bw, gap, x0, base, hmax = 38, 15, 30, 178, 104
    for i, v in enumerate(vals):
        h = max(3, hmax * v / top)
        x = x0 + i * (bw + gap)
        col = HEAT[4] if v == top else HEAT[2]
        self_css = f"g{i}"
        c.css.append(f".{self_css}{{transform-origin:{x + bw / 2}px {base}px;animation:grow{i} .9s ease-out {150 + i * 90}ms both}}"
                     f"@keyframes grow{i}{{from{{transform:scaleY(0)}}to{{transform:scaleY(1)}}}}")
        c.add(f'<rect class="{self_css}" x="{x}" y="{base - h:.1f}" width="{bw}" height="{h:.1f}" rx="6" fill="{col}"/>')
        c.add(c.fade(t(x + bw / 2, base - h - 8, n(v), "sm", "middle"), 500 + i * 90))
        c.add(t(x + bw / 2, base + 18, names[i], "lab", "middle"))
    return c.svg()


def card_month(s, upd):
    c = Card(430, 214, "Last 30 days", None, upd)

    def delta(a, b):
        if not b:
            return ("new", C["green"])
        pct = (a - b) / b * 100
        return (f"{'+' if pct >= 0 else ''}{pct:.0f}% vs previous 30", C["green"] if pct >= 0 else C["red"])

    d1, c1 = delta(s["m_total"], s["m_prev"])
    c.add(c.fade(t(24, 66, "Contributions", "lab") + t(24, 98, n(s["m_total"]), "big")
                 + t(24, 118, d1, "sm", extra=f'style="fill:{c1}"'), 100))
    c.add(c.fade(t(232, 66, "Active days", "lab") + t(232, 98, f"{s['m_active']}/30", "big"), 200))
    if s["m_best_date"]:
        c.add(c.fade(t(232, 118, f"best: {s['m_best']} on {fdate(s['m_best_date'], False)}", "sm"), 260))
    vals = [v for _, v in s["last30"]]
    top = max(1, max(vals))
    bw = 382 / len(vals)
    bars = "".join(
        f'<rect x="{24 + i * bw:.1f}" y="{186 - max(2, 46 * v / top):.1f}" width="{bw - 3:.1f}" height="{max(2, 46 * v / top):.1f}" rx="2" '
        f'fill="{HEAT[0] if v == 0 else (HEAT[4] if v == top else HEAT[2])}"/>' for i, v in enumerate(vals))
    c.add(c.fade(bars, 350))
    return c.svg()


def card_leetcode(l, upd):
    c = Card(430, 214, "LeetCode", f"@{USER}", upd)
    e, m, h = l["solved"].get("Easy", 0), l["solved"].get("Medium", 0), l["solved"].get("Hard", 0)
    tot = e + m + h
    cx, cy, r = 100, 122, 52
    circ = 2 * math.pi * r
    segs, acc = "", 0.0
    for val, col in ((e, C["green"]), (m, C["yellow"]), (h, C["red"])):
        if not val:
            continue
        frac = val / tot
        L = circ * frac
        segs += (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{col}" stroke-width="10" '
                 f'stroke-dasharray="{max(L - 4, 1):.2f} {circ:.2f}" stroke-dashoffset="{-acc:.2f}" '
                 f'transform="rotate(-90 {cx} {cy})" stroke-linecap="round"/>')
        acc += L
    ring = f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="{C["track"]}" stroke-width="10"/>' + segs
    c.add(c.fade(ring + t(cx, cy + 6, n(tot), "big", "middle") + t(cx, cy + 26, "solved", "sm", "middle"), 150))
    for i, (lab, val, col) in enumerate((("Easy", e, C["green"]), ("Medium", m, C["yellow"]), ("Hard", h, C["red"]))):
        y = 76 + i * 40
        totq = l["total"].get(lab, 1)
        w = 230 * min(1, val / totq * 8)  # 8x zoom so early progress is visible
        c.add(c.fade(t(180, y, lab, "lab", extra=f'style="fill:{col}"') + t(410, y, f"{val} / {n(totq)}", "lab", "end")
                     + f'<rect x="180" y="{y + 8}" width="230" height="7" rx="3.5" fill="{C["track"]}"/>'
                     + (f'<rect x="180" y="{y + 8}" width="{max(w, 7):.1f}" height="7" rx="3.5" fill="{col}"/>' if val else ""),
                     200 + i * 100))
    meta = f"{l['active_days']} active days · best streak {l['streak']}" + (f" · {l['language']}" if l.get("language") else "")
    c.add(c.fade(t(180, 196, meta, "tiny"), 500))
    return c.svg()


def card_thm(m, upd):
    tier = (m.get("leagueTier") or "").capitalize()
    c = Card(430, 214, "TryHackMe", f"{m.get('rank') or ''} · {tier} league".strip(" ·"), upd)
    cx, cy = 92, 120
    hexa = " ".join(f"{cx + 54 * math.cos(math.radians(a)):.1f},{cy + 54 * math.sin(math.radians(a)):.1f}" for a in range(-90, 270, 60))
    c.add(c.fade(f'<polygon points="{hexa}" fill="{C["box"]}" stroke="{C["line"]}" stroke-width="2" stroke-linejoin="round"/>'
                 + t(cx, cy - 8, "LEVEL", "sm", "middle", 'style="letter-spacing:2px"')
                 + t(cx, cy + 24, m.get("level", "—"), "big", "middle", 'style="font-size:34px"'), 150))
    stats = [("Points", m.get("totalPoints")), ("Rooms", m.get("completedRoomsNumber")),
             ("Badges", m.get("badgesNumber")), ("Best streak", m.get("largestStreak"))]
    for i, (lab, val) in enumerate(stats):
        x, y = 184 + (i % 2) * 120, 82 + (i // 2) * 58
        c.add(c.fade(t(x, y, lab, "lab") + t(x, y + 28, n(val if val is not None else "—"), "mid"), 250 + i * 90))
    if m.get("dateSignUp"):
        c.add(t(184, 196, f"hacking since {fdate(m['dateSignUp'])}", "tiny"))
    return c.svg()


def card_chess(ch, upd):
    c = Card(880, 226, "Chess.com", f"@{USER} · {ch.get('league') or ''} league".replace(" ·  league", ""), upd)
    icons = {"Blitz": "⚡", "Rapid": "⏱", "Bullet": "•", "Daily": "☀"}
    modes = list(ch["modes"].items())
    tw = 880 / 5
    for i, (name, m) in enumerate(modes[:4]):
        x = tw * i + 24
        games = m["win"] + m["loss"] + m["draw"] or 1
        wpx, dpx = 128 * m["win"] / games, 128 * m["draw"] / games
        g = (t(x, 72, f"{icons.get(name, '')} {name}", "lab") + t(x, 106, m["rating"], "big")
             + t(x, 126, f"best {m['best']}" if m.get("best") else "", "sm")
             + f'<rect x="{x}" y="146" width="128" height="8" rx="4" fill="{C["red"]}"/>'
             + f'<rect x="{x}" y="146" width="{wpx + dpx:.1f}" height="8" rx="4" fill="{C["gray"]}"/>'
             + f'<rect x="{x}" y="146" width="{wpx:.1f}" height="8" rx="4" fill="{C["green"]}"/>'
             + t(x, 172, f"{n(m['win'])}W · {n(m['loss'])}L · {n(m['draw'])}D", "sm")
             + t(x, 190, f"{100 * m['win'] / games:.0f}% wins · {n(games)} games", "tiny"))
        if i:
            c.add(f'<line x1="{tw * i:.0f}" y1="58" x2="{tw * i:.0f}" y2="196" stroke="{C["line"]}"/>')
        c.add(c.fade(g, 120 + i * 110))
    x = tw * 4 + 24
    c.add(f'<line x1="{tw * 4:.0f}" y1="58" x2="{tw * 4:.0f}" y2="196" stroke="{C["line"]}"/>')
    c.add(c.fade(t(x, 72, "🧩 Puzzles peak", "lab") + t(x, 106, ch.get("puzzles") or "—", "big")
                 + t(x, 140, "Puzzle Rush best", "lab") + t(x, 164, ch.get("rush") or "—", "mid")
                 + t(x, 190, f"{ch.get('followers', 0)} followers", "tiny"), 560))
    return c.svg()


def card_typing(lines):
    """Monospace typewriter that cycles through lines (SMIL, works in <img>)."""
    W, H, fs = 880, 64, 22
    cw = fs * 0.6
    tch, hold, dch, gap = 0.07, 1.8, 0.035, 0.35
    segs, tt = [], 0.0
    for ln in lines:
        k = len(ln)
        segs.append((tt, k))
        tt += k * tch + hold + k * dch + gap
    T = tt
    parts = []
    cur_times, cur_x = [0.0], [W / 2]
    for idx, (ln, (s, k)) in enumerate(zip(lines, segs)):
        x0 = (W - k * cw) / 2
        times, widths = [0.0], [0]
        steps = [(s + j * tch, j) for j in range(1, k + 1)]
        steps += [(s + k * tch + hold + j * dch, k - j) for j in range(1, k + 1)]
        for tm, j in steps:
            times.append(tm / T)
            widths.append(j * cw)
            cur_times.append(tm / T)
            cur_x.append(x0 + j * cw)
        times.append(1.0)
        widths.append(0)
        kt = ";".join(f"{v:.5f}" for v in times)
        vw = ";".join(f"{v:.1f}" for v in widths)
        parts.append(f'<clipPath id="c{idx}"><rect x="{x0:.1f}" y="0" width="0" height="{H}">'
                     f'<animate attributeName="width" values="{vw}" keyTimes="{kt}" dur="{T:.2f}s" calcMode="discrete" repeatCount="indefinite"/></rect></clipPath>')
        parts.append(f'<text x="{x0:.1f}" y="{H / 2 + fs * 0.35:.1f}" clip-path="url(#c{idx})" textLength="{k * cw:.1f}" '
                     f'lengthAdjust="spacingAndGlyphs" style="font:600 {fs}px {MONO};fill:{C["text"]}">{escape(ln)}</text>')
    order = sorted(zip(cur_times, cur_x))
    kt = ";".join(f"{a:.5f}" for a, _ in order) + ";1"
    vx = ";".join(f"{b:.1f}" for _, b in order) + f";{order[-1][1]:.1f}"
    parts.append(f'<rect y="{H / 2 - fs * 0.55:.1f}" width="2.5" height="{fs * 1.1:.1f}" fill="{C["accent"]}" x="{W / 2}">'
                 f'<animate attributeName="x" values="{vx}" keyTimes="{kt}" dur="{T:.2f}s" calcMode="discrete" repeatCount="indefinite"/>'
                 f'<animate attributeName="opacity" values="1;0;1" dur="0.9s" repeatCount="indefinite"/></rect>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img">'
            + "".join(parts) + "</svg>\n")


TYPING = [
    "Hi, I'm Alexey Belkin",
    "16 y/o self-taught developer",
    "Flutter · Python · C# · AI tools",
    "judo on the mat, code at the desk",
    "such code. much wow.",
]


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

    # name.svg is the dark-theme card, name-light.svg the light-theme one.
    for theme, suffix in (("dark", ""), ("light", "-light")):
        use_theme(theme)
        if "github" in data:
            g = data["github"]
            s = cal_stats(g["days"])
            u = g["updated"]
            write(f"calendar{suffix}.svg", card_calendar(g, u))
            write(f"habits{suffix}.svg", card_rings(s, u))
            write(f"highlights{suffix}.svg", card_highlights(g, s, u))
            write(f"activity{suffix}.svg", card_activity(g["days"], u))
            write(f"rhythm{suffix}.svg", card_rhythm(s, u))
            write(f"month{suffix}.svg", card_month(s, u))
            if "github_private" in data:
                p = data["github_private"]
                write(f"stats{suffix}.svg", card_stats(p, g, p["updated"], avatar))
                write(f"languages{suffix}.svg", card_languages(p, p["updated"]))
        if "leetcode" in data:
            write(f"leetcode{suffix}.svg", card_leetcode(data["leetcode"], data["leetcode"]["updated"]))
        if "chess" in data:
            write(f"chess{suffix}.svg", card_chess(data["chess"], data["chess"]["updated"]))
        if "tryhackme" in data:
            write(f"tryhackme{suffix}.svg", card_thm(data["tryhackme"], data["tryhackme"]["updated"]))
        write(f"typing{suffix}.svg", card_typing(TYPING))
    json.dump(data, open(data_path, "w", encoding="utf-8"), indent=1)


if __name__ == "__main__":
    main()
