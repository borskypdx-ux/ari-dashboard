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
# Regionální (KHS) – zkoušíme více formulací, weby je často mění.
RE_ARI_REGIONAL_LIST = [
    re.compile(
        r"[Cc]elkov[aá]\s+nemocnost\s+(?:ARI\s+)?(?:čin[íi]la|dosáhla|byla|je|byl[ao]\s+na\s+[úu]rovni)\s+"
        r"([\d\s ]+(?:[,\.]\d+)?)\s*(?:případů\s+)?(?:onemocn[ěe]n[íi]\s+)?na\s+100[\s ]000",
        re.IGNORECASE),
    re.compile(
        r"nemocnost\s+(?:ARI|akutn[íi]mi\s+respira[čc]n[íi]mi\s+infekcemi)[^.]{0,60}?"
        r"([\d\s ]{2,}(?:[,\.]\d+)?)\s*(?:případů\s+)?(?:onemocn[ěe]n[íi]\s+)?na\s+100[\s ]000",
        re.IGNORECASE),
    re.compile(
        r"([\d\s ]{2,}(?:[,\.]\d+)?)\s*(?:případů\s+)?(?:onemocn[ěe]n[íi]\s+)?na\s+100[\s ]000"
        r"[^.]{0,40}?(?:ARI|akutn[íi]ch?\s+respira[čc]n[íi]ch?\s+infekc[íi])",
        re.IGNORECASE),
]
RE_ILI = re.compile(r"ILI.*?([\d]+)\s*(?:případů|onemocnění)?\s+na\s+100[\s ]000", re.IGNORECASE)


def match_regional_ari(text):
    """Vrátí první rozumnou regionální hodnotu ARI/100k, nebo None."""
    for rx in RE_ARI_REGIONAL_LIST:
        m = rx.search(text)
        if not m:
            continue
        try:
            val = parse_num(m.group(1))
        except ValueError:
            continue
        # Sanity check – ARI/100k se reálně pohybuje řádově ve stovkách až tisících
        if 50 <= val <= 20000:
            return val
    return None


def load_data():
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Ulozeno: {DATA_FILE}")

def iso_key(year, week):
    return f"{year}-W{week:02d}"

def weeks_in_isoyear(year):
    """Počet ISO týdnů v daném roce – 52 nebo 53 (např. 2026 a 2020 mají 53)."""
    return date(year, 12, 28).isocalendar()[1]

def next_iso_week(year, week):
    """Následující ISO týden, korektně přes přelom roku (i 53týdenní roky)."""
    return (year + 1, 1) if week >= weeks_in_isoyear(year) else (year, week + 1)

def prev_iso_week(year, week):
    """Předchozí ISO týden, korektně přes přelom roku."""
    return (year - 1, weeks_in_isoyear(year - 1)) if week <= 1 else (year, week - 1)

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


# ── ZDROJ 1b: SZÚ datová stránka → index týdenních PDF ────────────────────────
# Týdenní reporty SZÚ jsou vystaveny na datové stránce a soubor je uložen
# podle MĚSÍCE VYDÁNÍ (např. W22 leží v /2026/06/, ne /2026/05/). Proto je
# spolehlivější vzít odkazy přímo z indexu než hádat měsíc z čísla týdne.
RE_SZU_PDF = re.compile(r"/wp-content/uploads/(\d{4})/(\d{2})/(\d{1,2})_tyden\.pdf", re.IGNORECASE)
SZU_DATA_PAGE = "https://szu.gov.cz/publikace-szu/data/akutni-respiracni-infekce-chripka/"

def fetch_szu_data_index():
    """Vrátí mapu {(rok, týden): url_pdf} týdenních ARI reportů z datové stránky SZÚ."""
    html = get(SZU_DATA_PAGE)
    if not html:
        return {}
    index = {}
    for m in RE_SZU_PDF.finditer(html):
        year, _month, week = int(m.group(1)), int(m.group(2)), int(m.group(3))
        # Klíčujeme dvojicí (rok-v-cestě, týden). Pro letní týdny ISO rok == rok v cestě.
        index.setdefault((year, week), "https://szu.gov.cz" + m.group(0))
    return index

def _ari_from_pdf_text(text):
    """Z textu PDF vytáhne národní hodnotu ARI/100k – zkusí národní i obecné vzory."""
    m = RE_ARI_NATIONAL.search(text)
    if m:
        try:
            v = parse_num(m.group(1))
            if 50 <= v <= 20000:
                return v
        except ValueError:
            pass
    return match_regional_ari(text)

