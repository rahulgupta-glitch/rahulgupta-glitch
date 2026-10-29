#!/usr/bin/env python3
"""
Builds the self-hosted images for the GitHub profile README (standard library only):

  assets/skyline.svg         3D contribution skyline: one bar per day, plus active
                             days, streaks and a level/XP bar
  assets/languages.svg       pie/donut chart of languages across all public repos
  assets/projects/<slug>.svg one creative card per project in data/projects.json

Contribution and language data come from the GitHub GraphQL API (the GITHUB_TOKEN
that GitHub Actions provides is enough). The project cards need no network at all.

Preview locally with fake data (no token needed):
    python3 scripts/generate_dashboard.py --demo
Rebuild only the project cards:
    python3 scripts/generate_dashboard.py --projects-only
"""
import html
import json
import math
import os
import random
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

PROFILE_USER = os.environ.get("PROFILE_USER", "rahulgupta-glitch")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.environ.get("OUT_DIR", os.path.join(ROOT, "assets"))
PROJECTS_FILE = os.path.join(ROOT, "data", "projects.json")

# ---------------------------- easy settings --------------------------------
HIDE_LANGUAGES = {"Jupyter Notebook"}   # left out of the pie chart; use set() to show all
MAX_LANGUAGES = 7                       # smaller languages are grouped into "Other"
HOLE = 0.55                             # 0 = full pie chart, 0.55 = donut
# ---------------------------------------------------------------------------

BG, PANEL, BORDER = "#0d1117", "#161b22", "#30363d"
TEXT, MUTED, ACCENT = "#e6edf3", "#8b949e", "#fb923c"
SKY = ["#1e293b", "#38bdf8", "#818cf8", "#c084fc", "#fb923c"]   # skyline level colours
FONT = "-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
LEVELS = {"NONE": 0, "FIRST_QUARTILE": 1, "SECOND_QUARTILE": 2,
          "THIRD_QUARTILE": 3, "FOURTH_QUARTILE": 4}

QUERY = """
query($login: String!) {
  user(login: $login) {
    followers { totalCount }
    repositories(first: 100, ownerAffiliations: OWNER, isFork: false, privacy: PUBLIC) {
      totalCount
      nodes {
        stargazerCount
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name color } }
        }
      }
    }
    contributionsCollection {
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount contributionLevel weekday } }
      }
    }
  }
}
"""


