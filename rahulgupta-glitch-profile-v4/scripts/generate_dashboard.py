#!/usr/bin/env python3
"""
Builds two self-hosted SVG images for the GitHub profile README:

  assets/heatmap.svg    LeetCode-style activity card: active days, streaks,
                        a level/XP bar and a 12-month contribution heatmap
  assets/languages.svg  Pie/donut chart of the languages used across all
                        public repositories

Only the Python standard library is used. Data comes from the GitHub GraphQL
API (the GITHUB_TOKEN that GitHub Actions provides is enough).

Preview locally with fake data (no token needed):
    python3 scripts/generate_dashboard.py --demo
"""
import html
import json
import math
import os
import random
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

PROFILE_USER = os.environ.get("PROFILE_USER", "rahulgupta-glitch")
TOKEN = os.environ.get("GITHUB_TOKEN", "")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.environ.get("OUT_DIR", os.path.join(ROOT, "assets"))

# ---------------------------- easy settings --------------------------------
HIDE_LANGUAGES = {"Jupyter Notebook"}   # left out of the pie chart; use set() to show everything
MAX_LANGUAGES = 7        # smaller languages are grouped into "Other"
HOLE = 0.55              # 0 = full pie chart, 0.55 = donut
# ---------------------------------------------------------------------------

BG, PANEL, BORDER = "#0d1117", "#161b22", "#30363d"
TEXT, MUTED, ACCENT = "#e6edf3", "#8b949e", "#58a6ff"
LEVEL_COLORS = ["#161b22", "#0e4429", "#006d32", "#26a641", "#39d353"]
FALLBACK = ["#58a6ff", "#a371f7", "#f778ba", "#ffa657", "#3fb950", "#79c0ff", "#d29922"]
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
            weeks.append({"contributionDays": week}); week = []
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


# ------------------------------- drawing -----------------------------------
def esc(s):
    return html.escape(str(s), quote=True)


def svg_open(w, h, title):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" role="img" aria-label="{esc(title)}">'
            f'<title>{esc(title)}</title>'
            f'<style>text{{font-family:{FONT}}}'
            f'.c{{animation:pop .6s ease-out backwards}}'
            f'@keyframes pop{{from{{opacity:0}}to{{opacity:1}}}}</style>'
            f'<rect x="1" y="1" width="{w - 2}" height="{h - 2}" rx="14" fill="{BG}" stroke="{BORDER}"/>')