def fetch_szu_pdf_indexed(year, week, index):
    """Stáhne týdenní PDF SZÚ podle indexu a vytáhne národní ARI hodnotu."""
    if not HAS_PDF:
        return None
    url = index.get((year, week))
    if not url:
        return None
    c = get(url, binary=True)
    if not c:
        return None
    try:
        with pdfplumber.open(io.BytesIO(c)) as pdf:
            text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    except Exception as ex:
        print(f"  PDF chyba {url}: {ex}")
        return None
    ari = _ari_from_pdf_text(text)
    if ari is None:
        print(f"  [SZU PDF] W{week}/{year}: hodnota ARI nenalezena ({url}); "
              f"délka textu={len(text)}")
        # Ladění: vypiš celý text PDF (newliny → ' | '), ať vidíme strukturu tabulky.
        flat = " | ".join(line.strip() for line in text.splitlines() if line.strip())
        print(f"      [FULLTEXT W{week}] {flat[:2200]}")
        return None
    ili_m = RE_ILI.search(text)
    e = make_entry(year, week, ari, float(ili_m.group(1)) if ili_m else None,
                   source=url, note="SZÚ týdenní PDF (datová stránka)")
    print(f"  [SZU PDF] W{week}/{year}: ARI={e['ari_per_100k']} ({url})")
    return e


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


def discover_week_link(index_urls, year, week):
    """Projde indexové stránky KHS a najde odkaz na článek pro daný týden.
    Vrací absolutní URL nebo None. Robustní vůči změnám URL slugů."""
    needles = [f"-{week}-kalendarnim-tydnu", f"v-{week}-kalendarnim",
               f"{week}-tydnu-roku-{year}", f"{week}-tydnu-{year}"]
    for index_url in index_urls:
        html = get(index_url)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        host = "/".join(index_url.split("/")[:3])
        for a in soup.find_all("a", href=True):
            href = a["href"]
            low  = href.lower()
            if str(year) not in low:
                continue
            if not any(n in low for n in needles):
                continue
            if not href.startswith("http"):
                href = host + ("" if href.startswith("/") else "/") + href
            return href
    return None


# ── ZDROJ 3: KHS Středočeský kraj ─────────────────────────────────────────────
def fetch_khs_stc(year, week):
    """Stáhne regionální data KHS StC a přepočítá na národní odhad."""
    base = "https://khsstc.cz/informace-o-epidemiologicke-situaci-ve-vyskytu-akutnich-respiracnich-infekci-a-chripky-"
    # Více variant slugů – web mění předložku (v/ve) i koncovku.
    candidates = [
        base + f"ve-{week}-kalendarnim-tydnu-{year}/",
        base + f"v-{week}-kalendarnim-tydnu-{year}/",
        base + f"ve-{week}-kalendarnim-tydnu-roku-{year}/",
        base + f"v-{week}-kalendarnim-tydnu-roku-{year}/",
    ]
    # Pokud přímé URL nevyjdou, zkus najít odkaz na indexových stránkách.
    discovered = discover_week_link(
        ["https://khsstc.cz/category/aktuality/", "https://khsstc.cz/"],
        year, week)
    if discovered:
        candidates.append(discovered)

    for url in candidates:
        html = get(url)
        if not html:
            continue
        text = BeautifulSoup(html, "html.parser").get_text(" ")
        regional_ari = match_regional_ari(text)
        if regional_ari is None:
            continue
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
    candidates = [
        base + f"{week}-tydnu-roku-{year}/",
        base + f"{week}-kalendarnim-tydnu-roku-{year}/",
        base + f"{week}-tydnu-{year}/",
    ]
    discovered = discover_week_link(
        ["https://www.hygpraha.cz/aktuality/", "https://www.hygpraha.cz/"],
        year, week)
    if discovered:
        candidates.append(discovered)

    for url in candidates:
        html = get(url)
        if not html:
            continue
        text = BeautifulSoup(html, "html.parser").get_text(" ")
        regional_ari = match_regional_ari(text)
        if regional_ari is None:
            continue
        national_est = round(regional_ari * KHS_SCALE["praha"])
        e = make_entry(year, week, national_est, source=url,
                       note=f"Odhad: KHS Praha={regional_ari} × {KHS_SCALE['praha']}")
        print(f"  [KHS Praha] W{week}/{year}: regional={regional_ari} → national≈{national_est}")
        return e
    return None


# ── HLAVNÍ LOGIKA ─────────────────────────────────────────────────────────────
def weeks_to_check(data, max_back=12, today=None):
    today  = today or date.today()
    cy, cw = today.isocalendar()[:2]
    # Poslední UZAVŘENÝ ISO týden (aktuální týden ještě nemá kompletní data)
    target = prev_iso_week(cy, cw)
    valid  = [h for h in data["history"] if h.get("ari_per_100k") is not None]
    if not valid:
        return [target]
    last = valid[-1]
    by_key = {h["week"]: h for h in data["history"]}
    y, w = next_iso_week(last["year"], last["iso_week"])
    missing = []
    # Pojistka proti zacyklení (poškozená data) – nikdy nejdeme dál než ~3 roky
    for _ in range(160):
        if (y, w) > target:
            break
        ex = by_key.get(iso_key(y, w))
        if ex is None or ex.get("ari_per_100k") is None:
            missing.append((y, w))
        y, w = next_iso_week(y, w)
    # Když je dashboard dlouho pozadu, doplň jen posledních max_back týdnů,
    # ať jeden běh nemusí stahovat desítky stránek.
    return missing[-max_back:]

