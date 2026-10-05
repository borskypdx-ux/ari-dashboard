#!/usr/bin/env python3
"""ARI Dashboard – aktualizace dat ze SZÚ a výpočet předpovědi.

Zdroj: týdenní PDF reporty SZÚ na datové stránce
  https://szu.gov.cz/publikace-szu/data/akutni-respiracni-infekce-chripka/
Každé PDF obsahuje tabulku „Relativní nemocnost na 100000 obyvatel" a národní
řádek „Česká republika – the Czech Republic" (věkové skupiny + Celkem) pro ARI
i ILI. Datová stránka drží zhruba poslední rok reportů, takže každý běh doplní
i týdny, které dřív chyběly.

Starší historie (2009–2023) pochází z WHO FluID (přepočteno na měřítko SZÚ,
viz METODIKA.md) a v datech je označena source = "who_scaled".

Běh: python scripts/fetch_ari_data.py   (data/ari_data.json se přepíše jen
při skutečné změně, aby denní spouštění nevytvářelo prázdné commity)
"""
import io, json, math, re, sys
from datetime import date
from pathlib import Path
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
import forecast as F      # ISO týdny
import ari_model as M     # produkční předpověď (model + intervaly)
import model_profile_holiday as PH   # svátkové týdny (podle data)

try:
    import pdfplumber
    HAS_PDF = True
except ImportError:
    HAS_PDF = False

DATA_FILE = Path(__file__).parent.parent / "data" / "ari_data.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ARI-Dashboard/4.0)"}
SZU_DATA_PAGE = "https://szu.gov.cz/publikace-szu/data/akutni-respiracni-infekce-chripka/"

RE_PDF_URL = re.compile(r"/wp-content/uploads/(\d{4})/(\d{2})/(\d{1,2})_tyden\.pdf$", re.I)
RE_HDR = re.compile(r"(\d{1,2})\s*\.?\s*t[ýy]den\s+(\d{4})", re.I)
# Národní řádek tabulky: věkové skupiny … a poslední číslo = Celkem (na 100 000).
# První výskyt je ARI, druhý ILI.
RE_CR_ROW = re.compile(r"[ČC]esk[áa]\s+republika\s*-\s*the\s+Czech\s+Republic[ \t]+([\d][\d \t,\.]*)")
# Řádek změn proti předchozímu týdnu (v %, desetinná čárka, může být záporné)
RE_CHANGE_ROW = re.compile(r"Zm[ěe]na\s*-\s*Change\s*\[%\][ \t]+([-−\d \t,\.]+)")
N_COLS = 6                 # 0–5, 6–14, 15–24, 25–64, 65+, Celkem
PLAUSIBLE_RATIO = (0.4, 2.5)   # přípustný poměr k předchozímu týdnu (Vánoce ~0,45)
REVISION_TOL = 0.02        # oprava předchozího týdne, liší-li se o víc než 2 %
STALE_WEEKS = 4            # data starší o tolik týdnů → běh skončí chybou (upozornění)

BANDS = [(1500, "red"), (1000, "amber"), (750, "yellow"), (0, "green")]


# ── Stahování a parsování ────────────────────────────────────────────────────
def get(url, binary=False):
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        return r.content if binary else r.text
    except Exception as e:
        print(f"  CHYBA {url}: {e}")
        return None

def _nums(s):
    out = []
    for x in s.split():
        x = x.replace("−", "-").replace(",", ".")
        if re.fullmatch(r"-?\d+(\.\d+)?", x):
            out.append(float(x))
    return out

def _row_ok(v):
    """Národní řádek: přesně 6 čísel a Celkem (vážený průměr skupin) mezi min a max skupin."""
    return len(v) == N_COLS and min(v[:-1]) - 1 <= v[-1] <= max(v[:-1]) + 1

