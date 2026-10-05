#!/usr/bin/env python3
"""Sezónní profil růstu + svátkové efekty + tlumená odchylka trendu.

  a_t      = log y_t − δ(τ_t)                 (očištěno o svátky)
  g[w]     = medián týdenních změn a v týdnu w (±1 týden) přes minulé sezóny
  s_t      = průměr posledních n změn a (letošní tempo), ḡ_t = profil za stejné týdny
  ẑ_{t+h}  = a_t + Σ_{i≤h} (g[w(t+i)] + φ^i·(s_t − ḡ_t)) + δ(τ_{t+h})

Zpětný test vybral φ = 0: letošní tempo nad rámec sezónního profilu předpověď
nezlepšilo (φ = 0,3 a 0,5 vyšly hůř), takže růst se bere z profilu minulých
sezón a navazuje na aktuální (svátky očištěnou) úroveň.

Svátkové typy podle data a dne v týdnu (ne čísla týdne) – viz holiday().
δ = medián rozdílu log hodnoty
proti log-lineární interpolaci mezi nejbližšími nesvátkovými sousedy.
U podzimních prázdnin, Vánoc a Velikonočního pondělí se od sezóny 2024/25
berou jen novější výskyty (pokud jsou aspoň 2) – propady jsou od zavedení
automatizovaného hlášení výrazně hlubší (W44: −25 až −30 % místo −10 až −15 %).

Čistý Python, deterministické. Kontrakt: predict(hist, origin, H) -> [log ARI].
"""
import math
from datetime import date, timedelta
from functools import lru_cache

COVID_SEASONS = (2019, 2020, 2021)
P = dict(n=3, phi=0.0, win=1, recent=True, clip=0.35)
RECENT_FROM = 2024   # sezóna, od které se berou „nové" svátkové efekty


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


_MINOR = ((5, 1, "1. května"), (5, 8, "8. května"), (9, 28, "28. září"),
          (11, 17, "17. listopadu"), (7, 5, "5. července"), (7, 6, "6. července"))


@lru_cache(maxsize=None)
def holiday(y, w):
    """(typ, popis) svátku v ISO týdnu (y, w), nebo (None, None).

    Typy podle data a dne v týdnu (volný musí být pracovní den):
      xmas1     – týden s 27. 12. (hlavní vánoční propad: 24.–26. 12. + „mezi svátky“;
                  když 25. 12. připadne na víkend, je to až týden po něm)
      xmas2     – týden s 1. 1. ve všední den (pokud to není už týden xmas1)
      autumn    – 28. 10. ve všední den (+ podzimní prázdniny v témž týdnu); když
                  28. 10. připadne na víkend, propad v datech vidět není (2012, 2017,
                  2018, 2023), proto se takový týden nepočítá
      goodfri / eastermon – Velký pátek / Velikonoční pondělí
      minor     – 1. 5., 8. 5., 28. 9., 17. 11., 5. 7., 6. 7. ve všední den, nebo Štědrý
                  den ve všední den mimo týden xmas1
    """
    mon = date.fromisocalendar(y, w, 1)
    days = [mon + timedelta(d) for d in range(7)]
    wd = days[:5]
    for Y in sorted({mon.year, days[-1].year}):
        if date(Y, 12, 27) in days:
            return "xmas1", "Vánoce"
        if date(Y, 1, 1) in wd:
            return "xmas2", "Nový rok"
        if date(Y, 12, 24) in wd:
            return "minor", "Štědrý den"
        if date(Y, 10, 28) in wd:
            return "autumn", "28. října a podzimní prázdniny"
        e = _easter(Y)
        if e - timedelta(2) in days:
            return "goodfri", "Velký pátek"
        if e + timedelta(1) in days:
            return "eastermon", "Velikonoční pondělí"
        hits = [lab for m_, d_, lab in _MINOR if date(Y, m_, d_) in wd]
        if hits:
            return "minor", "5.–6. července" if len(hits) == 2 and "července" in hits[0] else " a ".join(hits)
    return None, None


def htype(y, w):
    return holiday(y, w)[0]


def _wk(w):
    return 52 if w == 53 else w


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return (xs[(n - 1) // 2] + xs[n // 2]) / 2 if n else 0.0


def log_series(hist, upto=None):
    """{index týdne: log hodnota} (jen kladné hodnoty; s upto jen týdny ≤ upto)."""
    out = {_widx(y, w): math.log(v) for (y, w), v in hist.items() if v and v > 0}
    return out if upto is None else {i: z for i, z in out.items() if i <= upto}


def holiday_effects(Z, recent=True):
    """δ pro každý typ svátku: medián (log hodnota − log-lineární interpolace mezi
    nejbližšími nesvátkovými sousedy do 3 týdnů); covidové sezóny se vynechávají."""
    tau = {i: htype(*_from_idx(i)) for i in Z}
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
        rec = [x for s, x in xs if s >= RECENT_FROM]
        if recent and t in ("autumn", "xmas1", "xmas2", "eastermon") and len(rec) >= 2:
            delta[t] = _median(rec)
        elif len(allv) >= 2:
            delta[t] = _median(allv)
    return delta


def effect(i, delta):
    """Svátkový efekt (log) pro týden s indexem i."""
    t = htype(*_from_idx(i))
    return delta.get(t, 0.0) if t else 0.0


def forecast(hist, origin, H, p=None):
    p = {**P, **(p or {})}
    io = _widx(*origin)
    Z = log_series(hist, io)          # data po origin se nikdy nepoužijí
    known = list(Z)
    if not known:
        return [0.0] * H
    i0 = max(known)                 # chybí-li týden origin, navážeme na poslední známý
    gap = io - i0
    S0 = _season(*_from_idx(io))
    delta = holiday_effects(Z, p["recent"])
    D = lambda i: effect(i, delta)
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
    for h in range(1, gap + H + 1):
        i = i0 + h
        a += g(_from_idx(i)[1]) + p["phi"] ** h * anom
        out.append(a + D(i))
    return out[gap:]


def predict(hist, origin, H):
    return forecast(hist, origin, H)
