#!/usr/bin/env python3
"""Předpověď týdenní nemocnosti ARI (na 100 000) na 1–8 týdnů dopředu.

Čistý Python (bez numpy), aby běžel v CI i lokálně. Řada je slovník
{(iso_rok, iso_týden): hodnota}. Každý model dostane jen data do „origin"
(včetně) – tím je zaručeno, že zpětné testování nenahlíží do budoucnosti.

Kandidátní modely (všechny pracují v logaritmu – nemocnost roste/klesá
multiplikativně):
  persistence        – hodnota zůstane stejná (referenční minimum)
  legacy             – původní model dashboardu (0,5×MA4 + 0,5×loňsko×YoY)
  seasonal_naive     – letošní úroveň × loňský poměr změny
  profile            – typický týdenní růst pro daný týden roku (medián
                       přes minulé sezóny) – „analogová" sezónní křivka
  profile_momentum   – profil + utlumená odchylka letošního trendu od profilu
  clim_anomaly       – sezónní klimatologie úrovně + perzistentní odchylka
                       (letos výš/níž než obvykle) s útlumem AR(1)
"""
import math
import statistics as st
from datetime import date, timedelta

# ── ISO týdny ────────────────────────────────────────────────────────────────
def weeks_in_isoyear(y):
    return date(y, 12, 28).isocalendar()[1]

def monday(y, w):
    return date.fromisocalendar(y, w, 1)

def week_add(y, w, k):
    d = monday(y, w) + timedelta(weeks=k)
    iy, iw, _ = d.isocalendar()
    return (iy, iw)

def week_index(y, w):
    """Lineární index týdne (pro rozdíly napříč roky, i 53týdenní)."""
    return monday(y, w).toordinal() // 7

def week_from_index(i):
    """Inverze week_index. Pondělí mají ordinal ≡ 1 (mod 7), proto +1."""
    iy, iw, _ = date.fromordinal(i * 7 + 1).isocalendar()
    return (iy, iw)

def season_of(y, w):
    """Sezóna = W27 roku y … W26 roku y+1 (respirační sezóna přes přelom roku)."""
    return y if w >= 27 else y - 1

def wk(w):
    """Týden pro sezónní profil – W53 se slučuje s W52 (vzácný, málo dat)."""
    return 52 if w == 53 else w

# ── Pomocné ──────────────────────────────────────────────────────────────────
def _log(v):
    return math.log(max(v, 1.0))

def _median(xs, default=0.0):
    xs = [x for x in xs if x is not None]
    return st.median(xs) if xs else default

def history_upto(series, origin):
    oi = week_index(*origin)
    return {k: v for k, v in series.items() if v and week_index(*k) <= oi}

# ── Sezónní profily z historie ───────────────────────────────────────────────
def growth_profile(hist, exclude_seasons=(), min_n=3):
    """g[w] = medián log(y[w+1]/y[w]) přes sezóny; klíčem je týden w."""
    acc = {}
    for (y, w), v in hist.items():
        if season_of(y, w) in exclude_seasons:
            continue
        nk = week_add(y, w, 1)
        nv = hist.get(nk)
        if not nv:
            continue
        acc.setdefault(wk(w), []).append(math.log(nv / v))
    return {w: _median(gs) for w, gs in acc.items() if len(gs) >= min_n}

def level_climatology(hist, exclude_seasons=(), min_n=3):
    """c[w] = medián log úrovně v týdnu w přes sezóny."""
    acc = {}
    for (y, w), v in hist.items():
        if season_of(y, w) in exclude_seasons:
            continue
        acc.setdefault(wk(w), []).append(_log(v))
    return {w: _median(ls) for w, ls in acc.items() if len(ls) >= min_n}

# COVID sezóny 2020/21 a 2021/22 mají netypický průběh (lockdowny, uzavřené
# školy) – pro sezónní profil je vynecháváme.
COVID_SEASONS = (2019, 2020, 2021)  # 2019/20 končí lockdownem od W11/2020

def _profile_excl(origin_season):
    return tuple(s for s in COVID_SEASONS) + (origin_season,)

# ── Modely: vrací list log-předpovědí pro h = 1..H ───────────────────────────
def m_persistence(hist, origin, H, **kw):
    return [_log(hist[origin])] * H