def compute_forecast(history, n=4):
    valid = [h for h in history if h.get("ari_per_100k") is not None]
    if len(valid) < 4: return []
    last = valid[-1]
    ma4  = sum(h["ari_per_100k"] for h in valid[-4:]) / 4
    prior= {h["iso_week"]: h["ari_per_100k"] for h in valid if h["year"] == last["year"]-1}
    fc_list = []
    fy, fw = last["year"], last["iso_week"]
    for i in range(1, n+1):
        fy, fw = next_iso_week(fy, fw)
        p = prior.get(fw)
        if p and p > 0:
            yoy = last["ari_per_100k"] / (prior.get(last["iso_week"]) or p)
            fc  = round(0.5*ma4 + 0.5*p*yoy, 1)
        else:
            fc = round(ma4*0.92, 1)
        fc_list.append({"week": iso_key(fy,fw), "ari_forecast": max(0,fc),
                        "direction": "spíš nižší" if fc < last["ari_per_100k"] else "spíš vyšší"})
    return fc_list

def diagnose_sources():
    """Vypíše aktuální strukturu odkazů na klíčových stránkách zdrojů.
    Slouží k ladění, když scraper nic nenajde – z logu pak vidíme,
    jak vypadají reálné URL/odkazy teď (mění se v čase)."""
    print("=== DIAG: aktuální struktura zdrojů ===")
    pages = {
        "SZU-zpravy":   "https://szu.gov.cz/zpravy-chripka-sars-cov-2-ari-ili/",
        "SZU-data-ARI": "https://szu.gov.cz/publikace-szu/data/akutni-respiracni-infekce-chripka/",
        "SZU-aktuality":"https://szu.gov.cz/aktuality/",
        "KHS-StC-akt":  "https://khsstc.cz/category/aktuality/",
        "KHS-StC-home": "https://khsstc.cz/",
        "HygPraha-akt": "https://www.hygpraha.cz/aktuality/",
    }
    keys = ["tydn", "týdn", "respira", "ari", "chřip", "chrip", "nemocnost",
            ".xlsx", ".csv", ".pdf", "influenza"]
    for name, url in pages.items():
        html = get(url)
        if not html:
            print(f"  [{name}] NELZE NAČÍST {url}")
            continue
        soup = BeautifulSoup(html, "html.parser")
        rel = []
        for a in soup.find_all("a", href=True):
            txt  = a.get_text(" ", strip=True)
            href = a["href"]
            if any(k in (txt + " " + href).lower() for k in keys):
                rel.append(f"      {txt[:80]!r} -> {href}")
        print(f"  [{name}] {url} : {len(rel)} relevantních odkazů (zobrazuji max 30)")
        for line in rel[:30]:
            print(line)
    print("=== /DIAG ===")


def main():
    print("ARI Dashboard – update dat")
    data    = load_data()
    missing = weeks_to_check(data)
    print(f"Chybejici: {[iso_key(y,w) for y,w in missing] or 'zadne'}")
    new_data = {}

    # Index týdenních PDF z datové stránky SZÚ (primární, nejspolehlivější zdroj).
    szu_index = fetch_szu_data_index() if missing else {}
    print(f"SZU index: {len(szu_index)} týdenních PDF nalezeno"
          + (f" (např. {sorted(szu_index)[-3:]})" if szu_index else ""))

    for year, week in missing:
        key = iso_key(year, week)
        # Primární zdroj: týdenní PDF SZÚ z datové stránky (národní data, rok ulato).
        # Záloha: odhad měsíce v URL. KHS/tiskové zprávy se pro daný týden už
        # nevydávají (potvrzeno 404), proto je v hlavní smyčce nepoužíváme –
        # jen by běh zpomalovaly. Zůstávají k dispozici jako funkce pro zimní sezónu.
        e = fetch_szu_pdf_indexed(year, week, szu_index)
        if not e:
            e = fetch_pdf(year, week)
        if e:
            new_data[key] = e
        else:
            print(f"  [MISS] W{week}/{year}: žádný zdroj nenalezl data")

    # (diagnose_sources() je k dispozici ručně; v běžném běhu ho nevoláme,
    #  ať logy zůstanou čitelné a případné PDF snippety jsou na konci.)

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
        new_fc = compute_forecast(data["history"])
        if new_fc:   # jen přepiš pokud máme reálný výsledek
            data["forecast"] = new_fc
        print(f"Pridano/aktualizovano {len(new_data)} zaznamu.")

    data["meta"]["last_updated"] = date.today().isoformat()
    save_data(data)
    return 0

if __name__ == "__main__":
    sys.exit(main())
