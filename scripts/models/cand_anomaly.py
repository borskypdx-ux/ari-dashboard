#!/usr/bin/env python3
"""Candidate "anomaly": refined climatology + anomaly, blended with a growth profile.

Everything is in log space, on a HOLIDAY-ADJUSTED series X = log(ARI) - holiday effect:

1. Holiday effects (Czech calendar, computed from dates only):
   * public holidays outside Christmas (1.5., 8.5., 5.+6.7., 28.9., 28.10., 17.11.,
     Easter Monday, Good Friday since 2016): effect = beta * (# holiday weekdays in
     the week).  beta is estimated inside predict() from `hist` as the
     recency-weighted mean of  x_t - (x_{t-1} + x_{t+1})/2  per holiday weekday
     (weeks whose neighbours are holiday-free).  It grows from about -0.08 (WHO
     era) to about -0.15 (SZU era), which the recency weighting picks up.
   * Christmas (weeks whose Monday falls on 15.12.-4.1.): each past season's
     dip is measured against a line through its reference weeks (Mondays 1.-14.12.
     and 5.-18.1.).  For the current season the effect for a given week is a
     kernel average over past seasons, matching the week's Monday date (days
     relative to 24.12.).  This handles the calendar shifts (W52 vs W53 vs W1),
     which ISO-week climatology cannot.
2. Climatology c[w]: recency-weighted (half-life `half_life` seasons) trimmed mean of
   X over past non-COVID seasons per ISO week (W53 -> W52, current season
   excluded), then smoothed [a, 1-2a, a] across neighbouring weeks.
   Growth profile g[w]: the same robust aggregate of weekly growth X[w+1]-X[w].
3. Anomaly: a0 = weighted mean of X - c over the last k weeks (weights decay**j).
   Long-run anomaly aL = mean of X - c over the last L = 104 weeks (non-COVID weeks):
   two years average out the biennial alternation and absorb level shifts
   (post-COVID baseline, change of data source).
4. Paths for target t = origin + h:
     A_h = c[t] + aL + (a0 - aL) * rho**h                  (climatology + anomaly)
     B_h = X[origin] + sum of g over the weeks up to t     (growth profile)
   Geometric blend  wA_h * A_h + (1 - wA_h) * B_h, where wA goes linearly from wA1
   (h=1) to wA8 (h=H).  Then the holiday effect of the target week is added back.

All parameters were tuned on the tune split (seasons 2012-2018) only, choosing
values from flat regions of a random search and coordinate search.  COVID
seasons 2019-2021 are not used for climatology, growth profile, holiday effects
or the long-run anomaly.  Pure stdlib, deterministic (inputs are sorted before
any summation), reads nothing but `hist`.

Contract: predict(hist, origin, H) -> [log ARI for weeks origin+1 .. origin+H].
"""
import math
from datetime import date, timedelta
from functools import lru_cache

COVID_SEASONS = (2019, 2020, 2021)

DEFAULTS = dict(
    half_life=4.0,    # recency weighting of past seasons in the climatology (seasons)
    trim=0.2,         # trimmed weight fraction per tail, level climatology
    g_trim=0.2,       # ... growth profile
    smooth=0.2,       # neighbour weight a in the [a, 1-2a, a] smoothing of the climatology
    min_n=1,          # min. number of seasons for a climatology week (fallback: neighbours)
    k=4,              # recent weeks in the anomaly estimate
    decay=0.4,        # weight of week j back = decay**j
    rho=0.7,          # decay of the anomaly towards the long-run anomaly (rho**h)
    lam=1.0,          # share of the long-run anomaly that persists (0 = revert to climatology)
    L=104,            # window (weeks) of the long-run anomaly
    wA1=0.4,          # weight of the climatology path at h=1 ...
    wA8=1.0,          # ... and at h=H (linear in between); the rest is the growth profile path
    hol=2,            # holiday adjustment: 0 none, 1 public holidays, 2 + Christmas calendar
    hol_hl=3.0,       # half-life (seasons) of the public-holiday effect estimate
    hol_prior=0.08,   # prior |log effect| per holiday weekday (used with hol_n0 > 0)
    hol_n0=0.0,       # prior pseudo-count (holiday weekdays)
    xm_sigma=3.0,     # kernel width (days) for matching Christmas weeks by Monday date
    xm_hl=6.0,        # half-life (seasons) of the Christmas effects
)


# ── weeks ───────────────────────────────────────────────────────────────────
@lru_cache(maxsize=None)
def _wi(y, w):
    """Linear week index (Monday ordinal // 7)."""
    return date.fromisocalendar(y, w, 1).toordinal() // 7


@lru_cache(maxsize=None)
def _pos(i):
    """(season, position 0..51) of week index i; ISO W53 shares the position of W52."""
    iy, iw, _ = date.fromordinal(i * 7 + 4).isocalendar()
    S = iy if iw >= 27 else iy - 1
    return S, ((52 if iw == 53 else iw) - 27) % 52