# ------------------------------- data --------------------------------------
def fetch_user():
    body = json.dumps({"query": QUERY, "variables": {"login": PROFILE_USER}}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql", data=body,
        headers={"Authorization": f"bearer {TOKEN}", "Content-Type": "application/json",
                 "User-Agent": "profile-dashboard"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.load(resp)
    if data.get("errors") or not (data.get("data") or {}).get("user"):
        sys.exit(f"GitHub API error: {json.dumps(data)[:500]}")
    return data["data"]["user"]


def demo_user():
    rnd = random.Random(7)
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=364)
    start -= timedelta(days=(start.weekday() + 1) % 7)       # back to a Sunday
    weeks, week, d = [], [], start
    while d <= today:
        busy = 0.55 if (d.month in (9, 10, 11) or d.month == 3) else 0.12
        n = rnd.choice([1, 2, 3, 4, 6, 9, 12]) if rnd.random() < busy else 0
        lvl = 0 if n == 0 else 1 if n < 3 else 2 if n < 6 else 3 if n < 10 else 4
        week.append({"date": d.isoformat(), "contributionCount": n,
                     "contributionLevel": list(LEVELS)[lvl], "weekday": (d.weekday() + 1) % 7})
        if len(week) == 7:
            weeks.append({"contributionDays": week})
            week = []
        d += timedelta(days=1)
    if week:
        weeks.append({"contributionDays": week})
    total = sum(x["contributionCount"] for w in weeks for x in w["contributionDays"])

    def lang(name, color, size):
        return {"size": size, "node": {"name": name, "color": color}}
    return {
        "followers": {"totalCount": 2},
        "repositories": {"totalCount": 12, "nodes": [
            {"stargazerCount": 1, "languages": {"edges": [
                lang("Jupyter Notebook", "#DA5B0B", 700000), lang("Python", "#3572A5", 120000),
                lang("Java", "#b07219", 60000), lang("HTML", "#e34c26", 9000)]}}]},
        "contributionsCollection": {"contributionCalendar": {
            "totalContributions": total, "weeks": weeks}},
    }


def parse_calendar(user):
    cal = user["contributionsCollection"]["contributionCalendar"]
    weeks = [[{"date": d["date"], "count": d["contributionCount"],
               "level": LEVELS.get(d["contributionLevel"], 0), "weekday": d["weekday"]}
              for d in w["contributionDays"]] for w in cal["weeks"]]
    return cal["totalContributions"], weeks


def streaks(weeks):
    days = sorted((d for w in weeks for d in w), key=lambda d: d["date"])
    best = run = 0
    for d in days:
        run = run + 1 if d["count"] > 0 else 0
        best = max(best, run)
    i = len(days) - 1
    if i >= 0 and days[i]["count"] == 0:      # today may still be in progress
        i -= 1
    cur = 0
    while i >= 0 and days[i]["count"] > 0:
        cur += 1
        i -= 1
    active = sum(1 for d in days if d["count"] > 0)
    return cur, best, active, len(days)


def language_slices(user):
    totals, colors = {}, {}
    for repo in user["repositories"]["nodes"]:
        for e in repo["languages"]["edges"]:
            name = e["node"]["name"]
            if name in HIDE_LANGUAGES:
                continue
            totals[name] = totals.get(name, 0) + e["size"]
            colors[name] = e["node"]["color"] or "#8b949e"
    ranked = sorted(totals.items(), key=lambda kv: -kv[1])
    grand = sum(v for _, v in ranked) or 1
    items = [(n, v / grand * 100, colors[n]) for n, v in ranked[:MAX_LANGUAGES]]
    rest = sum(v for _, v in ranked[MAX_LANGUAGES:])
    if rest:
        items.append(("Other", rest / grand * 100, "#6e7681"))
    return items


# ------------------------------ drawing helpers ----------------------------
def esc(s):
    return html.escape(str(s), quote=True)


def shade(hex_color, factor):
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02x%02x%02x" % (min(255, int(r * factor)), min(255, int(g * factor)), min(255, int(b * factor)))


def svg_open(w, h, title, css="", frame=True):
    out = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
           f'viewBox="0 0 {w} {h}" role="img" aria-label="{esc(title)}">'
           f'<title>{esc(title)}</title><style>text{{font-family:{FONT}}}{css}</style>')
    if frame:
        out += f'<rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="14" fill="{BG}" stroke="{BORDER}"/>'
    return out


def day_label(iso):
    d = datetime.strptime(iso, "%Y-%m-%d")
    return f"{d.strftime('%b')} {d.day}"


