#!/usr/bin/env python3
"""Jednorázové sestavení dlouhé historie ARI (2009 → současnost) na měřítku SZÚ.

Zdroje:
  * SZÚ týdenní PDF (od 2023-W40) – oficiální „Relativní nemocnost ARI na 100 000"
    (jednorázově zpětně stažené tabulky z PDF jako JSON [{year, week, ari, ili,
    ari_groups, url}], průběžně je doplňuje fetch_ari_data.py)
  * WHO FluID (xmart API, VIW_FID_EPI, země CZE) – týdenní ARI_CASE / ARI_POP_COV
    za věkové skupiny 0–4, 5–14, 15–64, 65+ (sečteno). Měřítko se liší od SZÚ
    (jiná pokrytá populace), proto se násobí kalibračním poměrem SZÚ/WHO:
      ARI 1,20 (medián, 136 společných týdnů 2023–2026, sd ~5 %; nezávisle
      potvrzeno 45 hodnotami ze zpráv NRL 2018–2020: 1,18)
      ILI 1,163
  * jednotlivé chybějící týdny mezi dvěma známými → geometrický průměr
    (source = "interpolated"); delší mezery (léto 2011, 2014) zůstávají prázdné

Použití:
  python3 scripts/import_history.py <szu_tables.json> <who_weekly.json|who_fluid.csv>
  (WHO CSV: https://xmart-api-public.who.int/FLUMART/VIW_FID_EPI?$format=csv&$filter=COUNTRY_CODE%20eq%20%27CZE%27)
Výsledek se zapíše do data/ari_data.json (history + current + forecast + model);
oficiální týdny SZÚ, které už v souboru jsou, se zachovají.
"""
import csv, json, math, sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import forecast as F
import fetch_ari_data as S

ARI_RATIO, ILI_RATIO = 1.20, 1.163
DATA = Path(__file__).parent.parent / "data" / "ari_data.json"


def who_rates(path):
    """→ {(rok, týden): (ari_per_100k, ili_per_100k)} na měřítku WHO."""
    if path.endswith(".json"):
        return {(r["year"], r["iso_week"]): (r["ari"], r.get("ili")) for r in json.load(open(path))}
    acc = {}
    for r in csv.DictReader(open(path, encoding="utf-8-sig")):
        if r["AGEGROUP_CODE"] not in ("0TO4", "5TO14", "15TO64", "65TO"):
            continue
        k = (int(r["ISO_YEAR"]), int(r["ISO_WEEK"]))
        a = acc.setdefault(k, [0.0, 0.0, 0.0, 0.0])
        try:
            if r["ARI_POP_COV"] and float(r["ARI_POP_COV"]) > 0:
                a[0] += float(r["ARI_CASE"] or 0); a[1] += float(r["ARI_POP_COV"])
            if r["ILI_POP_COV"] and float(r["ILI_POP_COV"]) > 0:
                a[2] += float(r["ILI_CASE"] or 0); a[3] += float(r["ILI_POP_COV"])
        except ValueError:
            pass
    return {k: (a[0] / a[1] * 1e5, a[2] / a[3] * 1e5 if a[3] else None) for k, a in acc.items() if a[1] >= 1e5}


def main(szu_path, who_path):
    data = json.loads(DATA.read_text(encoding="utf-8"))
    old = {(e["year"], e["iso_week"]): e for e in data["history"]}
    szu = {(r["year"], r["week"]): r for r in json.load(open(szu_path)) if r.get("ari") and r.get("year")}
    who = who_rates(who_path)
    first_szu = min(F.week_index(*k) for k in szu)

    hist = {}
    for k, (a, i) in who.items():
        if F.week_index(*k) < first_szu:
            hist[k] = S.make_entry(*k, a * ARI_RATIO, i * ILI_RATIO if i else None, "who_scaled")
    for k, r in szu.items():
        hist[k] = S.make_entry(*k, r["ari"], r.get("ili"), "szu", r["url"], groups=r.get("ari_groups"))
    # oficiální hodnoty z předchozího souboru (i ty, které mezitím přidal
    # fetch_ari_data.py) se nikdy nemažou: vyhrávají nad WHO, a nad zpětným
    # stažením, nesou-li opravu (poznámku o revizi)
    for k, e in old.items():
        if S.is_official(e) and (not S.is_official(hist.get(k)) or e.get("note")):
            hist[k] = dict(e)
    # týdny bez SZÚ reportu po 2023-W40 → WHO × poměr (např. 2024-W45, 2025-W52)
    for k, (a, i) in who.items():
        if k not in hist and F.week_index(*k) >= first_szu:
            hist[k] = S.make_entry(*k, a * ARI_RATIO, i * ILI_RATIO if i else None, "who_scaled",
                                   note="SZÚ report chybí – WHO FluID × 1,20")
    S.fill_single_gaps(hist)
    S.finalize(data, hist)
    data["meta"] = {
        "last_updated": date.today().isoformat(),
        "source": "Státní zdravotní ústav (SZÚ) – týdenní hlášení ARI/ILI: https://szu.gov.cz/publikace-szu/data/akutni-respiracni-infekce-chripka/",
        "history_note": "2009–2023: WHO FluID (ARI_CASE/ARI_POP_COV, CZE) × 1,20 na měřítko SZÚ; od 2023-W40 oficiální týdenní PDF SZÚ. Viz METODIKA.md.",
        "bands": {"green": "< 750", "yellow": "750–999", "amber": "1000–1499", "red": "≥ 1500"},
    }
    DATA.write_text(S.dump_data(data), encoding="utf-8")
    from collections import Counter
    print("history:", len(data["history"]), dict(Counter(e["source"] for e in data["history"])))
    print("current:", data["current"])


if __name__ == "__main__":
    main(*sys.argv[1:3])
