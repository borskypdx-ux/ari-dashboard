#!/usr/bin/env python3
"""
ARI Dashboard – automatický update dat ze SZÚ
Zdroje (kaskádově – bere nejlepší dostupný):
  1. SZÚ týdenní PDF:        https://szu.gov.cz/wp-content/uploads/YYYY/MM/WW_tyden.pdf
  2. SZÚ tiskové zprávy:     https://szu.gov.cz/aktuality/ (regex)
  3. KHS Středočeský kraj:   https://khsstc.cz/ (škálováno na národní)
  4. KHS Praha:              https://www.hygpraha.cz/ (škálováno na národní)
"""
import json, re, sys, time, io
from datetime import date, timedelta
from pathlib import Path
import requests
from bs4 import BeautifulSoup

try:
    import pdfplumber
    HAS_PDF = True
except ImportError:
    HAS_PDF = False

DATA_FILE = Path(__file__).parent.parent / "data" / "ari_data.json"
HEADERS   = {"User-Agent": "Mozilla/5.0 (compatible; ARI-Dashboard/3.0)"}

# Přepočetní koeficienty krajů → národní odhad
# Kalibrováno na W11/2026: national=1276, StC=1088 → 1.173; Praha≈0.73×national
KHS_SCALE = {
    "stc":   1.17,   # Středočeský kraj → národní
    "praha": 1.36,   # Praha → národní (Praha je typicky nižší než národní)
}

RE_WEEK = re.compile(r"(?:V|ve?)\s+(\d+)\.\s*(?:kalendářním\s+)?t[ýy]dnu\s+(?:roku\s+)?(\d{4})", re.IGNORECASE)
RE_ARI_NATIONAL = re.compile(
    r"(?:ARI|akutn[íi]ch?\s+respira[čc]n[íi]ch?\s+infekc[íi])"
    r".*?[úu]rovn[ěe]\s*([\d\s\u00a0]+(?:[,\.]\d+)?)\s*na\s*100[\s\u00a0]000",
    re.DOTALL | re.IGNORECASE
)
RE_ARI_REGIONAL = re.compile(
    r"[Cc]elkov[aá]\s+nemocnost\s+(?:ARI\s+)?(?:čin[íi]la|dosáhla|byla)\s+([\d\s\u00a0]+(?:[,\.]\d+)?)\s*onemocn[ěe]n[íi]\s+na\s+100[\s\u00a0]000",
    re.IGNORECASE
)
RE_ILI = re.compile(r"ILI.*?([\d]+)\s*(?:případů|onemocnění)?\s+na\s+100[\s\u00a0]000", re.IGNORECASE)


def load_data():
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Ulozeno: {DATA_FILE}")

def iso_key(year, week):
    return f"{year}-W{week:02d}"

def get(url, binary=False):
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        r.raise_for_status()
        return r.content if binary else r.text
    except Exception as e:
        print(f"  CHYBA {url}: {e}")
        return None

def parse_num(s):
    return float(s.replace(" ","").replace("\u00a0","").replace(",","."))

def make_entry(year, week, ari, ili=None, source="", note=""):
    return {"week": iso_key(year,week), "year": year, "iso_week": week,
            "ari_per_100k": round(ari), "ili_per_100k": round(ili) if ili else None,
            "source_url": source, "note": note}


# ── ZDROJ 1: SZÚ PDF ──────────────────────────────────────────────────────────
def fetch_pdf(year, week):
    if not HAS_PDF: return None
    start = date.fromisocalendar(year, week, 1)
    urls = [f"https://szu.gov.cz/wp-content/uploads/{year}/{m:02d}/{week:02d}_tyden.pdf"
            for m in list(dict.fromkeys([start.month, (start+timedelta(6)).month]))]
    for url in urls:
        c = get(url, binary=True)
        if not c: continue
        try:
            with pdfplumber.open(io.BytesIO(c)) as pdf:
                text = "\n".join(p.extract_text() or "" for p in pdf.pages)
            m = RE_ARI_NATIONAL.search(text)
            if not m: continue
            ari = parse_num(m.group(1))
            ili_m = RE_ILI.search(text)
            e = make_entry(year, week, ari, float(ili_m.group(1)) if ili_m else None,
                           source=url, note="SZÚ PDF")
            print(f"  [PDF] W{week}/{year}: ARI={e['ari_per_100k']}")
            return e
        except Exception as ex:
            print(f"  PDF chyba: {ex}")
    return None


