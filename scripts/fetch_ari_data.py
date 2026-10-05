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
RE_CR_ROW = re.compile(r"[ČC]esk[áa]\s+republika\s*-\s*the\s+Czech\s+Republic\s+([\d][\d\s,\.]*)")

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
    return [float(x.replace(",", ".")) for x in s.split() if re.fullmatch(r"[\d.,]+", x)]

def parse_szu_pdf_text(text):
    """→ {year, week, ari, ili, ari_groups, ili_groups} nebo None."""
    hdr = RE_HDR.search(text)
    rows = RE_CR_ROW.findall(text)
    if not hdr or not rows:
        return None
    ari = _nums(rows[0])
    ili = _nums(rows[1]) if len(rows) > 1 else []
    if not ari:
        return None
    return {"year": int(hdr.group(2)), "week": int(hdr.group(1)),
            "ari": ari[-1], "ili": ili[-1] if ili else None,
            "ari_groups": ari[:-1], "ili_groups": ili[:-1]}

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

def fill_single_gaps(hist):
    """Jednotlivý chybějící týden mezi dvěma známými → geometrický průměr
    (označeno source="interpolated"; delší mezery se nevyplňují)."""
    idx = {F.week_index(*k): k for k in hist}
    for i in range(min(idx), max(idx) + 1):
        if i in idx or (i - 1) not in idx or (i + 1) not in idx:
            continue
        k = F.week_from_index(i)
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
    # trend: průměrný týdenní log-růst za 2 týdny (tlumí jednorázové výkyvy)
    g = math.log(v / prev2) / 2 if prev2 else (math.log(v / prev) if prev else 0.0)
    trend = "rostoucí" if g > math.log(1.05) else "klesající" if g < -math.log(1.05) else "stabilní"
    return {"week": last["week"], "monday": last["monday"], "ari_per_100k": v,
            "ili_per_100k": last.get("ili_per_100k"), "band": band_of(v),
            "change_1w_pct": round(ch1 * 100, 1) if ch1 is not None else None,
            "change_2w_pct": round(ch2 * 100, 1) if ch2 is not None else None,
            "trend": trend, "source": last.get("source"),
            "source_url": last.get("source_url")}


# ── Hlavní běh ───────────────────────────────────────────────────────────────
def main():
    print("ARI Dashboard – aktualizace dat")
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    before = json.dumps(data, ensure_ascii=False, sort_keys=True)
    hist = {(e["year"], e["iso_week"]): e for e in data["history"]}

    urls = list_szu_pdfs()
    print(f"SZÚ datová stránka: {len(urls)} týdenních PDF")
    new = 0
    for url in urls:
        k = guess_key(url)
        if k and is_official(hist.get(k)):
            continue                      # už máme oficiální hodnotu
        rec = fetch_pdf_record(url)
        if not rec:
            continue
        key = (rec["year"], rec["week"])
        if is_official(hist.get(key)):
            continue
        hist[key] = make_entry(*key, rec["ari"], rec["ili"], "szu", url,
                               groups=rec.get("ari_groups"))
        new += 1
        print(f"  + {key[0]}-W{key[1]:02d}: ARI {rec['ari']:.0f}, ILI {rec['ili']}")
    fill_single_gaps(hist)
    data["history"] = sorted_history(hist)
    data["current"] = compute_current(hist)

    series = {(e["year"], e["iso_week"]): e["ari_per_100k"] for e in data["history"] if e.get("ari_per_100k")}
    fc = M.make_forecast(series)
    data["forecast"] = fc["forecast"]
    data["model"] = fc["model"]

    after = json.dumps(data, ensure_ascii=False, sort_keys=True)
    if after == before:
        print("Beze změny – soubor se nepřepisuje.")
        return 0
    data["meta"]["last_updated"] = date.today().isoformat()
    DATA_FILE.write_text(dump_data(data), encoding="utf-8")
    print(f"Uloženo ({new} nových týdnů). Aktuální: {data['current']['week']} = {data['current']['ari_per_100k']}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
