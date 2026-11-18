"""Turn weekly counts into mountain ridgelines (pure geometry, no SVG)."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

Point = tuple[float, float]


class Noise:
    """Deterministic hash noise: same seed + same key -> same value in [-1, 1].

    Keyed by position rather than drawn from a stream, so the terrain only
    changes where the data changes.
    """

    def __init__(self, seed: str):
        self.seed = seed.encode()

    def __call__(self, *key: object) -> float:
        h = hashlib.blake2b(self.seed + repr(key).encode(), digest_size=8).digest()
        return int.from_bytes(h, "big") / 2**63 - 1.0


def scale_counts(counts: list[int], mode: str = "sqrt") -> list[float]:
    """Compress counts so one huge week doesn't flatten everything; -> [0, 1]."""
    f = math.sqrt if mode == "sqrt" else math.log1p
    vals = [f(max(c, 0)) for c in counts]
    top = max(vals) or 1.0
    return [v / top for v in vals]


def smooth(values: list[float], window: int) -> list[float]:
    """Centered rolling average with triangular weights; renormalized to [0, 1]."""
    if window <= 1:
        return list(values)
    half = window // 2
    n = len(values)
    out = []
    for i in range(n):
        acc = wsum = 0.0
        for j in range(max(0, i - half), min(n, i + half + 1)):
            w = half + 1 - abs(i - j)
            acc += values[j] * w
            wsum += w
        out.append(acc / wsum)
    top = max(out) or 1.0
    return [v / top for v in out]


def percentile(values: list[float], p: float) -> float:
    s = sorted(values)
    if not s:
        return 0.0
    k = (len(s) - 1) * p / 100
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


@dataclass
class Peak:
    index: int
    value: float
    prominence: float
    left_valley: int
    right_valley: int


def find_peaks(values: list[float]) -> list[Peak]:
    """Local maxima with topographic prominence and bounding valleys."""
    n = len(values)
    peaks = []
    for i, v in enumerate(values):
        left_ok = i == 0 or v > values[i - 1]
        right_ok = i == n - 1 or v >= values[i + 1]
        if not (left_ok and right_ok):
            continue
        l = i - 1
        while l >= 0 and values[l] <= v:
            l -= 1
        r = i + 1
        while r < n and values[r] <= v:
            r += 1
        lmin = min(values[l + 1 : i + 1])
        rmin = min(values[i : r])
        # nearest valley on each side (for facets and snow extent)
        lv = i
        while lv > 0 and values[lv - 1] <= values[lv]:
            lv -= 1
        rv = i
        while rv < n - 1 and values[rv + 1] <= values[rv]:
            rv += 1
        peaks.append(Peak(i, v, v - max(lmin, rmin), lv, rv))
    return peaks


@dataclass
class Ridge:
    ctrl: list[Point]  # one control point per week (exact data positions)
    points: list[Point]  # full jagged outline, left to right

    def y_at(self, x: float) -> float:
        pts = self.points
        if x <= pts[0][0]:
            return pts[0][1]
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            if x1 <= x <= x2:
                t = (x - x1) / (x2 - x1) if x2 != x1 else 0
                return y1 + (y2 - y1) * t
        return pts[-1][1]

    def min_y_between(self, x0: float, x1: float) -> float:
        ys = [y for x, y in self.points if x0 <= x <= x1]
        ys += [self.y_at(x0), self.y_at(x1)]
        return min(ys)


def displace(a: Point, b: Point, depth: int, rough: float, noise: Noise, key: tuple) -> list[Point]:
    """Midpoint displacement between a and b; returns points from a up to (not incl.) b."""
    pts = [a, b]
    for level in range(depth):
        nxt = [pts[0]]
        for j, ((x1, y1), (x2, y2)) in enumerate(zip(pts, pts[1:])):
            seg = math.hypot(x2 - x1, y2 - y1)
            mx = (x1 + x2) / 2 + noise(*key, level, j, "x") * 0.12 * (x2 - x1)
            my = (y1 + y2) / 2 + noise(*key, level, j, "y") * rough * seg
            nxt += [(mx, my), (x2, y2)]
        pts = nxt
    return pts[:-1]


def build_ridge(
    values: list[float],
    *,
    width: float,
    top: float,
    floor: float,
    rough: float,
    depth: int,
    noise: Noise,
    layer: str,
    shift: float = 0.0,
    jitter: float = 0.22,
    saddle: float = 0.0,
) -> Ridge:
    n = len(values)
    dx = width / n
    ctrl: list[Point] = []
    for i, v in enumerate(values):
        x = (i + 0.5 + shift) * dx + noise(layer, "jx", i) * jitter * dx
        # zero weeks sit on an uneven valley floor, never a flat baseline
        y = floor - v * (floor - top) + noise(layer, "floor", i) * 3.5 * (1 - v)
        ctrl.append((x, y))
    # extend past both edges so the silhouette never starts mid-air
    ext = [(ctrl[0][0] - 2 * dx, ctrl[0][1] + 8 + noise(layer, "l") * 6)]
    ext += ctrl
    ext.append((ctrl[-1][0] + 2 * dx, ctrl[-1][1] + 8 + noise(layer, "r") * 6))
    # a saddle between neighbouring weeks turns every week into its own summit
    if saddle:
        withs = [ext[0]]
        for k, (a, b) in enumerate(zip(ext, ext[1:])):
            lower = max(a[1], b[1])
            dip = saddle * (0.45 + 0.55 * (noise(layer, "sad", k) + 1) / 2)
            sy = lower + dip * max(floor + 6 - lower, 4)
            sx = (a[0] + b[0]) / 2 + noise(layer, "sadx", k) * 0.18 * (b[0] - a[0])
            withs += [(sx, sy), b]
        ext = withs
    pts: list[Point] = []
    for k, (a, b) in enumerate(zip(ext, ext[1:])):
        pts += displace(a, b, depth, rough, noise, (layer, k))
    pts.append(ext[-1])
    return Ridge(ctrl, pts)


def couloir(apex: Point, foot: Point, noise: Noise, key: tuple) -> list[Point]:
    """Jagged shadow edge running from a summit down to the base."""
    pts = [apex, foot]
    for level in range(4):
        nxt = [pts[0]]
        for j, ((x1, y1), (x2, y2)) in enumerate(zip(pts, pts[1:])):
            seg = math.hypot(x2 - x1, y2 - y1)
            mx = (x1 + x2) / 2 + noise(*key, level, j) * 0.13 * seg
            nxt += [(mx, (y1 + y2) / 2), (x2, y2)]
        pts = nxt
    return pts