# ── ZDROJ 2: SZÚ tiskové zprávy ───────────────────────────────────────────────
def fetch_press_releases(target_weeks):
    """Prochází aktuality SZÚ, vrací dict {iso_key: entry}."""
    found = {}
    target_keys = {iso_key(y,w) for y,w in target_weeks}
    for page_url in ["https://szu.gov.cz/aktuality/", "https://szu.gov.cz/zpravy-chripka-sars-cov-2-ari-ili/"]:
        html = get(page_url)
        if not html: continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            if len(found) >= len(target_keys): break
            txt = a.get_text(" ", strip=True).lower()
            href = a["href"]
            if not href.startswith("http"): href = "https://szu.gov.cz" + href
            if not any(kw in txt for kw in ["nemocnost","respiračními","ari","chřipk"]): continue
            time.sleep(0.6)
            detail = get(href)
            if not detail: continue
            text = BeautifulSoup(detail,"html.parser").get_text(" ")
            mw = RE_WEEK.search(text)
            if not mw: continue
            week, year = int(mw.group(1)), int(mw.group(2))
            key = iso_key(year, week)
            if key not in target_keys: continue
            ma = RE_ARI_NATIONAL.search(text)
            if not ma: continue
            ari = parse_num(ma.group(1))
            ili_m = RE_ILI.search(text)
            e = make_entry(year, week, ari, float(ili_m.group(1)) if ili_m else None,
                           source=href, note="SZÚ tisková zpráva")
            found[key] = e
            print(f"  [Zprava] W{week}/{year}: ARI={e['ari_per_100k']}")
    return found


# ── ZDROJ 3: KHS Středočeský kraj ─────────────────────────────────────────────
def fetch_khs_stc(year, week):
    """Stáhne regionální data KHS StC a přepočítá na národní odhad."""
    ordinal_map = {1:"1.",2:"2.",3:"3.",4:"4.",5:"5.",6:"6.",7:"7.",8:"8.",9:"9.",
                   10:"10.",11:"11.",12:"12.",13:"13.",14:"14.",15:"15.",16:"16.",
                   17:"17.",18:"18.",19:"19.",20:"20.",21:"21.",22:"22.",23:"23.",
                   24:"24.",25:"25.",26:"26.",27:"27.",28:"28.",29:"29.",30:"30.",
                   31:"31.",32:"32.",33:"33.",34:"34.",35:"35.",36:"36.",37:"37.",
                   38:"38.",39:"39.",40:"40.",41:"41.",42:"42.",43:"43.",44:"44.",
                   45:"45.",46:"46.",47:"47.",48:"48.",49:"49.",50:"50.",51:"51.",52:"52."}
    ord_word = ordinal_map.get(week, f"{week}.")

    # URL vzor pro StC
    # "ve-13-kalendarnim-tydnu" nebo "v-13-kalendarnim-tydnu"
    slug_v  = f"v-{week}-kalendarnim-tydnu-{year}"
    slug_ve = f"ve-{week}-kalendarnim-tydnu-{year}"
    base = "https://khsstc.cz/informace-o-epidemiologicke-situaci-ve-vyskytu-akutnich-respiracnich-infekci-a-chripky-"
    for slug in [slug_ve, slug_v]:
        url = base + slug + "/"
        html = get(url)
        if not html: continue
        soup = BeautifulSoup(html,"html.parser")
        text = soup.get_text(" ")
        m = RE_ARI_REGIONAL.search(text)
        if not m: continue
        regional_ari = parse_num(m.group(1))
        national_est = round(regional_ari * KHS_SCALE["stc"])
        e = make_entry(year, week, national_est, source=url,
                       note=f"Odhad: KHS StC={regional_ari} × {KHS_SCALE['stc']}")
        print(f"  [KHS StC] W{week}/{year}: regional={regional_ari} → national≈{national_est}")
        return e
    return None


