#!/usr/bin/env python3
"""Holiday-aware ARI forecast (candidate "holiday").

Idea
----
Czech GP reporting dips are calendar driven (Christmas/New Year by actual date,
Easter, 28.9/28.10/17.11, 1.5/8.5, autumn school holidays ...).  The model

 1. builds calendar features for every ISO week (number of weekdays inside the
    Christmas window 24.12-1.1, edge days 23.12/2.1, other weekday public
    holidays, bridge days, autumn school holiday week, Easter school days,
    first week after Christmas),
 2. estimates multiplicative (log-additive) holiday effects from the history
    by iterative backfitting: residual of log y against a local smoother of
    the de-holidayed series (neighbours only) regressed on the features
    (ridge, recency weighted),
 3. de-holidays the series: z = log y - X.beta,
 4. forecasts z with a recency-weighted seasonal climatology (by ISO week)
    plus an anomaly that decays (AR(1)) towards a long-run anomaly,
 5. re-applies the holiday effects of the target weeks.

Holiday effects are re-estimated at every origin from `hist` only (ridge
regression, ~10 coefficients, recency weighted), so nothing is hard-coded but
the calendar.  Easter is computed with the Meeus/Jones/Butcher algorithm;
Good Friday counts as a public holiday from 2016.  ISO week 53 has its own
slot whose climatology is the mean of W52 and W01 of the de-holidayed series.

Pure stdlib, deterministic, uses only `hist` (data up to and incl. origin).
predict(hist, origin, H) -> list of H natural-log forecasts for origin+1..+H.
"""
import math
from datetime import date, timedelta
from functools import lru_cache

COVID_SEASONS = (2019, 2020, 2021)

DEFAULT = dict(
    # holiday-effect estimation
    hol_iters=4,          # backfitting iterations
    hol_ridge=5.0,        # ridge penalty (in units of "weeks of evidence")
    hol_halflife=6.0,     # recency half-life (seasons) for holiday weights
    fset="full",          # holiday feature set: full | simple | mid | mid2
    use_hol=1,            # 0 = ablation without holiday effects
    # climatology
    clim_halflife=4.0,    # recency half-life (seasons) for climatology
    clim_smooth=0.0,      # weight of +-1 neighbour weeks (centre = 1)
    min_n=1,              # min. seasons per week slot (else interpolated)
    twoway=0,             # two-way (season level + week shape) climatology fit
    # anomaly
    an_w=(0.8, 0.2),      # weights of last anomalies (origin, origin-1)
    hol_tau=0.0,          # down-weight anomalies of holiday weeks: exp(-|effect|/tau)
    rho=0.90,             # AR(1) decay of short anomaly per week
    long_n=78,            # weeks used for long-run anomaly
    long_w=0.75,          # weight of long-run anomaly as decay target
    mom=0.0,              # momentum (recent growth deviation) weight
    mom_phi=0.5,
)


# ── ISO-week helpers ─────────────────────────────────────────────────────────
def _monday(y, w):
    return date.fromisocalendar(y, w, 1)


def _widx(y, w):
    return _monday(y, w).toordinal() // 7


def _from_idx(i):
    iy, iw, _ = date.fromordinal(i * 7 + 1).isocalendar()
    return (iy, iw)


def _season(y, w):
    return y if w >= 27 else y - 1


def _slot(y, w):
    """Season slot 0..52 starting at W27 (W53 gets its own slot 26)."""
    if w == 53:
        return 26
    if w >= 27:
        return w - 27          # W27..W52 -> 0..25
    return w + 26              # W01..W26 -> 27..52


NSLOT = 53