# ----------------------------- 3D skyline ----------------------------------
def skyline_svg(total, weeks):
    cur, best, active, span = streaks(weeks)
    W, H, PAD = 900, 432, 24
    P, BW = 15, 11              # column pitch, bar width
    SX, DY = 5, 15              # sideways shift and screen height per row (depth)
    BX, BY = 5, -5              # depth vector of each bar
    MAXH = 62
    x0, y_back = 36, 262
    ncols = len(weeks)
    all_days = [d for w in weeks for d in w]
    mx = max((d["count"] for d in all_days), default=0) or 1

    css = (".b{animation:rise .7s ease-out backwards}"
           "@keyframes rise{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:none}}")
    out = [svg_open(W, H, "3D contribution skyline for the last 12 months", css)]

    # title + level / XP bar
    level = int(math.sqrt(total))
    lo, hi = level ** 2, (level + 1) ** 2
    prog = (total - lo) / (hi - lo) if hi > lo else 0
    out.append(f'<text x="{PAD}" y="38" font-size="17" font-weight="700" fill="{TEXT}">'
               f'Contribution Skyline<tspan font-size="12" font-weight="400" fill="{MUTED}" dx="10">'
               f'last 12 months &#183; every bar is one day</tspan></text>')
    bar_w, bar_x = 190, W - PAD - 190
    out.append(f'<text x="{W - PAD}" y="30" text-anchor="end" font-size="13" font-weight="700" '
               f'fill="{ACCENT}" letter-spacing="1.5">LEVEL {level}</text>')
    out.append(f'<rect x="{bar_x}" y="37" width="{bar_w}" height="7" rx="3.5" fill="#21262d"/>')
    out.append(f'<rect x="{bar_x}" y="37" width="{max(bar_w * prog, 7):.1f}" height="7" rx="3.5" fill="{ACCENT}"/>')
    out.append(f'<text x="{W - PAD}" y="60" text-anchor="end" font-size="11" fill="{MUTED}">'
               f'{total:,} / {hi:,} XP to next level</text>')

    # stat tiles
    tiles = [("Total contributions", f"{total:,}", ""),
             ("Active days", str(active), f"of {span}"),
             ("Current streak", str(cur), "days"),
             ("Longest streak", str(best), "days")]
    tw = (W - 2 * PAD - 3 * 12) / 4
    for i, (label, value, unit) in enumerate(tiles):
        x = PAD + i * (tw + 12)
        out.append(f'<rect x="{x:.1f}" y="74" width="{tw:.1f}" height="80" rx="10" fill="{PANEL}" stroke="{BORDER}"/>')
        unit_svg = (f'<tspan font-size="13" font-weight="400" fill="{MUTED}" dx="6">{esc(unit)}</tspan>'
                    if unit else "")
        out.append(f'<text x="{x + 18:.1f}" y="116" font-size="32" font-weight="700" fill="{TEXT}">{esc(value)}{unit_svg}</text>')
        out.append(f'<text x="{x + 18:.1f}" y="140" font-size="12" fill="{MUTED}">{esc(label)}</text>')

    # the stage (floor plate + slab edge)
    fl, fr = x0 - 8, x0 + ncols * P + 4
    bl, br = x0 + 6 * SX - 8, x0 + 6 * SX + ncols * P + BX + 6
    y_front = y_back + 6 * DY + 8
    y_rear = y_back + BY - 8
    out.append('<defs><linearGradient id="floor" x1="0" y1="0" x2="0" y2="1">'
               '<stop offset="0" stop-color="#0b1324"/><stop offset="1" stop-color="#152238"/>'
               '</linearGradient></defs>')
    out.append(f'<polygon points="{fl},{y_front} {fr},{y_front} {fr},{y_front + 8} {fl},{y_front + 8}" '
               f'fill="#080d18" stroke="#1e293b"/>')
    out.append(f'<polygon points="{bl},{y_rear} {br},{y_rear} {fr},{y_front} {fl},{y_front}" '
               f'fill="url(#floor)" stroke="#334155"/>')

    # geometry for each day, grouped by row so back rows are drawn first
    rows = [[] for _ in range(7)]
    for ci, week in enumerate(weeks):
        for d in week:
            rows[d["weekday"]].append((ci, d))

    def origin(ci, r):
        return x0 + ci * P + (6 - r) * SX, y_back + r * DY

    flat = []
    for r in range(7):                                    # empty days: flat tiles on the floor
        for ci, d in rows[r]:
            if d["count"] == 0:
                fx, fy = origin(ci, r)
                flat.append(f'<polygon points="{fx},{fy} {fx + BW},{fy} {fx + BW + BX},{fy + BY} {fx + BX},{fy + BY}"/>')
    out.append(f'<g fill="{SKY[0]}" stroke="#0b1324" stroke-width=".6">{"".join(flat)}</g>')

    peak = None
    for r in range(7):                                    # active days: 3D bars, back to front
        for ci, d in rows[r]:
            if d["count"] == 0:
                continue
            fx, fy = origin(ci, r)
            h = 3 + (d["count"] / mx) ** 0.6 * (MAXH - 3)
            color = SKY[max(1, d["level"])]
            top, front, side = color, shade(color, 0.72), shade(color, 0.5)
            ty = fy - h
            out.append(
                f'<g class="b" style="animation-delay:{ci * 14}ms">'
                f'<rect x="{fx}" y="{ty:.1f}" width="{BW}" height="{h:.1f}" fill="{front}"/>'
                f'<polygon points="{fx + BW},{fy} {fx + BW + BX},{fy + BY} {fx + BW + BX},{ty + BY:.1f} {fx + BW},{ty:.1f}" fill="{side}"/>'
                f'<polygon points="{fx},{ty:.1f} {fx + BW},{ty:.1f} {fx + BW + BX},{ty + BY:.1f} {fx + BX},{ty + BY:.1f}" fill="{top}"/>'
                f'</g>')
            if peak is None or d["count"] >= peak[0]:
                peak = (d["count"], d["date"], fx + BW / 2 + BX / 2, ty + BY)

    if peak:                                              # "best day" tag floats above the skyline
        count, date, px, py = peak
        label = f"Best day: {count} on {day_label(date)}"
        lw = len(label) * 6.6 + 20
        lx = min(max(px - lw / 2, PAD), W - PAD - lw)
        pill_y = 164
        out.append(f'<line x1="{px:.1f}" y1="{pill_y + 22}" x2="{px:.1f}" y2="{py - 2:.1f}" stroke="{ACCENT}" '
                   f'stroke-width="1.2" stroke-dasharray="2 3" opacity=".8"/>')
        out.append(f'<circle cx="{px:.1f}" cy="{py - 2:.1f}" r="2.8" fill="{ACCENT}"/>')
        out.append(f'<rect x="{lx:.1f}" y="{pill_y}" width="{lw:.1f}" height="22" rx="11" fill="#1c1208" stroke="{ACCENT}"/>')
        out.append(f'<text x="{lx + lw / 2:.1f}" y="{pill_y + 15}" text-anchor="middle" font-size="11" font-weight="600" fill="#fdba74">{esc(label)}</text>')

    # month labels along the front edge
    prev, last = None, -10
    for ci, week in enumerate(weeks):
        month = week[0]["date"][5:7]
        if month != prev:
            if ci - last >= 3:
                name = datetime.strptime(week[0]["date"], "%Y-%m-%d").strftime("%b")
                out.append(f'<text x="{x0 + ci * P}" y="{y_front + 28}" font-size="10" fill="{MUTED}">{name}</text>')
                last = ci
            prev = month

    # legend + footer
    ly = H - 24
    out.append(f'<text x="{PAD}" y="{ly + 9}" font-size="10" fill="#6e7681">'
               f'Updated {datetime.now(timezone.utc):%d %b %Y} UTC &#183; built by GitHub Actions</text>')
    lx = W - PAD - 142
    out.append(f'<text x="{lx}" y="{ly + 10}" font-size="10" fill="{MUTED}">Less</text>')
    for i, color in enumerate(SKY):
        out.append(f'<rect x="{lx + 30 + i * 15}" y="{ly}" width="12" height="12" rx="2.5" fill="{color}"/>')
    out.append(f'<text x="{lx + 30 + 5 * 15 + 3}" y="{ly + 10}" font-size="10" fill="{MUTED}">More</text>')
    out.append("</svg>")
    return "\n".join(out)


