"""SVG rendering: sky, three ridgelines, snow, shadows, labels."""

from __future__ import annotations

import colorsys
import datetime as dt
import math
from xml.sax.saxutils import escape, quoteattr

from .terrain import Noise, Peak, Ridge, build_ridge, couloir, find_peaks, percentile, scale_counts, smooth

W, H = 800, 200
FONT = "ui-monospace, SFMono-Regular, Menlo, monospace"
SUMMITS = ["Haute Cime", "Cime de l'Est", "Dent Jaune", "La Forteresse", "La Cathédrale", "L'Éperon", "Les Doigts"]
MAX_LABELS = 5

# ---------------------------------------------------------------- time / sky


def zurich_now(now: dt.datetime | None = None) -> dt.datetime:
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        return now.astimezone(ZoneInfo("Europe/Zurich"))
    except Exception:  # no tz database: apply EU DST rules by hand
        utc = now.astimezone(dt.timezone.utc)

        def last_sunday(month: int) -> dt.datetime:
            d = dt.datetime(utc.year, month, 31, 1, tzinfo=dt.timezone.utc)
            return d - dt.timedelta(days=(d.weekday() + 1) % 7)

        summer = last_sunday(3) <= utc < last_sunday(10)
        return utc.astimezone(dt.timezone(dt.timedelta(hours=2 if summer else 1)))


def sky_state(hour: int) -> str:
    if 5 <= hour < 9:
        return "dawn"
    if 9 <= hour < 17:
        return "day"
    if 17 <= hour < 21:
        return "dusk"
    return "night"


# Light palettes. Dark mode derives muted versions via `mute`.
PALETTES = {
    "dawn": {
        "sky": [(0, "#6f7bb0"), (0.45, "#e7a6b4"), (1, "#f8cf9f")],
        "glow": ("#fff0c8", 0.82),  # colour, x position (east = right)
        "back": "#cfa7bf", "mid": "#977ba0", "front": "#433f66",
        "mist": "#f3c4b6", "snow": "#fff0ec", "shade": "#2a1a45",
    },
    "day": {
        "sky": [(0, "#8fc1e6"), (0.6, "#cfe5f3"), (1, "#eef5f8")],
        "glow": None,
        "back": "#b3cbdc", "mid": "#7896ad", "front": "#34525f",
        "mist": "#e6f0f5", "snow": "#ffffff", "shade": "#0e2433",
    },
    "dusk": {
        "sky": [(0, "#2d2150"), (0.5, "#b24a6e"), (1, "#f39243")],
        "glow": ("#ffd27a", 0.16),  # sun setting in the west
        "back": "#b4607c", "mid": "#6e3d66", "front": "#2a1f3f",
        "mist": "#f0905e", "snow": "#ffd2b4", "shade": "#150a24",
    },
    "night": {
        "sky": [(0, "#081028"), (0.7, "#16244a"), (1, "#24375f")],
        "glow": None,
        "back": "#2b3c66", "mid": "#1e2b50", "front": "#111a35",
        "mist": "#2f4470", "snow": "#c4cfe6", "shade": "#03060f",
    },
}