# ── Czech calendar ───────────────────────────────────────────────────────────
@lru_cache(maxsize=None)
def easter(y):
    """Gregorian Easter Sunday (Anonymous / Meeus-Jones-Butcher algorithm)."""
    a = y % 19
    b, c = divmod(y, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(y, month, day)


@lru_cache(maxsize=None)
def public_holidays(y):
    e = easter(y)
    s = {date(y, 1, 1), e + timedelta(days=1), date(y, 5, 1), date(y, 5, 8),
         date(y, 7, 5), date(y, 7, 6), date(y, 9, 28), date(y, 10, 28),
         date(y, 11, 17), date(y, 12, 24), date(y, 12, 25), date(y, 12, 26)}
    if y >= 2016:                       # Good Friday is a holiday since 2016
        s.add(e - timedelta(days=2))
    return frozenset(s)


def _is_ph(d):
    return d in public_holidays(d.year)


def _in_xmas(d):
    return (d.month == 12 and d.day >= 24) or (d.month == 1 and d.day == 1)


@lru_cache(maxsize=None)
def autumn_week(y):
    """ISO week of the autumn school holidays (2 days next to 28.10)."""
    d = date(y, 10, 28)
    if d.weekday() == 6:               # Sunday -> holidays Mon/Tue after
        d = d + timedelta(days=1)
    return d.isocalendar()[:2]


FEATS = ("xmas_main", "xmas_other", "xedge", "ph_mon", "ph_mid", "bridge",
         "july", "autumn", "easter_sch", "post_xmas")
NF = len(FEATS)


@lru_cache(maxsize=None)
def features(y, w):
    """Calendar features of ISO week (y, w) - counts of affected weekdays.

    xmas_main  weekdays on 24.12/25.12          xmas_other weekdays 26.12-1.1
    xedge      weekdays on 23.12 / 2.1          ph_mon     Monday public hol.
    ph_mid     Tue-Fri public holidays          bridge     Mon/Fri between PH and weekend
    july       weekday PH among 5.7/6.7 (extra) autumn     autumn school-holiday week
    easter_sch Maundy Thursday (+Good Friday before 2016, school-only days)
    post_xmas  first week starting 2.1-8.1
    (the Christmas window days are not counted in ph_*/bridge)
    """
    m = _monday(y, w)
    days = [m + timedelta(days=i) for i in range(7)]
    wd = days[:5]
    xmain = sum(1 for d in wd if d.month == 12 and d.day in (24, 25))
    xother = sum(1 for d in wd if _in_xmas(d) and not (d.month == 12 and d.day in (24, 25)))
    xedge = sum(1 for d in wd if (d.month, d.day) in ((12, 23), (1, 2)))
    ph_mon = sum(1 for d in wd if _is_ph(d) and not _in_xmas(d) and d.weekday() == 0)
    ph_mid = sum(1 for d in wd if _is_ph(d) and not _in_xmas(d) and d.weekday() > 0)
    july = sum(1 for d in wd if d.month == 7 and d.day in (5, 6))
    bridge = 0
    for d in wd:
        if _is_ph(d) or _in_xmas(d) or (d.month, d.day) in ((12, 23), (1, 2)):
            continue
        if d.weekday() == 4 and _is_ph(d - timedelta(days=1)):
            bridge += 1
        elif d.weekday() == 0 and _is_ph(d + timedelta(days=1)):
            bridge += 1
    autumn = 1 if any(autumn_week(yy) == (y, w) for yy in {days[0].year, days[6].year}) else 0
    easter_sch = 0
    for yy in {days[0].year, days[6].year}:
        gf = easter(yy) - timedelta(days=2)
        for d in (gf - timedelta(days=1), gf):
            if d in wd and not _is_ph(d):
                easter_sch += 1
    post_xmas = 1 if (m.month == 1 and 2 <= m.day <= 8) else 0
    return (float(xmain), float(xother), float(xedge), float(ph_mon), float(ph_mid),
            float(bridge), float(july), float(autumn), float(easter_sch), float(post_xmas))


def featvec(y, w, fset):
    f = features(y, w)
    if fset == "full":
        return f
    xm, xo, xe, pm, pmid, br, jl, au, es, px = f
    if fset == "simple":      # pooled Christmas days, pooled public holidays
        return (xm + xo, 0.0, xe, pm + pmid, 0.0, br, 0.0, au, es, px)
    if fset == "mid":         # Christmas split, PH pooled
        return (xm, xo, xe, pm + pmid, 0.0, br, 0.0, au, es, px)
    if fset == "mid2":        # Christmas pooled, PH split
        return (xm + xo, 0.0, xe, pm, pmid, br, jl, au, es, px)
    raise ValueError(fset)


# ── small linear algebra ─────────────────────────────────────────────────────
def _solve(A, b):
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[p][c]) < 1e-12:
            continue
        M[c], M[p] = M[p], M[c]
        for r in range(n):
            if r != c and M[r][c] != 0.0:
                f = M[r][c] / M[c][c]
                for k in range(c, n + 1):
                    M[r][k] -= f * M[c][k]
    return [M[i][n] / M[i][i] if abs(M[i][i]) > 1e-12 else 0.0 for i in range(n)]