def parse_szu_pdf_text(text):
    """→ {year, week, ari, ili, ari_groups, ili_groups, ari_change_pct} nebo None.

    Hodnoty se ověřují (6 sloupců, Celkem v rozsahu věkových skupin, ARI ≥ ILI,
    platný ISO týden) – při jakékoli odchylce formátu se raději nevrátí nic,
    než aby se uložilo špatné číslo."""
    hdr = RE_HDR.search(text)
    rows = list(RE_CR_ROW.finditer(text))
    if not hdr or len(rows) < 2:          # národní řádek ARI i ILI (jinak by se ILI vzalo jako ARI)
        return None
    year, week = int(hdr.group(2)), int(hdr.group(1))
    if not 1 <= week <= F.weeks_in_isoyear(year):
        return None
    ari = _nums(rows[0].group(1))
    ili = _nums(rows[1].group(1))
    # ILI je podmnožina ARI a bývá 15–100× nižší
    if not _row_ok(ari) or not _row_ok(ili) or ari[-1] < 3 * ili[-1]:
        return None
    chg = RE_CHANGE_ROW.search(text, rows[0].end())
    chg_v = _nums(chg.group(1)) if chg and chg.start() < rows[1].start() else []
    return {"year": year, "week": week,
            "ari": ari[-1], "ili": ili[-1],
            "ari_groups": ari[:-1], "ili_groups": ili[:-1],
            "ari_change_pct": chg_v[-1] if len(chg_v) == N_COLS else None,
            "ari_groups_change_pct": chg_v[:-1] if len(chg_v) == N_COLS else None}


def revise_previous(pe, rec, key):
    """SZÚ občas opraví předchozí týden (pozdní hlášení) – oprava je vidět jen
    v řádku „Změna [%]“ nového reportu. Liší-li se z něj odvozená hodnota od
    uložené o víc než REVISION_TOL, uloženou opraví (i věkové skupiny). → bool"""
    if not is_official(pe) or rec.get("ari_change_pct") is None:
        return False
    implied = rec["ari"] / (1 + rec["ari_change_pct"] / 100)
    old_v = pe["ari_per_100k"]
    if abs(implied / old_v - 1) <= REVISION_TOL:
        return False
    pe["ari_per_100k"] = round(implied)
    gch = rec.get("ari_groups_change_pct")
    if gch and len(gch) == len(rec["ari_groups"]):
        pe["ari_age"] = [round(g / (1 + c / 100)) for g, c in zip(rec["ari_groups"], gch)]
    pe["note"] = f"revidováno podle reportu za {key[0]}-W{key[1]:02d} (původně {old_v})"
    print(f"  ~ {pe['week']}: revize {old_v} → {pe['ari_per_100k']} (podle reportu {key[0]}-W{key[1]:02d})")
    return True

def guess_key(url):
    """(rok, týden) odhadnutý z URL – rok v cestě je rok NAHRÁNÍ, takže
    W52/W53 nahraný v lednu patří do předchozího roku."""
    m = RE_PDF_URL.search(url)
    if not m:
        return None
    y, mon, w = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if mon == 1 and w >= 50:
        y -= 1
    elif mon == 12 and w <= 2:
        y += 1
    return (y, w)

def list_szu_pdfs():
    html = get(SZU_DATA_PAGE)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for a in soup.find_all("a", href=True):
        href = a["href"].split("#")[0]
        if not href.startswith("http"):
            href = "https://szu.gov.cz" + href
        if RE_PDF_URL.search(href) and href not in urls:
            urls.append(href)
    return urls

def fetch_pdf_record(url):
    if not HAS_PDF:
        return None
    c = get(url, binary=True)
    if not c:
        return None
    try:
        with pdfplumber.open(io.BytesIO(c)) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    except Exception as e:
        print(f"  PDF chyba {url}: {e}")
        return None
    rec = parse_szu_pdf_text(text)
    if rec:
        rec["url"] = url
    else:
        print(f"  [?] nerozpoznaný formát: {url}")
    return rec


# ── Datový model ─────────────────────────────────────────────────────────────
def band_of(v):
    return next(name for lim, name in BANDS if v >= lim)