# ------------------------------ languages ----------------------------------
def slice_path(cx, cy, R, r, a0, a1):
    large = 1 if a1 - a0 > math.pi else 0
    p = lambda rad, a: (cx + rad * math.cos(a), cy + rad * math.sin(a))
    (x0, y0), (x1, y1) = p(R, a0), p(R, a1)
    if r > 0:
        (xi1, yi1), (xi0, yi0) = p(r, a1), p(r, a0)
        return (f"M{x0:.2f},{y0:.2f} A{R},{R} 0 {large} 1 {x1:.2f},{y1:.2f} "
                f"L{xi1:.2f},{yi1:.2f} A{r},{r} 0 {large} 0 {xi0:.2f},{yi0:.2f} Z")
    return f"M{cx},{cy} L{x0:.2f},{y0:.2f} A{R},{R} 0 {large} 1 {x1:.2f},{y1:.2f} Z"


def languages_svg(user):
    W, H, PAD = 900, 300, 24
    items = language_slices(user)
    repos = user["repositories"]["totalCount"]
    stars = sum(r["stargazerCount"] for r in user["repositories"]["nodes"])
    followers = user["followers"]["totalCount"]
    out = [svg_open(W, H, "Languages used across my public repositories")]
    out.append(f'<text x="{PAD}" y="38" font-size="17" font-weight="700" fill="{TEXT}">'
               f'Languages Across My Repositories<tspan font-size="12" font-weight="400" fill="{MUTED}" dx="10">'
               f'share of code by size</tspan></text>')

    cx, cy, R = 185, 160, 92
    r = round(R * HOLE)
    if not items:
        out.append(f'<text x="{W / 2}" y="{H / 2 - 4}" text-anchor="middle" font-size="15" fill="{TEXT}">Language chart loading</text>')
        out.append(f'<text x="{W / 2}" y="{H / 2 + 20}" text-anchor="middle" font-size="12" fill="{MUTED}">'
                   f'It is built automatically by GitHub Actions after the first workflow run.</text>')
    elif len(items) == 1:
        out.append(f'<circle cx="{cx}" cy="{cy}" r="{R}" fill="{items[0][2]}"/>')
        if r:
            out.append(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{BG}"/>')
    else:
        angle = -math.pi / 2
        for name, pct, color in items:
            sweep = pct / 100 * 2 * math.pi
            out.append(f'<path d="{slice_path(cx, cy, R, r, angle, angle + sweep)}" fill="{color}" '
                       f'stroke="{BG}" stroke-width="2"/>')
            angle += sweep
    if items and r:
        out.append(f'<text x="{cx}" y="{cy + 4}" text-anchor="middle" font-size="30" font-weight="700" fill="{TEXT}">{len(items)}</text>')
        out.append(f'<text x="{cx}" y="{cy + 24}" text-anchor="middle" font-size="11" fill="{MUTED}">languages</text>')

    for i, (name, pct, color) in enumerate(items):
        col, row = divmod(i, 4)
        x, y = (380 if len(items) > 4 else 440) + col * 250, 98 + row * 40
        out.append(f'<circle cx="{x}" cy="{y}" r="6" fill="{color}"/>')
        out.append(f'<text x="{x + 18}" y="{y + 5}" font-size="14" fill="{TEXT}">{esc(name)}</text>')
        out.append(f'<text x="{x + 220}" y="{y + 5}" text-anchor="end" font-size="14" font-weight="700" fill="{MUTED}">{pct:.1f}%</text>')

    if items:
        out.append(f'<text x="{PAD}" y="{H - 20}" font-size="12" fill="{MUTED}">'
                   f'{repos} public repo{"" if repos == 1 else "s"} &#183; {stars} star{"" if stars == 1 else "s"} '
                   f'&#183; {followers} follower{"" if followers == 1 else "s"}</text>')
        if HIDE_LANGUAGES:
            out.append(f'<text x="{W - PAD}" y="{H - 20}" text-anchor="end" font-size="11" fill="#6e7681">'
                       f'Excludes: {esc(", ".join(sorted(HIDE_LANGUAGES)))}</text>')
    out.append("</svg>")
    return "\n".join(out)


# ----------------------------- project cards -------------------------------
def icon(name, c):
    s = f'stroke="{c}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"'   # stroke only
    n = s + ' fill="none"'                                                                  # stroke, no fill
    dots = lambda pts, r=2: "".join(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{c}"/>' for x, y in pts)
    if name == "spark":
        return f'<path d="M16 3 L19.5 12.5 L29 16 L19.5 19.5 L16 29 L12.5 19.5 L3 16 L12.5 12.5 Z" {s} fill="{c}" fill-opacity=".25"/>'
    if name == "network":
        return (f'<g {n}><line x1="8" y1="9" x2="24" y2="9"/><line x1="8" y1="9" x2="16" y2="24"/>'
                f'<line x1="24" y1="9" x2="16" y2="24"/></g>{dots([(8, 9), (24, 9), (16, 24)], 3.4)}')
    if name == "trend":
        return f'<line x1="4" y1="25" x2="28" y2="7" {s}/>' + dots([(7, 21), (12, 18), (16, 19), (21, 12), (26, 9)])
    if name == "shield":
        return (f'<path d="M16 3 L27 7 V15 C27 22 22 27 16 29 C10 27 5 22 5 15 V7 Z" {s} fill="{c}" fill-opacity=".18"/>'
                f'<path d="M11 16 L15 20 L22 12" {n}/>')
    if name == "bars":
        return "".join(f'<rect x="{x}" y="{28 - h}" width="6" height="{h}" rx="1.5" fill="{c}" fill-opacity="{o}"/>'
                       for x, h, o in ((5, 10, .55), (13, 17, .8), (21, 24, 1)))
    if name == "line":
        return f'<polyline points="4,24 11,15 17,20 28,6" {n}/>' + dots([(4, 24), (11, 15), (17, 20), (28, 6)], 2.4)
    if name == "clusters":
        return (dots([(8, 9), (12, 12), (7, 14)], 2.4)
                + f'<g fill="{c}" fill-opacity=".7">' + "".join(f'<circle cx="{x}" cy="{y}" r="2.4"/>' for x, y in ((23, 8), (26, 12), (21, 13))) + "</g>"
                + f'<g fill="{c}" fill-opacity=".45">' + "".join(f'<circle cx="{x}" cy="{y}" r="2.4"/>' for x, y in ((16, 23), (12, 26), (20, 26))) + "</g>")
    if name == "pie":
        return (f'<circle cx="16" cy="16" r="11" {n}/><path d="M16 16 L16 5 A11 11 0 0 1 27 16 Z" fill="{c}" fill-opacity=".6"/>')
    if name == "cup":
        return (f'<path d="M6 12 H22 V20 C22 24.5 18.5 27 14 27 C9.5 27 6 24.5 6 20 Z" {n}/>'
                f'<path d="M22 14 H25 C27.5 14 27.5 20 25 20 H22" {n}/><path d="M10 4 V8 M14 3 V8 M18 4 V8" {n}/>')
    if name == "braces":
        return f'<text x="16" y="22" text-anchor="middle" font-size="18" font-weight="700" font-family="monospace" fill="{c}">{{ }}</text>'
    return f'<circle cx="16" cy="16" r="10" {n}/>'


def wrap(text, width, max_lines):
    lines, cur = [], ""
    for word in text.split():
        if len(cur) + len(word) + (1 if cur else 0) <= width:
            cur = f"{cur} {word}".strip()
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(".,; ") + "..."
    return lines


def project_card(p, index):
    W, H = 440, 212
    c = p.get("color", "#58a6ff")
    css = (".card{animation:up .6s ease-out backwards}"
           "@keyframes up{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}")
    out = [svg_open(W, H, f'{p["name"]} project card', css, frame=False)]
    out.append(f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#18203a"/>'
               f'<stop offset="1" stop-color="{BG}"/></linearGradient>'
               f'<linearGradient id="a" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{c}"/>'
               f'<stop offset="1" stop-color="{c}" stop-opacity="0"/></linearGradient>'
               f'<clipPath id="k"><rect x="4" y="4" width="424" height="196" rx="16"/></clipPath></defs>')
    out.append(f'<g class="card" style="animation-delay:{index * 90}ms">')
    out.append('<rect x="10" y="12" width="424" height="196" rx="16" fill="#05080f"/>')           # 3D slab
    out.append(f'<rect x="4" y="4" width="424" height="196" rx="16" fill="url(#g)" stroke="{c}" stroke-opacity=".45"/>')
    out.append(f'<g clip-path="url(#k)"><circle cx="410" cy="10" r="95" fill="{c}" opacity=".10"/>'
               f'<rect x="4" y="4" width="424" height="4" fill="url(#a)"/></g>')
    out.append(f'<rect x="28" y="28" width="56" height="56" rx="14" fill="{c}" fill-opacity=".14" stroke="{c}" stroke-opacity=".5"/>')
    out.append(f'<g transform="translate(40,40)">{icon(p.get("icon", ""), c)}</g>')
    out.append(f'<text x="100" y="44" font-size="11" font-weight="600" letter-spacing="1.6" fill="{c}">{esc(p.get("category", "").upper())}</text>')
    out.append(f'<text x="100" y="70" font-size="20" font-weight="700" fill="#ffffff">{esc(p["name"])}</text>')
    if p.get("program"):
        out.append(f'<text x="412" y="44" text-anchor="end" font-size="10" letter-spacing="1.2" fill="{MUTED}">{esc(p["program"].upper())}</text>')
    for i, line in enumerate(wrap(p.get("description", ""), 54, 3)):
        out.append(f'<text x="28" y="{108 + i * 18}" font-size="13" fill="#c9d1d9">{esc(line)}</text>')
    x = 28
    for tag in p.get("tech", [])[:4]:
        w = len(tag) * 6.8 + 22
        out.append(f'<rect x="{x:.1f}" y="160" width="{w:.1f}" height="24" rx="12" fill="{c}" fill-opacity=".14" stroke="{c}" stroke-opacity=".45"/>')
        out.append(f'<text x="{x + w / 2:.1f}" y="176" text-anchor="middle" font-size="11" font-weight="600" fill="{shade(c, 1.25)}">{esc(tag)}</text>')
        x += w + 8
    out.append(f'<text x="412" y="176" text-anchor="end" font-size="10" font-weight="700" letter-spacing="1.2" fill="{c}">OPEN REPO  &#8594;</text>')
    out.append("</g></svg>")
    return "\n".join(out)


def write_projects():
    with open(PROJECTS_FILE, encoding="utf-8") as f:
        projects = json.load(f)
    folder = os.path.join(OUT_DIR, "projects")
    os.makedirs(folder, exist_ok=True)
    for i, p in enumerate(projects):
        slug = p.get("slug") or re.sub(r"[^a-z0-9]+", "-", p["name"].lower()).strip("-")
        path = os.path.join(folder, f"{slug}.svg")
        with open(path, "w", encoding="utf-8") as f:
            f.write(project_card(p, i) + "\n")
        print("wrote", path)


# --------------------------------- main -------------------------------------
def write(name, svg):
    with open(os.path.join(OUT_DIR, name), "w", encoding="utf-8") as f:
        f.write(svg + "\n")
    print("wrote", os.path.join(OUT_DIR, name))


def main():
    args = sys.argv[1:]
    os.makedirs(OUT_DIR, exist_ok=True)
    write_projects()                       # local data only, never depends on the network
    if "--projects-only" in args:
        return
    user = demo_user() if "--demo" in args else fetch_user()
    total, weeks = parse_calendar(user)
    write("skyline.svg", skyline_svg(total, weeks))
    write("languages.svg", languages_svg(user))


if __name__ == "__main__":
    main()
