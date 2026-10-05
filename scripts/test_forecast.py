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
check("změna Celkem proti předchozímu týdnu", r and r["ari_change_pct"] == -0.81, r and r.get("ari_change_pct"))
row = "Česká republika - the Czech Republic 2194 1099 672 496 330 738"
check("poznámka uvnitř řádku (496*) → odmítnuto", S.parse_szu_pdf_text(SAMPLE.replace("496 330", "496* 330")) is None)
check("chybějící buňka → odmítnuto", S.parse_szu_pdf_text(SAMPLE.replace("672 496", "– 496")) is None)
check("oddělovač tisíců (1 078) → odmítnuto", S.parse_szu_pdf_text(SAMPLE.replace(" 738\n", " 1 078\n", 1)) is None)
check("Celkem mimo rozsah skupin → odmítnuto", S.parse_szu_pdf_text(SAMPLE.replace(" 330 738", " 330 7380")) is None)
check("neplatný týden 53/2027 → odmítnuto", S.parse_szu_pdf_text(SAMPLE.replace("25 týden 2026", "53 týden 2027")) is None)
check("jen řádek ILI → odmítnuto", S.parse_szu_pdf_text(SAMPLE.split("ARI\n0 - 5")[0] + SAMPLE.split("ILI\n")[1]) is None)
check("číslo na dalším řádku se nepřilepí", S.parse_szu_pdf_text(SAMPLE.replace(" 330 738\n", " 330 738\n12\n")) is not None)
# revize předchozího týdne podle „Změna [%]“ (skutečný případ 2025-W38: 851 → 890)
REV = SAMPLE.replace("2194 1099 672 496 330 738", "2211 1311 1321 665 402 955").replace(
    "-2,71 -3,26 4,35 0,61 -2,08 -0,81", "0,14 -9,83 20,2 14,85 14,86 7,3")
rr = S.parse_szu_pdf_text(REV)
pe = S.make_entry(2026, 24, 851, 7, "szu", groups=[2079, 1468, 1013, 554, 324])
check("revize předchozího týdne", rr and S.revise_previous(pe, rr, (2026, 25)) and pe["ari_per_100k"] == 890
      and pe["ari_age"] == [2208, 1454, 1099, 579, 350], (pe["ari_per_100k"], pe.get("ari_age")))
pe2 = S.make_entry(2026, 24, 744, 7, "szu")
check("bez revize, když sedí", not S.revise_previous(pe2, r, (2026, 25)) and pe2["ari_per_100k"] == 744)
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
check(f"{M.H} týdnů předpovědi", len(f) == M.H)
check("týdny navazují", all(F.week_add(*((f[i]["year"], f[i]["iso_week"])), 1) == (f[i + 1]["year"], f[i + 1]["iso_week"]) for i in range(7)))
check("kvantily uspořádané", all(x["q10"] <= x["q25"] <= x["median"] <= x["q75"] <= x["q90"] for x in f))
check("pravděpodobnosti pásem monotónní", all(x["p_750"] >= x["p_1000"] >= x["p_1500"] for x in f))
check("pondělí odpovídá týdnu", all(F.monday(x["year"], x["iso_week"]).isoformat() == x["monday"] for x in f))
# žádný pohled do budoucnosti: přidání budoucích dat nesmí změnit předpověď z daného origin
origin = (2026, 20)
past = F.history_upto(ser, origin)
full = dict(ser)
# předání celé řady (i s daty po origin) musí dát stejnou předpověď jako oříznutá řada
check("model nevidí budoucnost", M.point_forecast(full, origin) == M.point_forecast(past, origin))
# přelom roku s W53 (2026 má 53 týdnů)
ser53 = {}
for y in range(2010, 2027):
    for w in range(1, F.weeks_in_isoyear(y) + 1):
        if (y, w) <= (2026, 48):
            ser53[(y, w)] = 900 + 600 * math.cos((w - 5) / 52 * 2 * math.pi)
p53 = M.point_forecast(ser53, (2026, 48))
check("předpověď přes W53/2026 je konečná", len(p53) == M.H and all(math.isfinite(x) for x in p53))