# ── holiday-effect estimation ────────────────────────────────────────────────
def estimate_holiday(L, F, wts, P):
    """L: {idx: log y}, F: {idx: feats}, wts: {idx: weight}. -> beta list."""
    beta = [0.0] * NF
    drop = set(P.get("drop", ()))
    keep = [p for p in range(NF) if FEATS[p] not in drop]
    idxs = [i for i in L if any(F[i][p] for p in keep)]
    for _ in range(P["hol_iters"]):
        def z(i):
            return L[i] - sum(b * f for b, f in zip(beta, F[i]))
        A = [[0.0] * NF for _ in range(NF)]
        rhs = [0.0] * NF
        for i in idxs:
            nb = []
            for k, wk_ in ((1, 2.0), (2, 1.0)):
                if (i - k) in L and (i + k) in L:
                    nb.append((wk_, (z(i - k) + z(i + k)) / 2.0))
            if not nb:
                continue
            base = sum(a * v for a, v in nb) / sum(a for a, _ in nb)
            r = L[i] - base
            f = F[i]
            wt = wts[i]
            for p in keep:
                if f[p] == 0.0:
                    continue
                rhs[p] += wt * f[p] * r
                for q in keep:
                    A[p][q] += wt * f[p] * f[q]
        for p in range(NF):
            A[p][p] += P["hol_ridge"]
        beta = _solve(A, rhs)
    return beta


# ── climatology of the de-holidayed series ──────────────────────────────────
def climatology(Z, S, s0, P):
    """Seasonal shape c[slot] of z (current season s0 excluded).

    two-way fit (P["twoway"]): z[s,sl] = alpha_s + c[sl] + e, recency-weighted
    over seasons, so that slots observed only in some seasons are not biased by
    those seasons' overall level.  Then light smoothing over neighbour slots and
    cyclic interpolation of empty slots.  Slot 26 (W53) = mean of W52/W01.
    """
    data = {}
    for i, zv in Z.items():
        s, sl = S[i]
        if s == s0 or sl == 26:
            continue
        data.setdefault(s, {})[sl] = zv
    if not data:
        return None
    hl = P["clim_halflife"]
    sw = {s: (0.5 ** ((s0 - s) / hl) if hl > 0 else 1.0) for s in data}
    alpha = {s: 0.0 for s in data}
    raw = [None] * NSLOT
    rw = [0.0] * NSLOT
    for _ in range(P["twoway"] and 6 or 1):
        for sl in range(NSLOT):
            num = den = 0.0
            cnt = 0
            for s, d in data.items():
                if sl in d:
                    num += sw[s] * (d[sl] - alpha[s])
                    den += sw[s]
                    cnt += 1
            ok = den > 0 and cnt >= P["min_n"]
            raw[sl] = num / den if ok else None
            rw[sl] = den if ok else 0.0
        if not P["twoway"]:
            break
        for s, d in data.items():
            rs = [v - raw[sl] for sl, v in d.items() if raw[sl] is not None]
            alpha[s] = sum(rs) / len(rs) if rs else 0.0
        # centre season levels (weighted) so c carries the recency-weighted level
        tot = sum(sw.values())
        m = sum(sw[s] * alpha[s] for s in data) / tot
        for s in data:
            alpha[s] -= m
    order = [sl for sl in range(NSLOT) if sl != 26]
    n = len(order)
    clim = [None] * NSLOT
    for k, sl in enumerate(order):
        acc = accw = 0.0
        for d, kw in ((-1, P["clim_smooth"]), (0, 1.0), (1, P["clim_smooth"])):
            sl2 = order[(k + d) % n]
            if raw[sl2] is not None and kw > 0:
                acc += kw * rw[sl2] * raw[sl2]
                accw += kw * rw[sl2]
        if accw > 0:
            clim[sl] = acc / accw
    have = [k for k, sl in enumerate(order) if clim[sl] is not None]
    if not have:
        return None
    if len(have) < n:
        filled = {}
        for k in range(n):
            if clim[order[k]] is not None:
                continue
            prv = max((h for h in have if h < k), default=None)
            nxt = min((h for h in have if h > k), default=None)
            if prv is None:
                prv = have[-1] - n
            if nxt is None:
                nxt = have[0] + n
            a = clim[order[prv % n]]
            b = clim[order[nxt % n]]
            filled[order[k]] = a + (b - a) * (k - prv) / (nxt - prv)
        for sl, v in filled.items():
            clim[sl] = v
    clim[26] = 0.5 * (clim[25] + clim[27])
    return clim


