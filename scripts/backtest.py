#!/usr/bin/env python3
"""Zpětné testování předpovědních modelů ARI (rolling origin).

Pro každý týden testovacích sezón se model „vrátí v čase": dostane jen data
do toho týdne a předpoví 1..H týdnů dopředu. Porovnáme se skutečností.

Sezóny (sezóna S = W27 roku S … W26 roku S+1):
  tune     – 2012..2018   (na nich se smí ladit parametry)
  holdout  – 2022..2025   (kontrolní; ladit podle nich se NESMÍ)
  COVID 2019–2021 se nehodnotí (lockdowny, netypické sezóny).

Metriky (v logaritmu → ~ relativní chyba):
  MAE%   – medián |chyby| v %      mean% – průměr |chyby| v %
  bias%  – průměrná chyba v % (+ = model nadhodnocuje)
  dir    – správný směr (růst/pokles) tam, kde se skutečnost změnila o >5 %
  cov50/cov80 – pokrytí intervalů 50 % / 80 % (intervaly z rezidují tune části)

Použití:
  python3 scripts/backtest.py [series.json] [--candidate path.py ...]
         [--models a,b] [--split tune|holdout|all] [--json out.json] [--phase]
Kandidátní modul musí definovat  predict(hist, origin, H) -> [log-hodnoty h=1..H]
(hist = {(rok, týden): hodnota} obsahuje JEN data do origin včetně).
"""
import argparse, importlib.util, json, math, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import forecast as F

TUNE = tuple(range(2012, 2019))
HOLDOUT = tuple(range(2022, 2026))
EVAL_EXCLUDE = (2019, 2020, 2021)
PHASES = {
    "podzim W34–W48": lambda w: 34 <= w <= 48,
    "zima W49–W10":   lambda w: w >= 49 or w <= 10,
    "jaro W11–W26":   lambda w: 11 <= w <= 26,
    "léto W27–W33":   lambda w: 27 <= w <= 33,
}

def load_series(path):
    """Řada {(rok, týden): ARI} z research řady (list) i z data/ari_data.json (dict)."""
    data = json.load(open(path))
    rows = data["history"] if isinstance(data, dict) else data
    out = {}
    for r in rows:
        v = r.get("ari_per_100k", r.get("ari"))
        if v:
            out[(int(r["year"]), int(r["iso_week"]))] = float(v)
    return out

def load_candidate(path):
    spec = importlib.util.spec_from_file_location(Path(path).stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return Path(path).stem, mod.predict

def run_backtest(series, models, H=8, seasons=None):
    """models: {name: fn(hist, origin, H)} → {name: [záznamy]}"""
    keys = sorted(series, key=lambda k: F.week_index(*k))
    res = {m: [] for m in models}
    for origin in keys:
        s = F.season_of(*origin)
        if s in EVAL_EXCLUDE or (seasons is not None and s not in seasons):
            continue
        hist = F.history_upto(series, origin)
        for name, fn in models.items():
            try:
                preds = fn(hist, origin, H)
            except Exception as e:
                print(f"  ! {name} {origin}: {e!r}", file=sys.stderr)
                continue
            for h, lp in enumerate(preds[:H], 1):
                act = series.get(F.week_add(*origin, h))
                if not act or lp is None or not math.isfinite(lp):
                    continue
                res[name].append({"h": h, "err": lp - math.log(act), "origin": origin,
                                  "chg": math.log(act / series[origin]),
                                  "pchg": lp - math.log(series[origin])})
    return res

def quantile(xs, q):
    xs = sorted(xs)
    if not xs:
        return 0.0
    pos = q * (len(xs) - 1)
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)

def residual_quantiles(recs, H=8, qs=(0.1, 0.25, 0.5, 0.75, 0.9)):
    """Empirické kvantily chyby (log) podle horizontu → šířky intervalů."""
    out = {}
    for h in range(1, H + 1):
        e = [x["err"] for x in recs if x["h"] == h]
        out[h] = {q: quantile(e, q) for q in qs}
    return out

