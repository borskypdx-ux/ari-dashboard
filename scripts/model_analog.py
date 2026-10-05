#!/usr/bin/env python3
"""Analogové sezóny – předpověď týdenní nemocnosti ARI (log) na h = 1..H.

Myšlenka: letošní nedávný průběh (posledních k týdnů, log úroveň) porovnáme se
stejnými kalendářními týdny všech minulých ne-COVID sezón. Každá minulá
sezóna („analog") pak promítne letošní úroveň dopředu svou vlastní křivkou;
analogy s podobnějším tvarem posledních týdnů váží víc.

Zarovnání sezón je kalendářní (ne podle čísla ISO týdne): týden analogu je
ten, který obsahuje stejné datum jako čtvrtek letošního týdne (posun o celé
roky od 1. 7.). Tím se korektně páruje Vánoční týden, podzimní prázdniny
i týden 53 (v 53týdenních letech ↔ Novoroční týden ostatních let).

Předpověď z analogu S:   f_S(h) = z_S(t+h) + o_long + (o_now − o_long)·rho^h
   z_S     … log úroveň analogu na zarovnaném týdnu t+h
   o_now   … letos − analog v týdnu původu (aktuální anomálie)
   o_long  … průměr (letos − analog) za posledních L = 104 týdnů: trvalý
             rozdíl úrovní (např. vyšší základna po COVIDu, jiné škálování);
             2 roky průměrují i dvouletý cyklus velikosti epidemií
   Aktuální anomálie se k trvalému rozdílu vrací s útlumem rho^h.
Výsledek = vážený průměr f_S přes analogy (K nejbližších; ladění ukázalo, že
nejlepší je ponechat všechny a jen mírně vážit), váha exp(-d²/2τ²), kde d je
RMS rozdílu tvaru posledních k týdnů po odečtení posunu (novější týdny váží
víc, decay^j).

Týdny COVID sezón (2019/20–2021/22) se nepoužívají vůbec (ani jako analog,
ani v oknech posunů). Chybějící týdny se přeskakují; analog bez dostatku dat
v okně se vynechá.

Čistý Python, deterministické, nečte soubory ani globální data.
Kontrakt: predict(hist, origin, H) -> [log ARI pro týdny origin+1..origin+H].
"""
import math
from datetime import date

COVID_SEASONS = (2019, 2020, 2021)

DEFAULTS = dict(
    k=6,            # délka porovnávaného okna tvaru (týdny)
    min_pairs=3,    # min. počet společných týdnů v okně, jinak analog vynechat
    decay=0.7,      # váha týdne j zpět ve vzdálenosti tvaru = decay**j
    K=30,           # max. počet analogů (30 = prakticky všechny)
    tau=0.25,       # šířka gaussovských vah podle vzdálenosti (log)
    L=104,          # okno trvalého posunu letos vs analog (týdny)
    rho=0.80,       # útlum aktuální anomálie k trvalému posunu (rho**h)
)


def _widx(y, w):
    return date.fromisocalendar(y, w, 1).toordinal() // 7


def _season(y, w):
    return y if w >= 27 else y - 1


def _season_of_index(i):
    iy, iw, _ = date.fromordinal(i * 7 + 1).isocalendar()
    return _season(iy, iw)


def _logmap(hist):
    """{index týdne: log úroveň}; týdny COVID sezón se vynechávají úplně."""
    out = {}
    for (y, w), v in hist.items():
        if v and v > 0 and _season(y, w) not in COVID_SEASONS:
            out[_widx(y, w)] = math.log(max(v, 1.0))
    return out


def _shift(S0, S):
    """Posun indexu týdne: letošní týden i ↔ týden analogu i + shift.
    Týden analogu obsahuje stejné datum jako čtvrtek letošního týdne."""
    delta = (date(S0, 7, 1) - date(S, 7, 1)).days
    return (3 - delta) // 7


def _wmean(pairs):
    sw = sum(w for w, _ in pairs)
    return sum(w * x for w, x in pairs) / sw if sw > 0 else None


def forecast(hist, origin, H, p=None):
    P = dict(DEFAULTS)
    if p:
        P.update(p)
    k, K, tau, decay = P["k"], P["K"], P["tau"], P["decay"]
    L, rho = P["L"], P["rho"]

    LI = _logmap(hist)
    i0 = _widx(*origin)
    if i0 not in LI:  # origin chybí → poslední dostupný týden
        prev = [i for i in LI if i <= i0]
        if not prev:
            v = hist.get(origin) or next((x for x in reversed(list(hist.values())) if x), 1.0)
            return [math.log(max(v, 1.0))] * H
        i0 = max(prev)
    x0 = LI[i0]
    S0 = _season(*origin)
    seasons = sorted({_season_of_index(i) for i in LI})
    seasons = [s for s in seasons if s < S0 and s not in COVID_SEASONS]

    cands = []
    for S in seasons:
        sh = _shift(S0, S)
        # trvalý posun: průměr (letos − analog) za posledních L týdnů
        lng = []
        for j in range(L):
            a, b = LI.get(i0 - j), LI.get(i0 - j + sh)
            if a is not None and b is not None:
                lng.append(a - b)
        # tvar posledních k týdnů
        win = []
        for j in range(k):
            a, b = LI.get(i0 - j), LI.get(i0 - j + sh)
            if a is not None and b is not None:
                win.append((decay ** j, a - b))
        if len(win) < max(P["min_pairs"], (k + 1) // 2):
            continue
        o_win = _wmean(win)
        d2 = _wmean([(w, (r - o_win) ** 2) for w, r in win])
        z0 = LI.get(i0 + sh)
        o_now = x0 - z0 if z0 is not None else o_win
        o_long = sum(lng) / len(lng) if lng else o_win
        fut = [LI.get(i0 + h + sh) for h in range(1, H + 1)]
        if all(f is None for f in fut):
            continue
        cands.append(dict(S=S, d2=d2, o_now=o_now, o_long=o_long, fut=fut))
    if not cands:
        return [x0] * H

    cands.sort(key=lambda c: (c["d2"], c["S"]))
    sel = cands[:K]
    for c in sel:
        c["w"] = math.exp(-c["d2"] / (2 * tau * tau)) if tau > 0 else 1.0
    if sum(c["w"] for c in sel) <= 0:
        for c in sel:
            c["w"] = 1.0

    out = []
    last = x0
    for h in range(1, H + 1):
        damp = rho ** h
        acc = [(c["w"], c["fut"][h - 1] + c["o_long"] + (c["o_now"] - c["o_long"]) * damp)
               for c in sel if c["fut"][h - 1] is not None]
        v = _wmean(acc) if acc else None
        if v is None:  # žádný analog nemá data pro tento týden
            v = last
        out.append(v)
        last = v
    return out


def predict(hist, origin, H):
    return forecast(hist, origin, H)