def m_legacy(hist, origin, H, **kw):
    """Původní model dashboardu (pro srovnání)."""
    keys = sorted(hist, key=lambda k: week_index(*k))
    last = keys[-4:]
    ma4 = sum(hist[k] for k in last) / len(last)
    y0, w0 = origin
    out = []
    for h in range(1, H + 1):
        fy, fw = week_add(y0, w0, h)
        p = hist.get((fy - 1, fw))
        p0 = hist.get((y0 - 1, w0))
        if p and p > 0:
            yoy = hist[origin] / (p0 or p)
            fc = 0.5 * ma4 + 0.5 * p * yoy
        else:
            fc = ma4 * 0.92
        out.append(_log(fc))
    return out

def m_seasonal_naive(hist, origin, H, **kw):
    y0, w0 = origin
    base = hist.get((y0 - 1, wk(w0)))
    out = []
    for h in range(1, H + 1):
        fy, fw = week_add(y0, w0, h)
        prev = hist.get((fy - 1, wk(fw)))
        if base and prev:
            out.append(_log(hist[origin]) + math.log(prev / base))
        else:
            out.append(_log(hist[origin]))
    return out

def m_profile(hist, origin, H, exclude=None, **kw):
    excl = exclude if exclude is not None else _profile_excl(season_of(*origin))
    g = growth_profile(hist, excl)
    y0, w0 = origin
    lv = _log(hist[origin]); out = []
    cy, cw = y0, w0
    for h in range(1, H + 1):
        lv += g.get(wk(cw), 0.0)
        cy, cw = week_add(cy, cw, 1)
        out.append(lv)
    return out

def recent_anomaly_growth(hist, origin, g, k=2):
    """Průměrná odchylka letošního týdenního růstu od profilu za posledních k týdnů."""
    devs = []
    cy, cw = origin
    for _ in range(k):
        py, pw = week_add(cy, cw, -1)
        a, b = hist.get((py, pw)), hist.get((cy, cw))
        if a and b and wk(pw) in g:
            devs.append(math.log(b / a) - g[wk(pw)])
        cy, cw = py, pw
    return sum(devs) / len(devs) if devs else 0.0

def m_profile_momentum(hist, origin, H, phi=0.6, k=2, exclude=None, **kw):
    excl = exclude if exclude is not None else _profile_excl(season_of(*origin))
    g = growth_profile(hist, excl)
    a = recent_anomaly_growth(hist, origin, g, k)
    y0, w0 = origin
    lv = _log(hist[origin]); out = []
    cy, cw = y0, w0
    for h in range(1, H + 1):
        lv += g.get(wk(cw), 0.0) + a * (phi ** h)
        cy, cw = week_add(cy, cw, 1)
        out.append(lv)
    return out

def m_clim_anomaly(hist, origin, H, rho=0.92, smooth=2, exclude=None, **kw):
    """Klimatologie úrovně + perzistentní anomálie (letos výš/níž než obvykle)."""
    excl = exclude if exclude is not None else _profile_excl(season_of(*origin))
    c = level_climatology(hist, excl)
    y0, w0 = origin
    # anomálie = průměr posledních `smooth` týdnů (tlumí svátkový šum)
    an = []
    cy, cw = y0, w0
    for _ in range(smooth):
        v = hist.get((cy, cw))
        if v and wk(cw) in c:
            an.append(_log(v) - c[wk(cw)])
        cy, cw = week_add(cy, cw, -1)
    if not an or wk(w0) not in c:
        return m_profile(hist, origin, H, exclude=excl)
    a0 = an[0] * 0.7 + (sum(an) / len(an)) * 0.3
    out = []
    for h in range(1, H + 1):
        fy, fw = week_add(y0, w0, h)
        base = c.get(wk(fw))
        if base is None:
            out.append(out[-1] if out else _log(hist[origin]))
        else:
            out.append(base + a0 * (rho ** h))
    return out

MODELS = {
    "persistence": m_persistence,
    "legacy": m_legacy,
    "seasonal_naive": m_seasonal_naive,
    "profile": m_profile,
    "profile_momentum": m_profile_momentum,
    "clim_anomaly": m_clim_anomaly,
}