print("Svátkové týdny (podle data)")
import model_profile_holiday as PH
exp = {(2026, 40): "minor", (2026, 44): "autumn", (2026, 47): "minor", (2026, 52): "xmas1",
       (2026, 53): "xmas2", (2027, 1): None, (2026, 41): None, (2025, 52): "xmas1", (2026, 1): "xmas2",
       # 25. 12. v neděli (2022) / v sobotu (2027): hlavní propad až týden po Vánocích
       (2022, 51): None, (2022, 52): "xmas1", (2023, 1): None,
       (2027, 51): "minor", (2027, 52): "xmas1", (2028, 1): None,
       # 28. 10. o víkendu (2018, 2023): propad v datech není → bez svátku
       (2018, 43): None, (2018, 44): None, (2023, 43): None,
       # svátky o víkendu se nepočítají
       (2025, 39): None, (2027, 17): None, (2027, 18): None, (2026, 27): None}
got = {k: PH.htype(*k) for k in exp}
check("typy svátků 2025–2027", got == exp, got)

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
vals = [e["ari_per_100k"] for e in h if e.get("ari_per_100k") is not None]
check("hodnoty ARI v rozumném rozsahu (50–5 000)", all(50 <= v <= 5000 for v in vals), (min(vals), max(vals)))
jumps = [(b["week"], round(b["ari_per_100k"] / a["ari_per_100k"], 2)) for a, b in zip(h, h[1:])
         if b["source"] == "szu" and a.get("ari_per_100k") and F.week_add(a["year"], a["iso_week"], 1) == (b["year"], b["iso_week"])
         and not S.PLAUSIBLE_RATIO[0] <= b["ari_per_100k"] / a["ari_per_100k"] <= S.PLAUSIBLE_RATIO[1]]
check("žádné nepravděpodobné skoky mezi týdny SZÚ", not jumps, jumps)

print("Předpověď na skutečných datech (podzim 2026, zmrazený snímek řady do 2026-W39)")
# zmrazená řada → testy nezávisí na nových datech ani na zpětných opravách SZÚ
# (jinak by revize W39 v reportu za W40 mohla zablokovat všechny další aktualizace)
snap = json.loads((HERE / "fixtures" / "ari_series_2026w39.json").read_text(encoding="utf-8"))
real = {(int(k[:4]), int(k[6:])): v for k, v in snap.items()}
if (2026, 39) in real:
    v39 = real[(2026, 39)]
    f38 = M.make_forecast(F.history_upto(real, (2026, 38)))["forecast"]
    check("z W38: skutečná W39 v 80% intervalu", f38[0]["q10"] <= v39 <= f38[0]["q90"],
          f"{f38[0]['q10']}–{f38[0]['q90']} vs {v39}")
    f39 = M.make_forecast(F.history_upto(real, (2026, 39)))["forecast"]
    med = {x["iso_week"]: x["median"] for x in f39}
    # podzimní nárůst: W41–W43 nikdy pod úrovní W39 (mimo svátkové týdny ve 12 sezónách)
    check("z W39: W41–W43 nad hodnotou W39", all(med[w] > v39 for w in (41, 42, 43)), med)
    check("svátkové propady: W40 < W41, W44 < W43 i W45", med[40] < med[41] and med[44] < med[43] and med[44] < med[45], med)
    # od automatizovaného hlášení (2024/25) je propad o podzimních prázdninách −25 až −30 %
    check("propad W44 aspoň 15 % pod W43", med[44] / med[43] < 0.85, round(med[44] / med[43], 3))
    # obě složky modelu musí svátek promítnout (analogy na řadě očištěné o svátky)
    import model_analog as MA, model_profile_holiday as MP
    h39 = F.history_upto(real, (2026, 39))
    rat = lambda lp: math.exp(lp[4] - lp[3])          # W44 / W43 z origin W39
    ra, rp = rat(MA.predict(h39, (2026, 39), 6)), rat(MP.predict(h39, (2026, 39), 6))
    d_aut = math.exp(MP.holiday_effects(MP.log_series(h39))["autumn"])
    check("W44/W43 u obou složek < 0,82", ra < 0.82 and rp < 0.82, (round(ra, 3), round(rp, 3)))
    check("W44/W43 blízko svátkového efektu", abs(med[44] / med[43] - d_aut) < 0.06, (round(med[44] / med[43], 3), round(d_aut, 3)))
    hol = {x["iso_week"]: x.get("holiday") for x in f39}
    check("svátky v předpovědi popsané", bool(hol[40]) and bool(hol[44]) and bool(hol[47]) and not hol[41], hol)

print()
if FAILS:
    print(f"SELHALO {len(FAILS)} testů: {FAILS}")
    sys.exit(1)
print("Vše v pořádku.")
