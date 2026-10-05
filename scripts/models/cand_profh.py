#!/usr/bin/env python3
"""PROF-H: sezónní profil růstu + svátkové efekty + tlumená odchylka trendu.

  a_t      = log y_t − δ(τ_t)                 (očištěno o svátky)
  g[w]     = medián týdenních změn a v týdnu w (±1 týden) přes minulé sezóny
  s_t      = průměr posledních n změn a (letošní tempo), ḡ_t = profil za stejné týdny
  ẑ_{t+h}  = a_t + Σ_{i≤h} (g[w(t+i)] + φ^i·(s_t − ḡ_t)) + δ(τ_{t+h})

Svátkové typy podle data (ne čísla týdne): xmas1 (25. 12.), xmas2 (1. 1.),
autumn (28. 10. ve všední den), goodfri, eastermon, minor (1. 5., 8. 5.,
28. 9., 17. 11., 5. 7., 6. 7. ve všední den). δ = medián rozdílu log hodnoty
proti log-lineární interpolaci mezi nejbližšími nesvátkovými sousedy.
"""
import math
from datetime import date, timedelta

COVID_SEASONS = (2019, 2020, 2021)
P = dict(n=3, phi=0.3, win=1, recent=True, clip=0.35)


def _season(y, w):
    return y if w >= 27 else y - 1


def _widx(y, w):
    return date.fromisocalendar(y, w, 1).toordinal() // 7


def _from_idx(i):
    y, w, _ = date.fromordinal(i * 7 + 1).isocalendar()
    return y, w


def _easter(y):
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mo = (h + l - 7 * m + 114) // 31
    da = (h + l - 7 * m + 114) % 31 + 1
    return date(y, mo, da)


_TAU = {}


def htype(y, w):
    key = (y, w)
    if key in _TAU:
        return _TAU[key]
    mon = date.fromisocalendar(y, w, 1)
    days = [mon + timedelta(d) for d in range(7)]
    wd = days[:5]
    t = None
    for Y in {mon.year, (mon + timedelta(6)).year}:
        if date(Y, 12, 25) in days:
            t = "xmas1"
        elif date(Y, 1, 1) in days:
            t = t or "xmas2"
        elif date(Y, 10, 28) in wd:
            t = t or "autumn"
        else:
            e = _easter(Y)
            if e - timedelta(2) in days:
                t = t or "goodfri"
            elif e + timedelta(1) in days:
                t = t or "eastermon"
            elif any(date(Y, m_, d_) in wd for m_, d_ in ((5, 1), (5, 8), (9, 28), (11, 17), (7, 5), (7, 6))):
                t = t or "minor"
    _TAU[key] = t
    return t


def _wk(w):
    return 52 if w == 53 else w


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return (xs[(n - 1) // 2] + xs[n // 2]) / 2 if n else 0.0


def forecast(hist, origin, H, p=None):
    p = {**P, **(p or {})}
    Z = {}
    for (y, w), v in hist.items():
        if v and v > 0:
            Z[_widx(y, w)] = math.log(v)
    i0 = _widx(*origin)
    if i0 not in Z:
        i0 = max(i for i in Z if i <= i0)
    S0 = _season(*_from_idx(i0))
    tau = {i: htype(*_from_idx(i)) for i in Z}
    # δ: svátkové efekty
    occ = {}
    for i, t in tau.items():
        if t is None or _season(*_from_idx(i)) in COVID_SEASONS:
            continue
        lo = next((i - k for k in range(1, 4) if (i - k) in Z and tau.get(i - k) is None), None)
        hi = next((i + k for k in range(1, 4) if (i + k) in Z and tau.get(i + k) is None), None)
        if lo is None or hi is None:
            continue
        interp = Z[lo] + (Z[hi] - Z[lo]) * (i - lo) / (hi - lo)
        occ.setdefault(t, []).append((_season(*_from_idx(i)), Z[i] - interp))
    delta = {}
    for t, xs in occ.items():
        allv = [x for _, x in xs]
        rec = [x for s, x in xs if s >= 2024]
        if p["recent"] and t in ("autumn", "xmas1", "xmas2", "eastermon") and len(rec) >= 2:
            delta[t] = _median(rec)
        elif len(allv) >= 2:
            delta[t] = _median(allv)
    D = lambda i: delta.get(htype(*_from_idx(i)), 0.0) if htype(*_from_idx(i)) else 0.0
    A = {i: z - D(i) for i, z in Z.items()}
    # profil růstu z minulých sezón
    by_w = {}
    for i, a in A.items():
        s = _season(*_from_idx(i))
        if s in COVID_SEASONS or s >= S0 or (i - 1) not in A:
            continue
        by_w.setdefault(_wk(_from_idx(i)[1]), []).append(a - A[i - 1])
    def g(w):
        w = _wk(w)
        xs = []
        for d in range(-p["win"], p["win"] + 1):
            ww = (w - 1 + d) % 52 + 1
            xs += by_w.get(ww, [])
        return _median(xs)
    n = p["n"]
    ch = [A[i0 - k] - A[i0 - k - 1] for k in range(n) if (i0 - k) in A and (i0 - k - 1) in A]
    if ch:
        s_t = max(-p["clip"], min(p["clip"], sum(ch) / len(ch)))
        gbar = sum(g(_from_idx(i0 - k)[1]) for k in range(len(ch))) / len(ch)
        anom = s_t - gbar
    else:
        anom = 0.0
    out, a = [], A[i0]
    for h in range(1, H + 1):
        i = i0 + h
        a += g(_from_idx(i)[1]) + p["phi"] ** h * anom
        out.append(a + D(i))
    return out


def predict(hist, origin, H):
    return forecast(hist, origin, H)