def make_entry(y, w, ari, ili=None, source="szu", url=None, note=None, groups=None):
    e = {"week": f"{y}-W{w:02d}",
         "year": y, "iso_week": w, "monday": F.monday(y, w).isoformat(),
         "ari_per_100k": round(ari), "ili_per_100k": round(ili, 1) if ili is not None else None,
         "source": source}
    if url:
        e["source_url"] = url
    if note:
        e["note"] = note
    if groups:
        e["ari_age"] = groups
    return e

def dump_data(data):
    """JSON pro git: horní úroveň odsazená, každý týden historie na jednom řádku."""
    def one(v):
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    parts = []
    for k, v in data.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            body = ",\n  ".join(one(x) for x in v)
            parts.append(f' "{k}": [\n  {body}\n ]')
        else:
            parts.append(f' "{k}": {json.dumps(v, ensure_ascii=False, indent=1)}')
    return "{\n" + ",\n".join(parts) + "\n}\n"

def is_official(e):
    return e and e.get("source") == "szu" and e.get("ari_per_100k") is not None

def sorted_history(hist):
    return [hist[k] for k in sorted(hist, key=lambda k: F.week_index(*k))]

def fill_single_gaps(hist, skip=()):
    """Jednotlivý chybějící týden mezi dvěma známými → geometrický průměr
    (označeno source="interpolated"; delší mezery se nevyplňují). Svátkové týdny
    se nedopočítávají (průměr sousedů by propad přecenil o 40–60 %), stejně jako
    týdny, jejichž PDF existuje, ale nepodařilo se ho načíst (skip)."""
    idx = {F.week_index(*k): k for k in hist}
    for i in range(min(idx), max(idx) + 1):
        if i in idx or (i - 1) not in idx or (i + 1) not in idx:
            continue
        k = F.week_from_index(i)
        if PH.htype(*k) or k in skip:
            continue
        a, b = hist[idx[i - 1]]["ari_per_100k"], hist[idx[i + 1]]["ari_per_100k"]
        hist[k] = make_entry(*k, math.sqrt(a * b), source="interpolated",
                             note="dopočteno – SZÚ za tento týden report nezveřejnil")

def compute_current(hist):
    rows = [e for e in sorted_history(hist) if e.get("ari_per_100k")]
    last = rows[-1]
    v = last["ari_per_100k"]
    prev = rows[-2]["ari_per_100k"] if len(rows) > 1 else None
    prev2 = rows[-3]["ari_per_100k"] if len(rows) > 2 else None
    ch1 = (v / prev - 1) if prev else None
    ch2 = (v / prev2 - 1) if prev2 else None
    # trend: stejné pravidlo jako na dashboardu (trendClass) – týdenní log-růst
    # z dvoutýdenních součtů bez svátkových týdnů; „klesající" jen při poklesu
    # potvrzeném 2 týdny po sobě
    trend = "stabilní"
    clean = [r["ari_per_100k"] for r in rows[-8:] if not PH.htype(r["year"], r["iso_week"])]
    if len(clean) >= 4:
        x3, x2, x1, x0 = clean[-4:]
        g = 0.5 * math.log((x0 + x1) / (x2 + x3))
        down_ok = math.log(x0 / x1) <= 0.02 and math.log(x1 / x2) <= 0.02
        trend = "rostoucí" if g >= 0.03 else "klesající" if g <= -0.03 and down_ok else "stabilní"
    cur = {"week": last["week"], "monday": last["monday"], "ari_per_100k": v,
           "ili_per_100k": last.get("ili_per_100k"), "band": band_of(v),
           "change_1w_pct": round(ch1 * 100, 1) if ch1 is not None else None,
           "change_2w_pct": round(ch2 * 100, 1) if ch2 is not None else None,
           "trend": trend, "source": last.get("source"),
           "source_url": last.get("source_url")}
    hol = PH.holiday(last["year"], last["iso_week"])[1]
    if hol:
        cur["holiday"] = hol
    return cur


