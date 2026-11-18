"""Contribution data: GitHub GraphQL API, or realistic fake data."""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import random
import urllib.error
import urllib.request

API_URL = "https://api.github.com/graphql"
WEEKS = 52

QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        weeks {
          firstDay
          contributionDays { date contributionCount }
        }
      }
    }
  }
}
"""

Week = tuple[dt.date, int]  # (first day of the week, total contributions)


class FetchError(RuntimeError):
    pass


def fetch_weeks(user: str, token: str | None = None) -> list[Week]:
    """Return the last 52 weeks of contributions for `user`, oldest first."""
    token = token or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise FetchError("GITHUB_TOKEN is not set (or use --fake)")

    body = json.dumps({"query": QUERY, "variables": {"login": user}}).encode()
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "dents-widget",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise FetchError(f"GitHub API returned HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise FetchError(f"Could not reach GitHub API: {e.reason}") from e

    if payload.get("errors"):
        msgs = "; ".join(err.get("message", "?") for err in payload["errors"])
        raise FetchError(f"GitHub API error: {msgs}")
    user_node = (payload.get("data") or {}).get("user")
    if user_node is None:
        raise FetchError(f"GitHub user {user!r} not found")

    weeks = user_node["contributionsCollection"]["contributionCalendar"]["weeks"]
    out: list[Week] = []
    for w in weeks:
        first = dt.date.fromisoformat(w["firstDay"])
        total = sum(d["contributionCount"] for d in w["contributionDays"])
        out.append((first, total))
    out.sort(key=lambda w: w[0])
    return out[-WEEKS:]


def _week_start(day: dt.date) -> dt.date:
    """GitHub calendar weeks start on Sunday."""
    return day - dt.timedelta(days=(day.weekday() + 1) % 7)


def fake_weeks(user: str, seed: int | None = None, today: dt.date | None = None) -> list[Week]:
    """Plausible developer activity: seasonal drift, crunch weeks, holidays."""
    rng = random.Random(f"dents-fake:{user}:{seed}")
    today = today or dt.date.today()
    last = _week_start(today)
    first = last - dt.timedelta(weeks=WEEKS - 1)

    base = rng.uniform(10, 28)
    phase = rng.uniform(0, 2 * math.pi)
    holidays: set[int] = set()
    for _ in range(rng.randint(1, 3)):
        start = rng.randrange(WEEKS)
        holidays.update(range(start, start + rng.randint(1, 3)))

    out: list[Week] = []
    level = 1.0
    for i in range(WEEKS):
        level = 0.7 * level + 0.3 * rng.lognormvariate(0, 0.45)  # momentum
        lam = base * level * (1 + 0.4 * math.sin(2 * math.pi * i / 30 + phase))
        if rng.random() < 0.07:
            lam *= rng.uniform(2.5, 5.0)  # crunch / hackathon week
        if i in holidays:
            lam *= rng.choice([0, 0, 0.1])
        count = max(0, round(rng.gauss(lam, math.sqrt(lam) + 1))) if lam > 0 else 0
        out.append((first + dt.timedelta(weeks=i), count))
    return out