# ── forecast ─────────────────────────────────────────────────────────────────
def _predict(hist, origin, H, P):
    y0, w0 = origin
    s0 = _season(y0, w0)
    oi = _widx(y0, w0)
    L, F, S, wts_h = {}, {}, {}, {}
    for (y, w) in sorted(hist):         # sorted: result independent of dict order
        v = hist[(y, w)]
        if not v or v <= 0:
            continue
        s = _season(y, w)
        if s in COVID_SEASONS:
            continue
        i = _widx(y, w)
        L[i] = math.log(v)
        F[i] = featvec(y, w, P["fset"])
        S[i] = (s, _slot(y, w))
        wts_h[i] = 0.5 ** ((s0 - s) / P["hol_halflife"]) if P["hol_halflife"] > 0 else 1.0
    # make sure origin (even in COVID season) is available
    if oi not in L and hist.get(origin):
        L[oi] = math.log(max(hist[origin], 1.0))
        F[oi] = featvec(y0, w0, P["fset"])
        S[oi] = (s0, _slot(y0, w0))
        wts_h[oi] = 1.0
    beta = estimate_holiday(L, F, wts_h, P) if P["use_hol"] else [0.0] * NF

    def hol(f):
        return sum(b * x for b, x in zip(beta, f))

    Z = {i: L[i] - hol(F[i]) for i in L}

    clim = climatology(Z, S, s0, P)
    if clim is None or not Z:
        last = max((k for k in hist if hist[k] and hist[k] > 0),
                   key=lambda k: _widx(*k), default=None)
        lv = math.log(max(hist[last], 1.0)) if last else 0.0
        return [lv] * H

    def c_of(i):
        return clim[_slot(*_from_idx(i))]

    # anomalies
    # (weeks with a large estimated holiday effect are down-weighted: their
    #  de-holidayed value is less certain)
    a_short = []
    for k, wv in enumerate(P["an_w"]):
        j = oi - k
        if j in Z:
            he = abs(L[j] - Z[j])
            if P["hol_tau"] > 0:
                wv = wv * math.exp(-he / P["hol_tau"])
            a_short.append((wv, Z[j] - c_of(j)))
    if not a_short:                    # origin week missing: latest available
        j = max(i for i in Z if i <= oi)
        a_short = [(1.0, Z[j] - c_of(j))]
    a0 = sum(w * a for w, a in a_short) / sum(w for w, _ in a_short)
    longs = [Z[j] - c_of(j) for j in range(oi - P["long_n"] + 1, oi + 1) if j in Z]
    aL = (sum(longs) / len(longs)) * P["long_w"] if longs else 0.0
    # momentum: recent de-holidayed growth vs climatological growth
    mom = 0.0
    if P["mom"] and oi in Z and (oi - 1) in Z:
        mom = (Z[oi] - Z[oi - 1]) - (c_of(oi) - c_of(oi - 1))
    out = []
    cum_m = 0.0
    for h in range(1, H + 1):
        j = oi + h
        cum_m += P["mom"] * mom * (P["mom_phi"] ** h)
        zf = c_of(j) + aL + (a0 - aL) * (P["rho"] ** h) + cum_m
        out.append(zf + hol(featvec(*_from_idx(j), P["fset"])))
    return out


def predict(hist, origin, H):
    return _predict(hist, origin, H, DEFAULT)
