#!/usr/bin/env python3
"""Candidate "free" (work in progress)."""
import math
from datetime import date, timedelta

COVID = (2019, 2020, 2021)

P = dict(
    delta=0.8,       # recency decay of season weights in climatology
    smooth_c=0.3,    # climatology neighbour smoothing
    nL=78,           # window (weeks) of long-run anomaly (flat mean)
    w_now=0.7,       # weight of current week in smoothed current anomaly
    rho=0.8,         # decay of short anomaly per week
    blend=(0.4, 0.2, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0),  # weight of climatological-growth path per h
    # analog component
    ablend=(0.0,) * 8,
    ablend_win=None,
    aM=4, aT=3, adecay=0.8, apen=0.002, aK=10, arec=0.9,
    # local regression correction
    reg=False,
    K=4,             # half-width (weeks) of week-of-year window for training origins
    lam=2.0,         # ridge penalty
    dreg=0.85,       # recency decay of training seasons
    feats=("one", "s"),
    bien=1.0,        # extra weight of seasons an even number of years back
    shape=0.0,       # fraction of season offset removed before averaging climatology
    rho_win=0.9, 
    rho_aut=0.8,     # decay of short anomaly for autumn origins (W34-W48)    # decay of short anomaly for winter origins (W49-W10)
    cmed=0.0,        # weight of weighted median (vs mean) in climatology
    nM=0,            # window of medium-term anomaly (0 = off)
    rhoM=0.95,       # decay of medium-term anomaly
    n0=2.0,          # pseudo-weeks shrinking the long-run anomaly to 0
    hol=True,
    hol_ridge=1.0,
    hol_iter=3,
)


def _monday(y, w):
    return date.fromisocalendar(y, w, 1)


def _widx(y, w):
    return _monday(y, w).toordinal() // 7


def _from_idx(i):
    iy, iw, _ = date.fromordinal(i * 7 + 1).isocalendar()
    return iy, iw


def _season(y, w):
    return y if w >= 27 else y - 1


def _slot(w):
    return 52 if w == 53 else w