# ── ZDROJ 4: KHS Praha ────────────────────────────────────────────────────────
def fetch_khs_praha(year, week):
    """Stáhne data Hyg. stanice Praha a přepočítá na národní odhad."""
    base = "https://www.hygpraha.cz/informace-k-aktualni-epidemiologicke-situaci-ve-vyskytu-akutnich-respiracnich-infekci-vcetne-chripky-na-uzemi-hl-m-prahy-v-"
    slug = f"{week}-tydnu-roku-{year}"
    url = base + slug + "/"
    html = get(url)
    if not html: return None
    soup = BeautifulSoup(html,"html.parser")
    text = soup.get_text(" ")
    m = RE_ARI_REGIONAL.search(text)
    if not m: return None
    regional_ari = parse_num(m.group(1))
    national_est = round(regional_ari * KHS_SCALE["praha"])
    e = make_entry(year, week, national_est, source=url,
                   note=f"Odhad: KHS Praha={regional_ari} × {KHS_SCALE['praha']}")
    print(f"  [KHS Praha] W{week}/{year}: regional={regional_ari} → national≈{national_est}")
    return e


# ── HLAVNÍ LOGIKA ─────────────────────────────────────────────────────────────
def weeks_to_check(data):
    today  = date.today()
    cy, cw = today.isocalendar()[0], today.isocalendar()[1]
    valid  = [h for h in data["history"] if h.get("ari_per_100k") is not None]
    if not valid: return [(cy, cw-1)]
    last = valid[-1]
    y, w = last["year"], last["iso_week"] + 1
    missing = []
    while (y, w) <= (cy, cw-1):
        key = iso_key(y, w)
        ex = next((h for h in data["history"] if h["week"] == key), None)
        if ex is None or ex.get("ari_per_100k") is None:
            missing.append((y, w))
        w += 1
        if w > 52: w = 1; y += 1
    return missing

def compute_forecast(history, n=4):
    valid = [h for h in history if h.get("ari_per_100k") is not None]
    if len(valid) < 4: return []
    last = valid[-1]
    ma4  = sum(h["ari_per_100k"] for h in valid[-4:]) / 4
    prior= {h["iso_week"]: h["ari_per_100k"] for h in valid if h["year"] == last["year"]-1}
    fc_list = []
    for i in range(1, n+1):
        fw, fy = last["iso_week"]+i, last["year"]
        if fw > 52: fw -= 52; fy += 1
        p = prior.get(fw)
        if p and p > 0:
            yoy = last["ari_per_100k"] / (prior.get(last["iso_week"]) or p)
            fc  = round(0.5*ma4 + 0.5*p*yoy, 1)
        else:
            fc = round(ma4*0.92, 1)
        fc_list.append({"week": iso_key(fy,fw), "ari_forecast": max(0,fc),
                        "direction": "spíš nižší" if fc < last["ari_per_100k"] else "spíš vyšší"})
    return fc_list

def main():
    print("ARI Dashboard – update dat")
    data    = load_data()
    missing = weeks_to_check(data)
    print(f"Chybejici: {[iso_key(y,w) for y,w in missing] or 'zadne'}")
    new_data = {}

    for year, week in missing:
        key = iso_key(year, week)
        # Kaskáda zdrojů: PDF → tisková zpráva → KHS StC → KHS Praha
        e = fetch_pdf(year, week)
        if not e:
            time.sleep(0.5)
        if not e:
            press = fetch_press_releases([(year, week)])
            e = press.get(key)
        if not e:
            time.sleep(0.5)
            e = fetch_khs_stc(year, week)
        if not e:
            time.sleep(0.5)
            e = fetch_khs_praha(year, week)
        if e:
            new_data[key] = e
        else:
            print(f"  [MISS] W{week}/{year}: žádný zdroj nenalezl data")

    # Ulož do history
    existing = {h["week"]: i for i, h in enumerate(data["history"])}
    changed = False
    for key, e in new_data.items():
        if key in existing:
            if data["history"][existing[key]].get("ari_per_100k") is None:
                data["history"][existing[key]].update(e); changed = True
        else:
            data["history"].append(e); changed = True

    if changed:
        data["history"].sort(key=lambda h: (h["year"], h["iso_week"]))
        valid = [h for h in data["history"] if h.get("ari_per_100k") is not None]
        if valid:
            last = valid[-1]
            data["current"] = {
                "week": last["week"], "ari_per_100k": last["ari_per_100k"],
                "ili_per_100k": last.get("ili_per_100k"),
                "load_pct": round(last["ari_per_100k"]/data["meta"]["baseline_ari_per_100k"],4),
                "trend": "klesající", "source_note": last.get("note","")
            }
        data["forecast"] = compute_forecast(data["history"])
        print(f"Pridano/aktualizovano {len(new_data)} zaznamu.")

    data["meta"]["last_updated"] = date.today().isoformat()
    save_data(data)
    return 0

if __name__ == "__main__":
    sys.exit(main())
