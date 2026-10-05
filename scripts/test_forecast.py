#!/usr/bin/env python3
"""Rychlé testy datové a předpovědní logiky (běží v CI před každou aktualizací).

python3 scripts/test_forecast.py   → exit 0 = vše v pořádku
"""
import json, math, sys, types
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
# PDF se v testech neparsuje binárně (jen text) → pdfplumber není potřeba.
sys.modules.setdefault("pdfplumber", types.ModuleType("pdfplumber"))
import forecast as F
import ari_model as M
import fetch_ari_data as S

FAILS = []
def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)

print("ISO týdny")
check("2026 má 53 týdnů", F.weeks_in_isoyear(2026) == 53)
check("W52/2026 + 1 = W53/2026", F.week_add(2026, 52, 1) == (2026, 53))
check("W53/2026 + 1 = W01/2027", F.week_add(2026, 53, 1) == (2027, 1))
check("W01/2027 − 1 = W53/2026", F.week_add(2027, 1, -1) == (2026, 53))
check("index ↔ týden (vč. W53)", all(F.week_from_index(F.week_index(*k)) == k for k in [(2026, 53), (2027, 1), (2009, 32), (2020, 53), (2026, 27)]))

print("Parsování PDF SZÚ (text)")
SAMPLE = """25 týden 2026 - Week 25
Relativní nemocnost na 100000 obyvatel - Morbidity per 100000
ARI
0 - 5 let/ 6 - 14 15 - 24 25 - 64 65+ let/ Celkem /
Česká republika - the Czech Republic 2194 1099 672 496 330 738
Změna - Change [%] -2,71 -3,26 4,35 0,61 -2,08 -0,81
ILI
Česká republika - the Czech Republic 15 9 7 6 5 7
"""
r = S.parse_szu_pdf_text(SAMPLE)
check("rok/týden z hlavičky", r and (r["year"], r["week"]) == (2026, 25), r)
check("ARI Celkem = 738", r and r["ari"] == 738)
check("ILI Celkem = 7", r and r["ili"] == 7)
check("věkové skupiny ARI", r and r["ari_groups"] == [2194, 1099, 672, 496, 330])
check("neznámý formát → None", S.parse_szu_pdf_text("bez tabulky") is None)
u = "https://szu.gov.cz/wp-content/uploads/{}/{}/{}_tyden.pdf"
check("W52 nahraný v lednu → předchozí rok", S.guess_key(u.format(2026, "01", 52)) == (2025, 52))
check("W01 nahraný v prosinci → další rok", S.guess_key(u.format(2025, "12", "01")) == (2026, 1))
check("běžný týden", S.guess_key(u.format(2026, "09", 39)) == (2026, 39))

print("Předpověď")
# syntetická řada: sezónní křivka (zima vysoko, léto nízko) 2010–2026 + šum
ser = {}
for y in range(2010, 2027):
    for w in range(1, F.weeks_in_isoyear(y) + 1):
        if (y, w) > (2026, 39):
            break
        base = 900 + 600 * math.cos((w - 5) / 52 * 2 * math.pi) * (1.1 if y % 2 else 0.9)
        ser[(y, w)] = base * (1 + 0.05 * math.sin(y * 7 + w))
fc = M.make_forecast(ser)
f = fc["forecast"]
check("8 týdnů předpovědi", len(f) == 8)
check("týdny navazují", all(F.week_add(*((f[i]["year"], f[i]["iso_week"])), 1) == (f[i + 1]["year"], f[i + 1]["iso_week"]) for i in range(7)))
check("kvantily uspořádané", all(x["q10"] <= x["q25"] <= x["median"] <= x["q75"] <= x["q90"] for x in f))
check("pravděpodobnosti pásem monotónní", all(x["p_750"] >= x["p_1000"] >= x["p_1500"] for x in f))
check("pondělí odpovídá týdnu", all(F.monday(x["year"], x["iso_week"]).isoformat() == x["monday"] for x in f))
# žádný pohled do budoucnosti: přidání budoucích dat nesmí změnit předpověď z daného origin
origin = (2026, 20)
past = F.history_upto(ser, origin)
full = dict(ser)
check("model nevidí budoucnost", M.point_forecast(past, origin) == M.point_forecast(F.history_upto(full, origin), origin))

print("Datový soubor")
data = json.loads((HERE.parent / "data" / "ari_data.json").read_text(encoding="utf-8"))
h = data["history"]
keys = [(e["year"], e["iso_week"]) for e in h]
check("historie seřazená", keys == sorted(keys, key=lambda k: F.week_index(*k)))
check("bez duplicit", len(keys) == len(set(keys)))
check("platné zdroje", all(e["source"] in ("szu", "who_scaled", "interpolated") for e in h))
check("current = poslední týden", data["current"]["week"] == h[-1]["week"])
check("předpověď navazuje na poslední týden", data["forecast"][0]["week"] == "%d-W%02d" % F.week_add(h[-1]["year"], h[-1]["iso_week"], 1))
check("pondělí v historii", all(F.monday(e["year"], e["iso_week"]).isoformat() == e["monday"] for e in h[-60:]))

print()
if FAILS:
    print(f"SELHALO {len(FAILS)} testů: {FAILS}")
    sys.exit(1)
print("Vše v pořádku.")