def finalize(data, hist):
    """Historie (se svátkovými popisky), aktuální stav a předpověď."""
    for k, e in hist.items():
        hol = PH.holiday(*k)[1]
        if hol:
            e["holiday"] = hol
        else:
            e.pop("holiday", None)
    data["history"] = sorted_history(hist)
    data["current"] = compute_current(hist)
    series = {(e["year"], e["iso_week"]): e["ari_per_100k"] for e in data["history"] if e.get("ari_per_100k")}
    fc = M.make_forecast(series)
    data["forecast"] = fc["forecast"]
    data["model"] = fc["model"]
    # předpovědi z předchozích 8 týdnů – dashboard podle nich dopočítá tehdejší
    # doporučení, aby akutní kapacitu nesnižoval bez potvrzeného poklesu
    keys = sorted(series, key=lambda k: F.week_index(*k))
    data.pop("forecast_prev", None)
    data["forecast_past"] = [
        {"origin": f"{o[0]}-W{o[1]:02d}", "forecast": M.make_forecast(F.history_upto(series, o))["forecast"]}
        for o in keys[-9:-1]]


def weeks_behind(last_key, today=None):
    t = (today or date.today()).isocalendar()
    return F.week_index(t[0], t[1]) - F.week_index(*last_key)


# ── Hlavní běh ───────────────────────────────────────────────────────────────
def main():
    print("ARI Dashboard – aktualizace dat")
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    before = json.dumps(data, ensure_ascii=False, sort_keys=True)
    hist = {(e["year"], e["iso_week"]): e for e in data["history"]}
    problems = []

    if not HAS_PDF:
        problems.append("knihovna pdfplumber není k dispozici")
    urls = list_szu_pdfs()
    print(f"SZÚ datová stránka: {len(urls)} týdenních PDF")
    if not urls:
        problems.append("datová stránka SZÚ nevrátila žádné PDF (nedostupná nebo změněná)")
    new, failed = 0, set()
    for url in urls if HAS_PDF else []:
        k = guess_key(url)
        if k and is_official(hist.get(k)):
            continue                      # už máme oficiální hodnotu
        try:
            rec = fetch_pdf_record(url)
            if not rec:
                raise ValueError("PDF se nepodařilo načíst nebo má nečekaný formát")
            key = (rec["year"], rec["week"])
            if k and abs(F.week_index(*key) - F.week_index(*k)) > 1:
                raise ValueError(f"týden v hlavičce {key} neodpovídá adrese {k}")
            if is_official(hist.get(key)):
                continue
            pk = F.week_add(*key, -1)
            pe = hist.get(pk)
            if pe and pe.get("ari_per_100k"):
                ratio = rec["ari"] / pe["ari_per_100k"]
                if not PLAUSIBLE_RATIO[0] <= ratio <= PLAUSIBLE_RATIO[1]:
                    raise ValueError(f"nepravděpodobná změna proti předchozímu týdnu (×{ratio:.2f})")
            hist[key] = make_entry(*key, rec["ari"], rec["ili"], "szu", url, groups=rec.get("ari_groups"))
            new += 1
            print(f"  + {key[0]}-W{key[1]:02d}: ARI {rec['ari']:.0f}, ILI {rec['ili']}")
            revise_previous(pe, rec, key)
        except Exception as e:
            if k:
                failed.add(k)
            problems.append(f"{url}: {e}")
            print(f"  ! {url}: {e}")
    fill_single_gaps(hist, skip=failed)
    finalize(data, hist)

    last = max(hist, key=lambda k: F.week_index(*k))
    behind = weeks_behind(last)
    if behind >= STALE_WEEKS:
        problems.append(f"poslední data {last[0]}-W{last[1]:02d} jsou {behind} týdnů stará")

    after = json.dumps(data, ensure_ascii=False, sort_keys=True)
    if after == before:
        print("Beze změny – soubor se nepřepisuje.")
    else:
        data["meta"]["last_updated"] = date.today().isoformat()
        DATA_FILE.write_text(dump_data(data), encoding="utf-8")
        print(f"Uloženo ({new} nových týdnů). Aktuální: {data['current']['week']} = {data['current']['ari_per_100k']}")
    if problems:
        print("\nPROBLÉMY (běh skončí chybou, aby přišlo upozornění):")
        for x in problems:
            print("  - " + x)
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())