def _rgb(c: str) -> tuple[float, float, float]:
    c = c.lstrip("#")
    return tuple(int(c[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _hex(r: float, g: float, b: float) -> str:
    return "#" + "".join(f"{round(max(0, min(1, v)) * 255):02x}" for v in (r, g, b))


def mix(a: str, b: str, t: float) -> str:
    ra, rb = _rgb(a), _rgb(b)
    return _hex(*(x + (y - x) * t for x, y in zip(ra, rb)))


def mute(c: str) -> str:
    """Dark-mode transform: desaturate and compress lightness."""
    h, l, s = colorsys.rgb_to_hls(*_rgb(c))
    return _hex(*colorsys.hls_to_rgb(h, 0.07 + l * 0.6, s * 0.45))


def luminance(c: str) -> float:
    r, g, b = _rgb(c)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def palette(state: str, dark: bool) -> dict:
    p = dict(PALETTES[state])
    if dark:
        p = {
            k: ([(o, mute(c)) for o, c in v] if k == "sky" else (mute(v[0]), v[1]) if k == "glow" and v else mute(v) if isinstance(v, str) else v)
            for k, v in p.items()
        }
    label_bg = mix(p["sky"][0][1], p["sky"][-1][1], 0.35)
    light_text = luminance(label_bg) < 0.45
    p["text"] = ("#e9ecf2" if not dark else "#c9ced8") if light_text else "#1d2a3a"
    p["halo"] = label_bg
    p["caption"] = "#ffffff" if not dark else "#c9ced8"
    return p


# ---------------------------------------------------------------- svg helpers


def f(v: float) -> str:
    s = f"{v:.1f}"
    return s[:-2] if s.endswith(".0") else s


def path_d(points: list[tuple[float, float]], close_bottom: bool = False) -> str:
    d = "M" + " ".join(f"{f(x)},{f(y)}" for x, y in points)
    if close_bottom:
        d += f" L{f(points[-1][0])},{H + 1} {f(points[0][0])},{H + 1}Z"
    return d


def poly(points: list[tuple[float, float]]) -> str:
    return "M" + " ".join(f"{f(x)},{f(y)}" for x, y in points) + "Z"


# ---------------------------------------------------------------- scene parts


def sky_defs(p: dict) -> str:
    stops = "".join(f'<stop offset="{o}" stop-color="{c}"/>' for o, c in p["sky"])
    out = f'<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">{stops}</linearGradient>'
    for name in ("back", "mid", "front"):
        k = {"back": 0.55, "mid": 0.4, "front": 0.18}[name]
        out += (
            f'<linearGradient id="g{name}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0.25" stop-color="{p[name]}"/>'
            f'<stop offset="1" stop-color="{mix(p[name], p["mist"], k)}"/></linearGradient>'
        )
    if p.get("glow"):
        out += (
            f'<radialGradient id="glow"><stop offset="0" stop-color="{p["glow"][0]}" stop-opacity=".9"/>'
            f'<stop offset="1" stop-color="{p["glow"][0]}" stop-opacity="0"/></radialGradient>'
        )
    return out


def night_sky(noise: Noise, dark: bool) -> str:
    parts = []
    for i in range(34):
        x = (noise("star", i, "x") + 1) / 2 * W
        y = (noise("star", i, "y") + 1) / 2 * 95 + 4
        r = 0.45 + (noise("star", i, "r") + 1) * 0.35
        o = 0.45 + (noise("star", i, "o") + 1) * 0.25
        parts.append(f'<circle cx="{f(x)}" cy="{f(y)}" r="{f(r)}" opacity="{o:.2f}"/>')
    stars = f'<g fill="#fffbe8">{"".join(parts)}</g>'
    mx = W * (0.62 + (noise("moon", "x") + 1) * 0.13)
    my = 26 + noise("moon", "y") * 6
    moon_col = "#c9c6b8" if dark else "#f4efd8"
    moon = (
        f'<mask id="crescent"><rect width="{W}" height="{H}" fill="#fff"/>'
        f'<circle cx="{f(mx + 4.2)}" cy="{f(my - 2.4)}" r="8" fill="#000"/></mask>'
        f'<circle cx="{f(mx)}" cy="{f(my)}" r="16" fill="{moon_col}" opacity=".07"/>'
        f'<circle cx="{f(mx)}" cy="{f(my)}" r="8.5" fill="{moon_col}" mask="url(#crescent)"/>'
    )
    return stars + moon


def facets(ridge: Ridge, peaks: list[Peak], noise: Noise, layer: str, min_prom: float) -> list[str]:
    """Shadowed east faces: a wedge from each summit, between a jagged couloir
    and the line from the next valley down to the base. Sun from the west."""
    out = []
    for pk in peaks:
        if pk.prominence < min_prom or pk.right_valley == pk.index:
            continue
        ax, ay = ridge.ctrl[pk.index]
        vx, vy = ridge.ctrl[pk.right_valley]
        span = vx - ax
        foot = (ax + (0.18 + 0.1 * noise(layer, "foot", pk.index)) * span, H + 2)
        edge = couloir((ax, ay), foot, noise, (layer, "c", pk.index))
        right = couloir((vx, vy), (vx - 0.1 * span, H + 2), noise, (layer, "r", pk.index))
        out.append(poly([(ax, -1), (vx, -1)] + right + edge[::-1]))
    return out


def snow_caps(ridge: Ridge, peaks: list[Peak], threshold: float, noise: Noise) -> list[str]:
    """Caps that hug the ridge: deepest at the summit, thinning down the slopes."""
    out = []
    for pk in peaks:
        if pk.value <= threshold or pk.value <= 0:
            continue
        ax, ay = ridge.ctrl[pk.index]
        lv, rv = ridge.ctrl[pk.left_valley], ridge.ctrl[pk.right_valley]
        relief = max(lv[1], rv[1]) - ay
        depth = max(6.0, min(24.0, 3 + 0.22 * relief + 10 * pk.value))
        x0 = ax - max(10.0, (ax - lv[0]) * 0.85)
        x1 = ax + max(10.0, (rv[0] - ax) * 0.85)
        steps = max(6, int((x1 - x0) / 2.2))
        edge = []
        for s in range(steps + 1):
            x = x1 - (x1 - x0) * s / steps
            reach = 1 - abs(x - ax) / ((x1 - ax) if x > ax else (ax - x0))
            band = depth * max(reach, 0) ** 0.9  # thickness below the ridge line
            drip = ((2.6 if s % 2 else -0.4) + noise("snow", pk.index, s) * 1.3) * min(1, band / 3)
            y = min(ay + depth, ridge.y_at(x) + band) + drip
            edge.append((x, y))
        out.append(poly([(x0, -1), (x1, -1)] + edge))
    return out


# ---------------------------------------------------------------- labels


def _overlaps(a, b, pad=2.0) -> bool:
    return not (a[2] + pad <= b[0] or b[2] + pad <= a[0] or a[3] + pad <= b[1] or b[3] + pad <= a[1])


def _seg_hits_box(p, q, box) -> bool:
    for t in range(0, 21):
        x = p[0] + (q[0] - p[0]) * t / 20
        y = p[1] + (q[1] - p[1]) * t / 20
        if box[0] <= x <= box[2] and box[1] <= y <= box[3]:
            return True
    return False


def choose_labels(peaks: list[Peak], counts: list[int]) -> list[Peak]:
    """Rank summits; always the top 3, then only clear standouts, max 5."""
    real = [p for p in peaks if counts[p.index] > 0]
    if not real:
        return []
    real.sort(key=lambda p: (-counts[p.index], -p.prominence, p.index))
    top = real[0]
    rest = [p for p in real[1:] if p.prominence >= 0.06]
    chosen = [top] + rest[:2]
    for p in rest[2:]:
        if len(chosen) >= MAX_LABELS:
            break
        if p.value >= 0.78 * top.value and p.prominence >= 0.12:
            chosen.append(p)
    return chosen


def place_labels(chosen, ridge: Ridge, weeks, flag_box) -> list[tuple]:
    """Greedy collision-free placement. Returns (peak, rank, box, anchor, leader_from)."""
    placed: list[tuple] = []
    obstacles = [flag_box] if flag_box else []
    leaders: list[tuple] = []
    for rank, pk in enumerate(chosen):
        ax, ay = ridge.ctrl[pk.index]
        if rank == 0 and flag_box:
            ay = flag_box[1]
        name = SUMMITS[rank]
        date, count = weeks[pk.index]
        meta = f"{date.isoformat()} · {count}"
        w = max(len(name) * 4.8, len(meta) * 3.6) + 2
        h = 16.5
        best = None
        for gap in (9, 15, 21, 28, 36, 45, 55):
            bottom = ay - gap
            top = bottom - h
            if top < 3:
                break
            for off in (0, -0.55, 0.55, -1.0, 1.0, -1.5, 1.5):
                cx = min(max(ax + off * w, w / 2 + 5), W - w / 2 - 5)
                box = (cx - w / 2, top, cx + w / 2, bottom)
                if ridge.min_y_between(box[0], box[2]) < bottom + 3:
                    continue
                lx = min(max(ax, box[0] + 3), box[2] - 3)
                start, end = (ax, ay - 1.5), (lx, bottom + 1)
                if any(_overlaps(box, o) for o in obstacles):
                    continue
                if any(_seg_hits_box(start, end, o) for o in obstacles):
                    continue
                if any(_seg_hits_box(s, e, box) for s, e in leaders):
                    continue
                best = (box, cx, start, end)
                break
            if best:
                break
        if not best:
            continue
        box, cx, start, end = best
        obstacles.append(box)
        leaders.append((start, end))
        placed.append((name, meta, box, cx, start, end))
    return placed


def label_svg(placed, p: dict) -> str:
    lines, texts = [], []
    for name, meta, box, cx, start, end in placed:
        lines.append(f"M{f(start[0])},{f(start[1])} {f(end[0])},{f(end[1])}")
        texts.append(
            f'<text x="{f(cx)}" y="{f(box[1] + 7.5)}" font-size="8" font-weight="600">{escape(name)}</text>'
            f'<text x="{f(cx)}" y="{f(box[1] + 15)}" font-size="5.6" opacity=".85">{escape(meta)}</text>'
        )
    if not placed:
        return ""
    return (
        f'<path d="{"".join(lines)}" stroke="{p["text"]}" stroke-width=".6" opacity=".7"/>'
        f'<g fill="{p["text"]}" text-anchor="middle" stroke="{p["halo"]}" stroke-opacity=".55" '
        f'stroke-width="2" stroke-linejoin="round" style="paint-order:stroke">' + "".join(texts) + "</g>"
    )


def swiss_flag(ax: float, ay: float) -> tuple[str, tuple]:
    pole_top = ay - 12
    x, y, s = ax + 0.4, pole_top, 6.4
    c = s / 5  # cross arm thickness
    svg = (
        f'<path d="M{f(ax)},{f(ay + 0.5)}V{f(pole_top)}" stroke="#f4f1ea" stroke-width=".7"/>'
        f'<rect x="{f(x)}" y="{f(y)}" width="{s}" height="{s}" fill="#d52b1e"/>'
        f'<path d="M{f(x + s/2 - c/2)},{f(y + s*0.18)}h{f(c)}v{f(s*0.64)}h{f(-c)}z'
        f'M{f(x + s*0.18)},{f(y + s/2 - c/2)}h{f(s*0.64)}v{f(c)}h{f(-s*0.64)}z" fill="#fff"/>'
    )
    return svg, (ax - 1, pole_top - 1, x + s + 1, ay)


# ---------------------------------------------------------------- main entry


def render(weeks: list[tuple[dt.date, int]], user: str, hour: int, dark: bool, scale: str = "sqrt") -> str:
    counts = [c for _, c in weeks]
    total = sum(counts)
    state = sky_state(hour)
    p = palette(state, dark)
    noise = Noise(f"dents:{user.lower()}")

    v = scale_counts(counts, scale)
    back = build_ridge(smooth(v, 9), width=W, top=64, floor=126, rough=0.42, depth=3,
                       noise=noise, layer="back", shift=-0.35, jitter=0.3)
    mid = build_ridge(smooth(v, 3), width=W, top=82, floor=148, rough=0.36, depth=3,
                      noise=noise, layer="mid", shift=0.45, saddle=0.18)
    # raw weekly summits; shoulders from a gentle envelope so peaks aren't needles
    shoulders = smooth(v, 5)
    front_v = [max(x, 0.5 * x_s * max(v)) for x, x_s in zip(v, shoulders)]
    front = build_ridge(front_v, width=W, top=70, floor=172, rough=0.3, depth=2,
                        noise=noise, layer="front", saddle=0.25)

    front_peaks = find_peaks(v)
    mid_peaks = find_peaks(smooth(v, 3))
    p75 = percentile(v, 75)

    shade = p["shade"]
    svg: list[str] = []
    svg.append("<defs>")
    svg.append(sky_defs(p))
    svg.append(f'<clipPath id="frame"><rect width="{W}" height="{H}" rx="6"/></clipPath>')
    svg.append(f'<path id="rf" d="{path_d(front.points, True)}"/>')
    svg.append(f'<path id="rm" d="{path_d(mid.points, True)}"/>')
    svg.append('<clipPath id="cf"><use href="#rf"/></clipPath><clipPath id="cm"><use href="#rm"/></clipPath>')
    svg.append("</defs>")

    svg.append('<g clip-path="url(#frame)">')
    svg.append(f'<rect width="{W}" height="{H}" fill="url(#sky)"/>')
    if p.get("glow"):
        gx = p["glow"][1] * W
        svg.append(f'<ellipse cx="{f(gx)}" cy="118" rx="210" ry="95" fill="url(#glow)"/>')
    if state == "night":
        svg.append(night_sky(noise, dark))

    svg.append(f'<path d="{path_d(back.points, True)}" fill="url(#gback)"/>')
    svg.append('<use href="#rm" fill="url(#gmid)"/>')
    mf = facets(mid, mid_peaks, noise, "mid", 0.08)
    if mf:
        svg.append(f'<path clip-path="url(#cm)" d="{"".join(mf)}" fill="{shade}" opacity=".16"/>')
    svg.append('<use href="#rf" fill="url(#gfront)"/>')

    caps = snow_caps(front, front_peaks, p75, noise)
    if caps:
        svg.append(f'<path clip-path="url(#cf)" d="{"".join(caps)}" fill="{p["snow"]}"/>')
    ff = facets(front, front_peaks, noise, "front", 0.05)
    if ff:
        svg.append(f'<path clip-path="url(#cf)" d="{"".join(ff)}" fill="{shade}" opacity=".3"/>')

    chosen = choose_labels(front_peaks, counts)
    flag_box = None
    if chosen:
        ax, ay = front.ctrl[chosen[0].index]
        flag_svg, flag_box = swiss_flag(ax, ay)
        svg.append(flag_svg)
    placed = place_labels(chosen, front, weeks, flag_box)
    svg.append(label_svg(placed, p))

    caption = f"{total:,} contributions · last {len(weeks)} weeks · {user}"
    svg.append(
        f'<text x="{W - 8}" y="{H - 7}" text-anchor="end" font-size="7" fill="{p["caption"]}" opacity=".78">{escape(caption)}</text>'
    )
    svg.append("</g>")

    title = f"dents: {caption}"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="100%" '
        f'role="img" aria-label={quoteattr(title)} font-family="{FONT}">'
        f"<title>{escape(title)}</title>" + "".join(svg) + "</svg>\n"
    )
