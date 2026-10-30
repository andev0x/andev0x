#!/usr/bin/env python3
"""Render assets/telemetry.svg.

Sources, in order of preference:

  1. /users/{owner}/events/public — recent public activity, one repo-scoped token
  2. local git history            — offline fallback, same output shape

No third-party services, no API keys, no dependencies outside the standard
library. GitHub exposes at most 300 public events, which is fewer than 30 days
for an active account, so the window shrinks to whatever was actually observed
and every label follows it. Better a true 14d than a fabricated 30d.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

MAX_DAYS = 30
MAX_PAGES = 3  # GitHub serves 300 events, full stop
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "assets" / "telemetry.svg"


def parse_day(stamp: str) -> date:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(timezone.utc).date()


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout


def shape(
    per_day: Counter, start: date, days: int, repos: int | None, source: str, stat: str
) -> dict:
    series = [per_day.get(start + timedelta(days=i), 0) for i in range(days)]
    return {
        "series": series,
        "days": days,
        "stat": stat,
        "total": sum(series),
        "active": sum(1 for count in series if count),
        "repos": repos,
        "source": source,
    }


def from_events(owner: str, token: str | None) -> dict | None:
    """Read public push events. Returns None so the caller can fall back."""
    if not token:
        return None

    records: list[tuple[date, str]] = []
    floor = datetime.now(timezone.utc).date() - timedelta(days=MAX_DAYS - 1)

    for page in range(1, MAX_PAGES + 1):
        request = urllib.request.Request(
            f"https://api.github.com/users/{owner}/events/public?per_page=100&page={page}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "andev0x-telemetry",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                batch = json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            print(f"events api unavailable: {exc}", file=sys.stderr)
            return None

        if not batch:
            break

        for event in batch:
            if event.get("type") != "PushEvent":
                continue
            stamp = event.get("created_at")
            if not stamp:
                continue
            records.append((parse_day(stamp), (event.get("repo") or {}).get("name", "")))

        oldest = batch[-1].get("created_at")
        if oldest and parse_day(oldest) <= floor:
            break

    today = datetime.now(timezone.utc).date()
    observed = min((day for day, _ in records), default=today)
    days = min(MAX_DAYS, max(1, (today - observed).days + 1))
    start = today - timedelta(days=days - 1)

    per_day: Counter = Counter()
    repos: set[str] = set()
    for day, repo in records:
        if day >= start:
            per_day[day] += 1
            repos.add(repo)

    # The events payload is not guaranteed to carry per-push commit counts
    # (private-repo pushes omit them entirely), so the unit stays "pushes".
    return shape(per_day, start, days, len(repos), "github events", "pushes")


def from_git(start: date, days: int) -> dict:
    """Offline fallback: count non-bot commits straight out of the object store."""
    per_day: Counter = Counter()
    try:
        raw = git("log", "--no-merges", "--date=short", "--pretty=format:%cs%x1f%ae")
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"git history unavailable: {exc}", file=sys.stderr)
        return shape(per_day, start, days, None, "git history", "commits")

    for line in raw.splitlines():
        if "\x1f" not in line:
            continue
        day_stamp, author = line.split("\x1f", 1)
        if "[bot]" in author.lower():
            continue
        per_day[date.fromisoformat(day_stamp)] += 1

    return shape(per_day, start, days, None, "git history", "commits")


def render(stats: dict, synced: datetime) -> str:
    series = stats["series"]
    days = stats["days"]
    peak = max(series) or 1

    left, right = 26.0, 854.0
    base, ceiling = 172.0, 40.0
    step = (right - left) / len(series)
    width = step * 0.52

    bars = []
    for index, count in enumerate(series):
        # sqrt compression: one busy day should not flatten the rest of the strip
        height = max(ceiling * (count / peak) ** 0.6, 1.5)
        x = left + index * step + (step - width) / 2
        y = base - height
        zero = "" if count else " zero"
        bars.append(
            f'<rect class="bar{zero}" x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" '
            f'height="{height:.1f}" rx="1.5" style="animation-delay:{index * 0.018:.2f}s"/>'
        )

    tiles = [
        (f"{stats['stat']} · {days}d", f"{stats['total']:,}"),
        (f"active days · {days}d", str(stats["active"])),
        ("repos touched", "—" if stats["repos"] is None else str(stats["repos"])),
        ("runner cost", "$0.00"),
    ]

    tile_markup = "".join(
        f'<text class="k" x="{26 + index * 207}" y="62">{label}</text>'
        f'<text class="v" x="{26 + index * 207}" y="92">{value}</text>'
        for index, (label, value) in enumerate(tiles)
    )

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="880" height="208" viewBox="0 0 880 208" role="img">
  <title>Repository telemetry</title>
  <style>
    /* The card paints its own panel, so contrast does not depend on the viewer's
       GitHub theme: #e6edf3 16.0:1 · #8b949e 6.2:1 · #3fb950 7.5:1 against
       #0d1117. Green is reserved for values, bars and live state. */
    text {{
      font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
      fill: #8b949e;
    }}
    .title {{ font-size: 13px; font-weight: 600; fill: #e6edf3; }}
    .k     {{ font-size: 10px; letter-spacing: 0.14em; text-transform: uppercase; fill: #8b949e; }}
    .v     {{ font-size: 22px; fill: #3fb950; font-variant-numeric: tabular-nums; }}
    .mono  {{ font-size: 10.5px; fill: #8b949e; }}
    .live  {{ fill: #3fb950; }}

    .dot {{ fill: #3fb950; animation: pulse 2.6s ease-in-out infinite; }}
    @keyframes pulse {{
      0%, 100% {{ opacity: 0.3; }}
      50%      {{ opacity: 1; }}
    }}

    .bar {{
      fill: #3fb950;
      opacity: 0.9;
      transform-box: fill-box;
      transform-origin: bottom;
      animation: grow 0.55s cubic-bezier(0.2, 0.8, 0.2, 1) backwards;
    }}
    .bar.zero {{ fill: #30363d; opacity: 1; }}
    @keyframes grow {{
      from {{ transform: scaleY(0); opacity: 0; }}
    }}

    @media (prefers-reduced-motion: reduce) {{
      .dot, .bar {{ animation: none; }}
    }}
  </style>

  <rect x="0.5" y="0.5" width="879" height="207" rx="12" fill="#0d1117" stroke="#30363d"/>

  <circle class="dot" cx="26" cy="24" r="4"/>
  <text class="title" x="40" y="28">system.telemetry</text>
  <text class="k live" x="854" y="27" text-anchor="end">live</text>

  {tile_markup}

  <text class="k" x="26" y="124">activity cadence · {days}d</text>
  {"".join(bars)}
  <line x1="26" y1="172.5" x2="854" y2="172.5" stroke="#30363d" stroke-width="1"/>

  <text class="mono" x="26" y="192">synced {synced:%Y-%m-%d %H:%M} UTC · daily cadence · source: {stats["source"]}</text>
  <text class="mono" x="854" y="192" text-anchor="end">no external apis</text>
</svg>
'''


def main() -> int:
    now = datetime.now(timezone.utc)
    start = now.date() - timedelta(days=MAX_DAYS - 1)

    owner = os.environ.get("GITHUB_REPOSITORY", "andev0x/andev0x").split("/")[0]
    stats = from_events(owner, os.environ.get("GITHUB_TOKEN")) or from_git(start, MAX_DAYS)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(render(stats, now), encoding="utf-8")

    print(
        f"telemetry: {stats['total']} {stats['stat']} over {stats['days']}d · "
        f"{stats['active']} active days · {stats['repos']} repos · "
        f"peak {max(stats['series'])}/day"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