# ── Czech calendar ──────────────────────────────────────────────────────────
@lru_cache(maxsize=None)
def _easter(y):
    a = y % 19; b = y // 100; c = y % 100; d = b // 4; e = b % 4
    f = (b + 8) // 25; g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30; i = c // 4; k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7; m = (a + 11 * h + 22 * l) // 451
    mo = (h + l - 7 * m + 114) // 31; da = ((h + l - 7 * m + 114) % 31) + 1
    return date(y, mo, da)


@lru_cache(maxsize=None)
def _pub_holidays(y):
    """Public holidays outside the Christmas window (24.12.-1.1. is handled separately)."""
    hs = {date(y, 5, 1), date(y, 5, 8), date(y, 7, 5), date(y, 7, 6),
          date(y, 9, 28), date(y, 10, 28), date(y, 11, 17)}
    e = _easter(y)
    hs.add(e + timedelta(1))            # Easter Monday
    if y >= 2016:
        hs.add(e - timedelta(2))        # Good Friday (public holiday since 2016)
    return hs


@lru_cache(maxsize=None)
def _npub(i):
    """Number of public-holiday weekdays (Mon-Fri) in week i."""
    m = date.fromordinal(i * 7 + 1)
    return sum(1 for d in range(5) if (m + timedelta(d)) in _pub_holidays((m + timedelta(d)).year))


@lru_cache(maxsize=None)
def _xmas(i):
    """Week with Monday on 15.12.-4.1.: (Christmas year, Monday offset in days from 24.12.)."""
    m = date.fromordinal(i * 7 + 1)
    if m.month == 12 and m.day >= 15:
        return m.year, (m - date(m.year, 12, 24)).days
    if m.month == 1 and m.day <= 4:
        return m.year - 1, (m - date(m.year - 1, 12, 24)).days
    return None


@lru_cache(maxsize=None)
def _xmas_weeks(Y):
    """Christmas of year Y: (pre reference weeks, post reference weeks, window weeks)."""
    pre, post, win = [], [], []
    i = date(Y, 11, 24).toordinal() // 7
    for j in range(i, i + 10):
        m = date.fromordinal(j * 7 + 1)
        if m.year == Y and m.month == 12 and m.day <= 14:
            pre.append(j)
        elif m.year == Y + 1 and m.month == 1 and 5 <= m.day <= 18:
            post.append(j)
        elif _xmas(j) is not None:
            win.append(j)
    return tuple(pre), tuple(post), tuple(win)


# ── robust aggregation ──────────────────────────────────────────────────────
def _wtrim(pairs, trim):
    """Weighted trimmed mean of [(weight, x)]; `trim` = weight fraction cut per tail."""
    pairs = sorted(pairs, key=lambda t: (t[1], t[0]))
    tot = sum(w for w, _ in pairs)
    if tot <= 0:
        return None
    lo, hi = trim * tot, (1 - trim) * tot
    acc = sw = sx = 0.0
    for w, x in pairs:
        ov = min(acc + w, hi) - max(acc, lo)
        acc += w
        if ov > 0:
            sw += ov
            sx += ov * x
    if sw <= 0:                      # trim ~ 0.5: weighted median
        acc = 0.0
        for w, x in pairs:
            acc += w
            if acc >= tot / 2:
                return x
    return sx / sw