def summarize(recs, H=8, phase=None, rq=None):
    out = {}
    for h in range(1, H + 1):
        r = [x for x in recs if x["h"] == h and (phase is None or phase(x["origin"][1]))]
        if not r:
            continue
        ae = sorted(abs(x["err"]) for x in r)
        dirs = [x for x in r if abs(x["chg"]) > math.log(1.05)]
        d = {
            "n": len(r),
            "mae_pct": (math.exp(ae[len(ae) // 2]) - 1) * 100,
            "mean_pct": (math.exp(sum(ae) / len(ae)) - 1) * 100,
            "bias_pct": (math.exp(sum(x["err"] for x in r) / len(r)) - 1) * 100,
            "dir": (sum(1 for x in dirs if (x["pchg"] > 0) == (x["chg"] > 0)) / len(dirs) * 100) if dirs else float("nan"),
        }
        if rq:  # pokrytí intervalů: skutečnost uvnitř [pred - q_hi, pred - q_lo]
            q = rq[h]
            d["cov50"] = sum(1 for x in r if q[0.25] <= x["err"] <= q[0.75]) / len(r) * 100
            d["cov80"] = sum(1 for x in r if q[0.1] <= x["err"] <= q[0.9]) / len(r) * 100
        out[h] = d
    return out

def score(summary, H=8):
    """Jedno číslo pro srovnání: průměr mediánu |chyby| přes h=1..H (menší = lepší)."""
    v = [summary[h]["mae_pct"] for h in range(1, H + 1) if h in summary]
    return sum(v) / len(v) if v else float("inf")

def print_table(name_to_summary, title, H=8):
    print(f"\n=== {title} — medián |chyby| % podle horizontu ===")
    print(f"{'model':22}" + "".join(f" h={h:<4}" for h in range(1, H + 1)) + "  skóre  dir1-4  cov50 cov80")
    for name, s in name_to_summary.items():
        cells = "".join(f" {s[h]['mae_pct']:5.1f}" if h in s else "     –" for h in range(1, H + 1))
        dirs = [s[h]["dir"] for h in range(1, 5) if h in s]
        c50 = [s[h].get("cov50") for h in s if s[h].get("cov50") is not None]
        c80 = [s[h].get("cov80") for h in s if s[h].get("cov80") is not None]
        extra = f"  {sum(c50)/len(c50):5.1f} {sum(c80)/len(c80):5.1f}" if c50 else ""
        print(f"{name:22}{cells}  {score(s, H):5.1f}  {sum(dirs)/len(dirs) if dirs else float('nan'):5.1f}{extra}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("series", nargs="?", default="research/derived/combined_series.json")
    ap.add_argument("--candidate", action="append", default=[])
    ap.add_argument("--models", default=",".join(F.MODELS))
    ap.add_argument("--split", default="both", choices=["tune", "holdout", "all", "both"])
    ap.add_argument("--json")
    ap.add_argument("--phase", action="store_true", help="rozpad podle fáze sezóny")
    ap.add_argument("-H", type=int, default=8)
    a = ap.parse_args()
    series = load_series(a.series)
    models = {m: (lambda f: (lambda h, o, H: f(h, o, H)))(F.MODELS[m]) for m in a.models.split(",") if m}
    for c in a.candidate:
        n, fn = load_candidate(c)
        models[n] = fn
    print(f"Řada: {len(series)} týdnů; modely: {', '.join(models)}")
    report = {}
    tune_res = run_backtest(series, models, a.H, TUNE)
    rq = {m: residual_quantiles(r, a.H) for m, r in tune_res.items()}
    splits = {"tune": tune_res}
    if a.split in ("holdout", "both", "all"):
        splits["holdout"] = run_backtest(series, models, a.H, HOLDOUT)
    if a.split == "all":
        splits["all"] = run_backtest(series, models, a.H, None)
    for sp, res in splits.items():
        if a.split not in ("both", "all") and sp != a.split and not (a.split == "holdout" and sp == "tune"):
            continue
        sums = {m: summarize(r, a.H, rq=rq[m]) for m, r in res.items()}
        print_table(sums, f"{sp} ({'2012–2018' if sp=='tune' else '2022–2025' if sp=='holdout' else 'vše'})", a.H)
        report[sp] = {m: {"score": score(s, a.H), "by_h": s} for m, s in sums.items()}
        if a.phase:
            for pn, pf in PHASES.items():
                print_table({m: summarize(r, a.H, pf, rq=rq[m]) for m, r in res.items()}, f"{sp} · {pn}", a.H)
                report[sp + " · " + pn] = {m: {"score": score(summarize(r, a.H, pf), a.H)} for m, r in res.items()}
    report["residual_quantiles_tune"] = {m: {h: q for h, q in v.items()} for m, v in rq.items()}
    if a.json:
        json.dump(report, open(a.json, "w"), indent=1, default=str)

if __name__ == "__main__":
    main()