# ── calendar (Czech public holidays, Christmas window, autumn school break) ──
def _easter(y):
    a = y % 19
    b, c = divmod(y, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mo, da = divmod(h + l - 7 * m + 114, 31)
    return date(y, mo, da + 1)


_HCACHE = {}


def _holidays(y):
    if y not in _HCACHE:
        e = _easter(y)
        hs = {date(y, 5, 1), date(y, 5, 8), date(y, 7, 5), date(y, 7, 6),
              date(y, 9, 28), date(y, 10, 28), date(y, 11, 17), e + timedelta(days=1)}
        if y >= 2016:
            hs.add(e - timedelta(days=2))
        _HCACHE[y] = hs
    return _HCACHE[y]


_FCACHE = {}


def _calfeat(i):
    """Calendar features of the week with index i."""
    if i in _FCACHE:
        return _FCACHE[i]
    m = date.fromordinal(i * 7 + 1)
    xm = ph = edge = 0
    for k in range(5):
        d = m + timedelta(days=k)
        if (d.month == 12 and d.day >= 24) or (d.month == 1 and d.day == 1):
            xm += 1
        elif (d.month == 12 and d.day == 23) or (d.month == 1 and d.day == 2):
            edge += 1
        elif d in _holidays(d.year):
            ph += 1
    # autumn school break: week containing 28.10 (or the next one if 28.10 is Sunday)
    oct28 = date(m.year, 10, 28)
    if oct28.weekday() == 6:
        oct28 += timedelta(days=1)
    aut = 1 if m <= oct28 < m + timedelta(days=7) else 0
    # first week after the Christmas window (catch-up)
    prev = m - timedelta(days=7)
    post = 0
    if xm == 0:
        for k in range(5):
            d = prev + timedelta(days=k)
            if (d.month == 12 and d.day >= 24) or (d.month == 1 and d.day == 1):
                post = 1
    f = {"xm": xm / 5.0, "ph": ph / 5.0, "edge": edge / 5.0, "aut": aut, "post": post}
    _FCACHE[i] = f
    return f


def _smooth(c, a):
    if a <= 0:
        return c
    cs = {}
    for sl, v in c.items():
        lo = 52 if sl == 1 else sl - 1
        hi = 1 if sl == 52 else sl + 1
        nb = [c[k] for k in (lo, hi) if k in c]
        cs[sl] = (1 - a) * v + a * sum(nb) / len(nb) if nb else v
    return cs


class _Ctx:
    """Per-origin precomputation: log series, slot sums by season."""

    def __init__(self, hist, origin, P):
        self.P = P
        y0, w0 = origin
        self.i0 = _widx(y0, w0)
        self.S0 = _season(y0, w0)
        self.X = {}
        self.slot = {}
        self.seas = {}
        bys = {}
        for (y, w), v in hist.items():
            if v and v > 0:
                i = _widx(y, w)
                lv = math.log(v)
                s = _season(y, w)
                sl = _slot(w)
                self.X[i] = lv
                self.slot[i] = sl
                self.seas[i] = s
                if s < self.S0 and s not in COVID:
                    bys.setdefault(s, {}).setdefault(sl, []).append(lv)
        # holiday effects (estimated from hist only), de-holiday the series
        self.beta = _est_holiday(self.X, P) if P.get("hol") else {}
        if self.beta:
            for i in self.X:
                self.X[i] -= self.hol(i)
            bys = {}
            for i, lv in self.X.items():
                s = self.seas[i]
                if s < self.S0 and s not in COVID:
                    bys.setdefault(s, {}).setdefault(self.slot[i], []).append(lv)
        # per season per slot mean (W52/W53 merged)
        self.bys = {s: {sl: sum(v) / len(v) for sl, v in d.items()} for s, d in bys.items()}
        self.wts = {s: P["delta"] ** (self.S0 - 1 - s) * (P["bien"] if (self.S0 - s) % 2 == 0 else 1.0)
                    for s in self.bys}
        self._sums()
        if P.get("shape", 0) and self.bys:
            c0 = {sl: self.num[sl] / self.den[sl] for sl in self.num}
            for s, d in self.bys.items():
                off = [lv - c0[sl] for sl, lv in d.items()]
                o = P["shape"] * sum(off) / len(off)
                self.bys[s] = {sl: lv - o for sl, lv in d.items()}
            self._sums()

    def _sums(self):
        num, den = {}, {}
        for s, d in self.bys.items():
            wt = self.wts[s]
            for sl, lv in d.items():
                num[sl] = num.get(sl, 0.0) + wt * lv
                den[sl] = den.get(sl, 0.0) + wt
        self.num, self.den = num, den

    def hol(self, i):
        if not self.beta:
            return 0.0
        f = _calfeat(i)
        return sum(b * f[k] for k, b in self.beta.items())

    def clim(self, excl=None):
        if self.P.get("cmed") and excl is None:
            acc = {}
            for s, d in self.bys.items():
                for sl, lv in d.items():
                    acc.setdefault(sl, []).append((lv, self.wts[s]))
            c = {}
            for sl, xs in acc.items():
                xs.sort()
                tot = sum(w for _, w in xs)
                q = self.P["cmed"]  # blend of weighted median and weighted mean
                cum = 0.0
                med = xs[-1][0]
                for lv, w in xs:
                    cum += w
                    if cum >= 0.5 * tot:
                        med = lv
                        break
                mean = sum(lv * w for lv, w in xs) / tot
                c[sl] = q * med + (1 - q) * mean
            return _smooth(c, self.P["smooth_c"])
        num, den = self.num, self.den
        c = {}
        d_ex = self.bys.get(excl, {}) if excl is not None else {}
        w_ex = self.wts.get(excl, 0.0)
        for sl in num:
            n, d = num[sl], den[sl]
            if sl in d_ex:
                n -= w_ex * d_ex[sl]
                d -= w_ex
            if d > 1e-9:
                c[sl] = n / d
        return _smooth(c, self.P["smooth_c"])


HFEATS = ("xm", "ph", "edge", "aut", "post")


def _est_holiday(X, P):
    """Backfitting: residual of log level vs. neighbours (de-holidayed) on calendar features."""
    feats = P.get("hfeats", HFEATS)
    beta = {k: 0.0 for k in feats}
    idx = sorted(X)
    F = {i: _calfeat(i) for i in idx}
    hot = [i for i in idx if any(F[i][k] for k in feats)]
    for _ in range(P["hol_iter"]):
        def z(i):
            return X[i] - sum(beta[k] * F[i][k] for k in feats)
        A = {(p, q): 0.0 for p in feats for q in feats}
        b = {p: 0.0 for p in feats}
        for i in hot:
            nb = []
            for d, wt in ((1, 2.0), (2, 1.0)):
                if i - d in X and i + d in X:
                    nb.append((wt, 0.5 * (z(i - d) + z(i + d))))
            if not nb:
                continue
            sm = sum(w * v for w, v in nb) / sum(w for w, _ in nb)
            r = X[i] - sm
            fv = F[i]
            for p in feats:
                b[p] += fv[p] * r
                for q in feats:
                    A[(p, q)] += fv[p] * fv[q]
        M = [[A[(p, q)] + (P["hol_ridge"] if p == q else 0.0) for q in feats] for p in feats]
        sol = _solve(M, [b[p] for p in feats])
        beta = dict(zip(feats, sol))
    return beta


def _cval(c, sl):
    if sl in c:
        return c[sl]
    for d in range(1, 6):
        for sgn in (-1, 1):
            k = (sl - 1 + sgn * d) % 52 + 1
            if k in c:
                return c[k]
    return None


def _base(ctx, c, i0, H, P):
    """Base forecast from origin index i0 using climatology c.
    Returns (list of log forecasts, feature dict) or None."""
    X, slot = ctx.X, ctx.slot
    if i0 not in X:
        return None

    def anom(i):
        if i not in X:
            return None
        cc = _cval(c, slot[i])
        return None if cc is None else X[i] - cc

    sa = 0.0
    n = 0
    seas = ctx.seas
    for k in range(P["nL"]):
        if seas.get(i0 - k) in COVID:
            continue
        a = anom(i0 - k)
        if a is not None:
            sa += a
            n += 1
    L = sa / (n + P["n0"])
    Lm = None
    if P.get("nM"):
        sm = 0.0
        nm = 0
        for k in range(P["nM"]):
            a = anom(i0 - k)
            if a is not None:
                sm += a
                nm += 1
        if nm:
            Lm = sm / nm
    a0 = anom(i0)
    if a0 is None:
        return None
    a1 = anom(i0 - 1)
    a_s = a0 if a1 is None else P["w_now"] * a0 + (1 - P["w_now"]) * a1
    s = a_s - L
    a2 = anom(i0 - 2)
    mom = 0.0
    if a1 is not None and a2 is not None:
        mom = 0.5 * (a0 - a2)
    elif a1 is not None:
        mom = a0 - a1
    w0 = _from_idx(i0)[1]
    if w0 >= 49 or w0 <= 10:
        rho = P["rho_win"]
    elif 34 <= w0 <= 48:
        rho = P["rho_aut"]
    else:
        rho = P["rho"]
    out = []
    lv_prof = X[i0]
    cprev = _cval(c, slot[i0])
    for h in range(1, H + 1):
        y, w = _from_idx(i0 + h)
        ch = _cval(c, _slot(w))
        if Lm is None:
            f1 = ch + L + s * rho ** h
        else:
            f1 = ch + L + (Lm - L) * P["rhoM"] ** h + (a_s - Lm) * rho ** h
        lv_prof += ch - cprev
        cprev = ch
        b = P["blend"][min(h, len(P["blend"])) - 1]
        out.append((1 - b) * f1 + b * lv_prof)
    feats = {"one": 1.0, "s": s, "mom": mom, "L": L, "a0": a0 - L, "d01": (a0 - a1) if a1 is not None else 0.0}
    return out, feats


def _analog(ctx, i0, H, P):
    """Method of analogs on the de-holidayed series: past seasons shifted by
    tau weeks, level-matched on the last M weeks; returns log forecasts or None."""
    X, seas, slot = ctx.X, ctx.seas, ctx.slot
    M, T = P["aM"], P["aT"]
    wm = [P["adecay"] ** m for m in range(M)]
    cur = [X.get(i0 - m) for m in range(M)]
    if cur[0] is None:
        return None
    w0 = slot[i0]
    cands = []
    for j0, s in seas.items():
        if s not in ctx.bys or slot[j0] != w0:
            continue
        if j0 + H > i0:
            continue
        for tau in range(-T, T + 1):
            j = j0 + tau
            fut = [X.get(j + h) for h in range(1, H + 1)]
            if any(v is None for v in fut) or seas.get(j + H) in COVID:
                continue
            sw = so = 0.0
            ds = []
            for m in range(M):
                a, b = cur[m], X.get(j - m)
                if a is None or b is None:
                    continue
                ds.append((wm[m], a - b))
            if len(ds) < max(2, M // 2):
                continue
            sw = sum(w for w, _ in ds)
            off = sum(w * d for w, d in ds) / sw
            sse = sum(w * (d - off) ** 2 for w, d in ds) / sw
            sse += P["apen"] * tau * tau
            cands.append((sse, s, off, fut))
    if len(cands) < 2:
        return None
    cands.sort(key=lambda r: r[0])
    top = cands[:P["aK"]]
    s2 = max(top[len(top) // 2][0], 1e-4)
    out = [0.0] * H
    tw = 0.0
    for sse, s, off, fut in top:
        w = math.exp(-0.5 * sse / s2) * P["arec"] ** (ctx.S0 - 1 - s)
        tw += w
        for h in range(H):
            out[h] += w * (fut[h] + off)
    return [v / tw for v in out]


def _solve(A, b):
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            return [0.0] * n
        M[col], M[piv] = M[piv], M[col]
        for r in range(n):
            if r != col:
                f = M[r][col] / M[col][col]
                if f:
                    for k in range(col, n + 1):
                        M[r][k] -= f * M[col][k]
    return [M[i][n] / M[i][i] for i in range(n)]


def _predict(hist, origin, H, P):
    ctx = _Ctx(hist, origin, P)
    i0 = ctx.i0
    if not ctx.bys or i0 not in ctx.X:
        lv = math.log(max(hist.get(origin, 1.0), 1.0))
        return [lv] * H
    c = ctx.clim()
    res = _base(ctx, c, i0, H, P)
    if res is None:
        return [ctx.X[i0]] * H
    base, f0 = res
    hb = [ctx.hol(i0 + h) for h in range(1, H + 1)]
    if P["ablend"] and any(P["ablend"]):
        an = _analog(ctx, i0, H, P)
        if an is not None:
            w0 = _from_idx(i0)[1]
            ab = P["ablend_win"] if (w0 >= 49 or w0 <= 10) and P.get("ablend_win") else P["ablend"]
            base = [(1 - ab[h]) * base[h] + ab[h] * an[h] for h in range(H)]
    if not P["reg"]:
        return [b + x for b, x in zip(base, hb)]
    feats = P["feats"]
    nf = len(feats)
    w0 = _slot(_from_idx(i0)[1])
    # gather training samples
    AtA = [[[0.0] * nf for _ in range(nf)] for _ in range(H)]
    Atb = [[0.0] * nf for _ in range(H)]
    cache = {}
    for i, s in ctx.seas.items():
        if s not in ctx.bys:
            continue
        dw = (ctx.slot[i] - w0 + 26) % 52 - 26
        if abs(dw) > P["K"] or i + 1 > i0:
            continue
        if s not in cache:
            cache[s] = ctx.clim(excl=s)
        cs = cache[s]
        ws = P["dreg"] ** (ctx.S0 - 1 - s)
        if True:
            r = _base(ctx, cs, i, H, P)
            if r is None:
                continue
            bp, ff = r
            fv = [ff[k] for k in feats]
            wk = ws * (1.0 - abs(dw) / (P["K"] + 1.0))
            for h in range(1, H + 1):
                t = i + h
                if t > i0 or t not in ctx.X:
                    continue
                y = ctx.X[t] - bp[h - 1]
                A, b = AtA[h - 1], Atb[h - 1]
                for p in range(nf):
                    wp = wk * fv[p]
                    b[p] += wp * y
                    for q in range(nf):
                        A[p][q] += wp * fv[q]
    out = []
    fv0 = [f0[k] for k in feats]
    for h in range(1, H + 1):
        A = [row[:] for row in AtA[h - 1]]
        for p in range(nf):
            A[p][p] += P["lam"]
        th = _solve(A, Atb[h - 1])
        out.append(base[h - 1] + hb[h - 1] + sum(t * f for t, f in zip(th, fv0)))
    return out


def predict(hist, origin, H):
    return _predict(hist, origin, H, P)