# ── model ───────────────────────────────────────────────────────────────────
def forecast(hist, origin, H, P=None):
    P = dict(DEFAULTS, **(P or {}))
    LI = {}
    for (y, w), v in sorted(hist.items()):        # sorted: order-independent float sums
        if v and v > 0:
            LI[_wi(y, w)] = math.log(max(v, 1.0))
    i0 = _wi(*origin)
    prev = [i for i in LI if i <= i0]
    if not prev:
        return [0.0] * H
    i_last = max(prev)                             # = origin unless the origin week is missing
    S0 = _pos(i0)[0]
    SI = {i: _pos(i)[0] for i in LI}

    def rw(S, hl):
        return 0.5 ** ((S0 - S) / hl) if hl > 0 else 1.0

    # ── holiday effects ──────────────────────────────────────────────────
    hol = P["hol"]
    beta = -P["hol_prior"]
    if hol >= 1:
        num = den = 0.0
        for i, x in LI.items():
            n = _npub(i)
            if not n or SI[i] in COVID_SEASONS or _xmas(i):
                continue
            a, b = LI.get(i - 1), LI.get(i + 1)
            if a is None or b is None or _npub(i - 1) or _npub(i + 1) or _xmas(i - 1) or _xmas(i + 1):
                continue
            wgt = rw(SI[i], P["hol_hl"])
            num += wgt * (x - (a + b) / 2)
            den += wgt * n
        n0 = P["hol_n0"]
        if den + n0 > 0:
            beta = (num - n0 * P["hol_prior"]) / (den + n0)

    xeff_own = {}      # week index -> Christmas effect measured in its own (complete) season
    xm_samples = []    # (season, Monday offset, effect)
    if hol >= 2:
        for Y in sorted({SI[i] for i in LI}):
            if Y in COVID_SEASONS:
                continue
            pre, post, win = _xmas_weeks(Y)
            pv = [LI[j] for j in pre if j in LI]
            qv = [LI[j] for j in post if j in LI]
            if len(pv) < 2 or len(qv) < 2 or not all(j in LI for j in win):
                continue
            ip, iq = sum(pre) / len(pre), sum(post) / len(post)
            rp, rq = sum(pv) / len(pv), sum(qv) / len(qv)
            for j in win:
                e = LI[j] - (rp + (rq - rp) * (j - ip) / (iq - ip))
                xeff_own[j] = e
                xm_samples.append((Y, _xmas(j)[1], e))
    xm_cache = {}

    def xm_hat(off):
        """Expected Christmas effect for a week whose Monday is `off` days from 24.12."""
        if off not in xm_cache:
            best = {}
            for Y, o, e in xm_samples:
                d = abs(o - off)
                if Y != S0 and d <= 3 and (Y not in best or d < best[Y][0]):
                    best[Y] = (d, e)
            sg = P["xm_sigma"]
            num = den = 0.0
            for Y in sorted(best):
                d, e = best[Y]
                wgt = math.exp(-d * d / (2 * sg * sg)) * rw(Y, P["xm_hl"])
                num += wgt * e
                den += wgt
            xm_cache[off] = num / den if den > 1e-9 else 0.0
        return xm_cache[off]

    def eff(i, own=True):
        e = beta * _npub(i) if hol >= 1 else 0.0
        if hol >= 2:
            xm = _xmas(i)
            if xm is not None:
                e += xeff_own[i] if (own and i in xeff_own and SI.get(i) != S0) else xm_hat(xm[1])
        return e

    X = {i: x - eff(i) for i, x in LI.items()}

    # ── climatology and growth profile (past non-COVID seasons) ──────────
    lev, gro = {}, {}
    for i, x in X.items():
        S, p = _pos(i)
        if S == S0 or S in COVID_SEASONS:
            continue
        lev.setdefault(p, []).append((S, x))
        x1 = X.get(i + 1)
        if x1 is not None:
            gro.setdefault(p, []).append((S, x1 - x))
    hl = P["half_life"]

    def agg(table, trim):
        out = {}
        for p, lst in table.items():
            if len({S for S, _ in lst}) >= P["min_n"]:
                out[p] = _wtrim([(rw(S, hl), x) for S, x in lst], trim)
        return out

    c_raw = agg(lev, P["trim"])
    g = agg(gro, P["g_trim"])
    a = P["smooth"]
    c = {p: (1 - 2 * a) * x + a * (c_raw.get(p - 1, x) + c_raw.get(p + 1, x))
         for p, x in c_raw.items()} if a > 0 else c_raw

    def cl(p):
        for d in (0, 1, -1, 2, -2, 3, -3):
            if p + d in c:
                return c[p + d]
        return None

    # ── anomaly ──────────────────────────────────────────────────────────
    num = den = 0.0
    for j in range(P["k"]):
        i = i_last - j
        cp = cl(_pos(i)[1]) if i in X else None
        if cp is not None:
            wgt = P["decay"] ** j
            num += wgt * (X[i] - cp)
            den += wgt
    a0 = num / den if den > 0 else 0.0
    aL = 0.0
    if P["lam"] > 0:
        ls = []
        for j in range(P["L"]):
            i = i_last - j
            if i in X and SI[i] not in COVID_SEASONS:
                cp = cl(_pos(i)[1])
                if cp is not None:
                    ls.append(X[i] - cp)
        aL = P["lam"] * sum(ls) / len(ls) if ls else 0.0

    # ── paths ────────────────────────────────────────────────────────────
    sh = i0 - i_last
    rho = P["rho"]
    lvB = X[i_last]
    for j in range(sh):                       # bridge a missing origin week
        lvB += g.get(_pos(i_last + j)[1], 0.0)
    out = []
    for h in range(1, H + 1):
        t = i0 + h
        lvB += g.get(_pos(t - 1)[1], 0.0)
        ct = cl(_pos(t)[1])
        if ct is None:
            z = lvB
        else:
            A = ct + aL + (a0 - aL) * rho ** (h + sh)
            wA = P["wA1"] + (P["wA8"] - P["wA1"]) * (h - 1) / max(H - 1, 1)
            wA = min(max(wA, 0.0), 1.0)
            z = wA * A + (1 - wA) * lvB
        out.append(z + eff(t, own=False))
    return out


def predict(hist, origin, H):
    return forecast(hist, origin, H)