def heatmap_svg(total, weeks):
    cur, best, active, span = streaks(weeks)
    W, H, PAD, CELL, GAP = 900, 338, 24, 12, 3
    PITCH = CELL + GAP
    x0, y0 = PAD + 32, 188
    out = [svg_open(W, H, "Contribution activity for the last 12 months")]

    # title + level / XP bar (a small RPG touch)
    level = int(math.sqrt(total))
    lo, hi = level ** 2, (level + 1) ** 2
    prog = (total - lo) / (hi - lo) if hi > lo else 0
    out.append(f'<text x="{PAD}" y="38" font-size="17" font-weight="700" fill="{TEXT}">'
               f'Contribution Activity<tspan font-size="12" font-weight="400" fill="{MUTED}" dx="10">'
               f'last 12 months</tspan></text>')
    bar_w, bar_x = 190, W - PAD - 190
    out.append(f'<text x="{W - PAD}" y="30" text-anchor="end" font-size="13" font-weight="700" '
               f'fill="{ACCENT}" letter-spacing="1.5">LEVEL {level}</text>')
    out.append(f'<rect x="{bar_x}" y="37" width="{bar_w}" height="7" rx="3.5" fill="#21262d"/>')
    out.append(f'<rect x="{bar_x}" y="37" width="{max(bar_w * prog, 7):.1f}" height="7" rx="3.5" fill="{ACCENT}"/>')
    out.append(f'<text x="{W - PAD}" y="60" text-anchor="end" font-size="11" fill="{MUTED}">'
               f'{total:,} / {hi:,} XP to next level</text>')

    # LeetCode-style stat tiles
    tiles = [("Total contributions", f"{total:,}", ""),
             ("Active days", str(active), f"of {span}"),
             ("Current streak", str(cur), "days"),
             ("Longest streak", str(best), "days")]
    tw = (W - 2 * PAD - 3 * 12) / 4
    for i, (label, value, unit) in enumerate(tiles):
        x = PAD + i * (tw + 12)
        out.append(f'<rect x="{x:.1f}" y="74" width="{tw:.1f}" height="82" rx="10" fill="{PANEL}" stroke="{BORDER}"/>')
        unit_svg = f'<tspan font-size="13" font-weight="400" fill="{MUTED}" dx="6">{esc(unit)}</tspan>' if unit else ""
        out.append(f'<text x="{x + 18:.1f}" y="118" font-size="32" font-weight="700" fill="{TEXT}">{esc(value)}{unit_svg}</text>')
        out.append(f'<text x="{x + 18:.1f}" y="141" font-size="12" fill="{MUTED}">{esc(label)}</text>')

    # month labels
    prev, last = None, -10
    for ci, week in enumerate(weeks):
        month = week[0]["date"][5:7]
        if month != prev:
            if ci - last >= 3:
                name = datetime.strptime(week[0]["date"], "%Y-%m-%d").strftime("%b")
                out.append(f'<text x="{x0 + ci * PITCH}" y="{y0 - 8}" font-size="10" fill="{MUTED}">{name}</text>')
                last = ci
            prev = month
    for row, name in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        out.append(f'<text x="{PAD}" y="{y0 + row * PITCH + 10}" font-size="10" fill="{MUTED}">{name}</text>')

    # the heatmap itself
    for ci, week in enumerate(weeks):
        for d in week:
            out.append(f'<rect class="c" style="animation-delay:{ci * 18}ms" x="{x0 + ci * PITCH}" '
                       f'y="{y0 + d["weekday"] * PITCH}" width="{CELL}" height="{CELL}" rx="2.5" '
                       f'fill="{LEVEL_COLORS[d["level"]]}"/>')

    # legend + footer
    ly = H - 22
    out.append(f'<text x="{PAD}" y="{ly + 9}" font-size="10" fill="#6e7681">'
               f'Updated {datetime.now(timezone.utc):%d %b %Y} UTC &#183; built by GitHub Actions</text>')
    lx = W - PAD - 142
    out.append(f'<text x="{lx}" y="{ly + 10}" font-size="10" fill="{MUTED}">Less</text>')
    for i, color in enumerate(LEVEL_COLORS):
        out.append(f'<rect x="{lx + 30 + i * PITCH}" y="{ly}" width="{CELL}" height="{CELL}" rx="2.5" fill="{color}"/>')
    out.append(f'<text x="{lx + 30 + 5 * PITCH + 3}" y="{ly + 10}" font-size="10" fill="{MUTED}">More</text>')
    out.append("</svg>")
    return "\n".join(out)


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
        out.append(f'<text x="{W / 2}" y="{H / 2}" text-anchor="middle" font-size="14" fill="{MUTED}">No language data yet</text>')
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
        out.append(f'<circle cx="{x}" cy="{y}" r="6" fill="{color or FALLBACK[i % len(FALLBACK)]}"/>')
        out.append(f'<text x="{x + 18}" y="{y + 5}" font-size="14" fill="{TEXT}">{esc(name)}</text>')
        out.append(f'<text x="{x + 220}" y="{y + 5}" text-anchor="end" font-size="14" font-weight="700" fill="{MUTED}">{pct:.1f}%</text>')

    out.append(f'<text x="{PAD}" y="{H - 20}" font-size="12" fill="{MUTED}">'
               f'{repos} public repo{"" if repos == 1 else "s"} &#183; {stars} star{"" if stars == 1 else "s"} '
               f'&#183; {followers} follower{"" if followers == 1 else "s"}</text>')
    if HIDE_LANGUAGES:
        out.append(f'<text x="{W - PAD}" y="{H - 20}" text-anchor="end" font-size="11" fill="#6e7681">'
                   f'Excludes: {esc(", ".join(sorted(HIDE_LANGUAGES)))}</text>')
    out.append("</svg>")
    return "\n".join(out)


def main():
    user = demo_user() if "--demo" in sys.argv else fetch_user()
    total, weeks = parse_calendar(user)
    os.makedirs(OUT_DIR, exist_ok=True)
    for name, svg in (("heatmap.svg", heatmap_svg(total, weeks)),
                      ("languages.svg", languages_svg(user))):
        with open(os.path.join(OUT_DIR, name), "w", encoding="utf-8") as f:
            f.write(svg + "\n")
        print("wrote", os.path.join(OUT_DIR, name))


if __name__ == "__main__":
    main()
