#!/usr/bin/env python3
"""Produkční předpověď ARI pro dashboard: bodový model + kalibrované intervaly.

make_forecast(series) vrátí předpověď na 1–10 týdnů od posledních dat (SZÚ data
mají 1–2 týdny zpoždění, takže to je zhruba 2 měsíce od dneška):
  median           – bodová předpověď (na 100 000)
  q10, q25, q75, q90 – hranice intervalů 50 % a 80 %
  p_750/p_1000/p_1500 – pravděpodobnost, že hodnota bude ≥ hranice pásma

Intervaly NEJSOU odhadnuté „od oka": při každém běhu se model zpětně otestuje
na všech historických sezónách (rolling origin, mimo COVID 2019–2021) a
použijí se skutečné chyby z týdnů ve stejné fázi sezóny (±6 týdnů od
aktuálního týdne). Tím je nejistota na podzim jiná než při nástupu chřipky.
"""
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import forecast as F
import model_analog
import model_profile_holiday

H = 10
EVAL_EXCLUDE = (2019, 2020, 2021)
PHASE_WINDOW = 6          # ± týdnů pro výběr historických chyb
MIN_PHASE_SAMPLES = 60    # jinak se použijí chyby z celého roku
THRESHOLDS = (750, 1000, 1500)

MODEL_NAME = "blend_analog_profile_holiday"
MODEL_LABEL = "Analogové sezóny + sezónní profil se svátkovými efekty (vážený průměr v logaritmu)"
# váha analogových sezón podle horizontu (zbytek = sezónní profil se svátky);
# vybráno na ladicích sezónách 2012–2018: profil je přesnější na 1–3 týdny,
# analogy na delší horizont
W_ANALOG = (0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)   # dál 1.0


def point_forecast(hist, origin, H=H):
    """log-předpovědi pro h = 1..H (jen z dat do origin)."""
    a = model_analog.predict(hist, origin, H)
    b = model_profile_holiday.predict(hist, origin, H)
    return [W_ANALOG[min(i, len(W_ANALOG) - 1)] * x + (1 - W_ANALOG[min(i, len(W_ANALOG) - 1)]) * y
            for i, (x, y) in enumerate(zip(a, b))]


def _week_dist(w1, w2):
    d = abs(F.wk(w1) - F.wk(w2))
    return min(d, 52 - d)


def backtest_residuals(series, model=point_forecast, H=H):
    """Chyby (pred − skutečnost, v log) ze všech historických origin týdnů."""
    keys = sorted(series, key=lambda k: F.week_index(*k))
    out = []
    for origin in keys:
        if F.season_of(*origin) in EVAL_EXCLUDE:
            continue
        hist = F.history_upto(series, origin)
        if len({F.season_of(*k) for k in hist}) < 4:
            continue
        try:
            preds = model(hist, origin, H)
        except Exception:
            continue
        for h, lp in enumerate(preds, 1):
            act = series.get(F.week_add(*origin, h))
            if act:
                out.append((origin, h, lp - math.log(act)))
    return out


def _quantile(xs, q):
    xs = sorted(xs)
    pos = q * (len(xs) - 1)
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def skill_summary(residuals, legacy_residuals=None):
    """Souhrn přesnosti pro zobrazení na dashboardu."""
    def mdae(rs, h):
        e = sorted(abs(x[2]) for x in rs if x[1] == h)
        return (math.exp(e[len(e) // 2]) - 1) * 100 if e else None
    s = {"mdae_pct_by_h": [round(mdae(residuals, h), 1) for h in range(1, H + 1)]}
    if legacy_residuals:
        s["legacy_mdae_pct_by_h"] = [round(mdae(legacy_residuals, h), 1) for h in range(1, H + 1)]
    s["n_origins"] = len({x[0] for x in residuals})
    s["seasons"] = sorted({F.season_of(*x[0]) for x in residuals})
    return s


def make_forecast(series, origin=None):
    keys = sorted(series, key=lambda k: F.week_index(*k))
    origin = origin or keys[-1]
    hist = F.history_upto(series, origin)
    lp = point_forecast(hist, origin, H)

    res = backtest_residuals(series)
    out = []
    for h in range(1, H + 1):
        near = [e for (o, hh, e) in res if hh == h and _week_dist(o[1], origin[1]) <= PHASE_WINDOW]
        errs = near if len(near) >= MIN_PHASE_SAMPLES else [e for (_, hh, e) in res if hh == h]
        y, w = F.week_add(*origin, h)
        # log(skutečnost) = pred − chyba  →  kvantil p skutečnosti = pred − kvantil (1−p) chyby
        q = {p: math.exp(lp[h - 1] - _quantile(errs, 1 - p)) for p in (0.1, 0.25, 0.5, 0.75, 0.9)}
        probs = {f"p_{t}": round(sum(1 for e in errs if lp[h - 1] - e >= math.log(t)) / len(errs), 3)
                 for t in THRESHOLDS}
        out.append({
            "week": f"{y}-W{w:02d}", "year": y, "iso_week": w,
            "monday": F.monday(y, w).isoformat(), "horizon": h,
            "median": round(q[0.5]), "point": round(math.exp(lp[h - 1])),
            "q10": round(q[0.1]), "q25": round(q[0.25]),
            "q75": round(q[0.75]), "q90": round(q[0.9]),
            **probs,
        })
    legacy = backtest_residuals(series, lambda hh, o, H: F.m_legacy(hh, o, H))
    return {
        "forecast": out,
        "model": {
            "name": MODEL_NAME, "label": MODEL_LABEL,
            "origin": f"{origin[0]}-W{origin[1]:02d}",
            "interval_method": f"empirické chyby zpětného testu, fáze sezóny ±{PHASE_WINDOW} týdnů",
            "skill": skill_summary(res, legacy),
        },
    }


if __name__ == "__main__":
    import json
    p = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).parent.parent / "data" / "ari_data.json")
    d = json.load(open(p))
    rows = d["history"] if isinstance(d, dict) else d
    ser = {(r["year"], r["iso_week"]): (r.get("ari_per_100k") or r.get("ari")) for r in rows}
    ser = {k: v for k, v in ser.items() if v}
    fc = make_forecast(ser)
    print(json.dumps(fc["model"], ensure_ascii=False, indent=1))
    for f in fc["forecast"]:
        print(f"{f['week']} (po {f['monday']}): {f['median']:5d}  50%: {f['q25']}–{f['q75']}  80%: {f['q10']}–{f['q90']}  "
              f"P≥1000 {f['p_1000']:.0%}  P≥1500 {f['p_1500']:.0%}")
