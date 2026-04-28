#!/usr/bin/env python3
"""
ARI Dashboard – automatický update dat ze SZÚ
Zdroje (v pořadí priority):
  1. SZÚ týdenní PDF reporty:  https://szu.gov.cz/wp-content/uploads/YYYY/MM/WW_tyden.pdf
  2. SZÚ tiskové zprávy:       https://szu.gov.cz/aktuality/  (regex parsing)
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
HEADERS   = {"User-Agent": "Mozilla/5.0 (compatible; ARI-Dashboard/2.0)"}

RE_WEEK = re.compile(r"V\s+(\d+)\.\s*t[ýy]dnu\s+roku\s+(\d{4})", re.IGNORECASE)
RE_ARI  = re.compile(
    r"(?:ARI|akutn[íi]ch?\s+respira[čc]n[íi]ch?\s+infekc[íi])"
    r".*?[úu]rovn[ěe]\s*([\d\s\u00a0]+(?:[,\.]\d+)?)\s*na\s*100[\s\u00a0]000",
    re.DOTALL | re.IGNORECASE
)
RE_ILI = re.compile(r"ILI.*?([\d]+)\s*na\s*100[\s\u00a0]000", re.IGNORECASE)


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

def parse_ari_text(text, year, week):
    m = RE_ARI.search(text)
    if not m:
        return None
    raw = m.group(1).replace(" ","").replace("\u00a0","").replace(",",".")
    try:
        ari = round(float(raw))
    except:
        return None
    m2 = RE_ILI.search(text)
    return {
        "week": iso_key(year, week),
        "year": year,
        "iso_week": week,
        "ari_per_100k": ari,
        "ili_per_100k": int(m2.group(1)) if m2 else None,
    }

def pdf_urls(year, week):
    start = date.fromisocalendar(year, week, 1)
    months = list(dict.fromkeys([start.month, (start + timedelta(6)).month]))
    return [f"https://szu.gov.cz/wp-content/uploads/{year}/{m:02d}/{week:02d}_tyden.pdf" for m in months]

def fetch_from_pdf(year, week):
    if not HAS_PDF:
        return None
    for url in pdf_urls(year, week):
        content = get(url, binary=True)
        if not content:
            continue
        try:
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                text = "\n".join(p.extract_text() or "" for p in pdf.pages)
            r = parse_ari_text(text, year, week)
            if r:
                r["source_url"] = url
                print(f"  PDF W{week}/{year}: ARI={r['ari_per_100k']}")
                return r
        except Exception as e:
            print(f"  PDF chyba {url}: {e}")
    return None

def fetch_press_releases():
    results = []
    for page_url in ["https://szu.gov.cz/aktuality/", "https://szu.gov.cz/zpravy-chripka-sars-cov-2-ari-ili/"]:
        html = get(page_url)
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            txt = a.get_text(" ", strip=True).lower()
            href = a["href"]
            if not href.startswith("http"):
                href = "https://szu.gov.cz" + href
            if not any(kw in txt for kw in ["nemocnost","respiračními","ari","chřipk"]):
                continue
            time.sleep(0.8)
            detail = get(href)
            if not detail:
                continue
            text = BeautifulSoup(detail, "html.parser").get_text(" ")
            mw = RE_WEEK.search(text)
            if not mw:
                continue
            week, year = int(mw.group(1)), int(mw.group(2))
            e = parse_ari_text(text, year, week)
            if e:
                e["source_url"] = href
                results.append(e)
                print(f"  Zprava W{week}/{year}: ARI={e['ari_per_100k']}")
    return results

def weeks_to_check(data):
    today = date.today()
    cy, cw = today.isocalendar()[0], today.isocalendar()[1]
    valid = [h for h in data["history"] if h.get("ari_per_100k") is not None]
    if not valid:
        return [(cy, cw - 1)]
    last = valid[-1]
    y, w = last["year"], last["iso_week"] + 1
    missing = []
    while (y, w) <= (cy, cw - 1):
        key = iso_key(y, w)
        existing = next((h for h in data["history"] if h["week"] == key), None)
        if existing is None or existing.get("ari_per_100k") is None:
            missing.append((y, w))
        w += 1
        if w > 52:
            w = 1; y += 1
    return missing

def compute_forecast(history, n=4):
    valid = [h for h in history if h.get("ari_per_100k") is not None]
    if len(valid) < 4:
        return []
    last = valid[-1]
    ma4 = sum(h["ari_per_100k"] for h in valid[-4:]) / 4
    prior = {h["iso_week"]: h["ari_per_100k"] for h in valid if h["year"] == last["year"] - 1}
    forecasts = []
    for i in range(1, n+1):
        fw, fy = last["iso_week"] + i, last["year"]
        if fw > 52:
            fw -= 52; fy += 1
        p = prior.get(fw)
        if p and p > 0:
            yoy = last["ari_per_100k"] / (prior.get(last["iso_week"]) or p)
            fc = round(0.5 * ma4 + 0.5 * p * yoy, 1)
        else:
            fc = round(ma4 * 0.92, 1)
        forecasts.append({"week": iso_key(fy, fw), "ari_forecast": max(0, fc),
                          "direction": "spis nizsi" if fc < last["ari_per_100k"] else "spis vyssi"})
    return forecasts

def main():
    print("Spoustim update ARI dat...")
    data = load_data()
    missing = weeks_to_check(data)
    new_data = []
    print(f"Chybejici tydny: {[iso_key(y,w) for y,w in missing] or 'zadne'}")

    for year, week in missing:
        e = fetch_from_pdf(year, week)
        if e:
            new_data.append(e)

    found = {e["week"] for e in new_data}
    still_missing = [(y, w) for y, w in missing if iso_key(y, w) not in found]
    if still_missing:
        print(f"PDF nenaslo {[iso_key(y,w) for y,w in still_missing]}, zkousim zpravy...")
        for e in fetch_press_releases():
            if e["week"] not in found:
                new_data.append(e)

    existing_weeks = {h["week"]: i for i, h in enumerate(data["history"])}
    changed = False
    for e in new_data:
        if e["week"] in existing_weeks:
            idx = existing_weeks[e["week"]]
            if data["history"][idx].get("ari_per_100k") is None:
                data["history"][idx].update(e)
                changed = True
        else:
            data["history"].append(e)
            changed = True

    if changed:
        data["history"].sort(key=lambda h: (h["year"], h["iso_week"]))
        valid = [h for h in data["history"] if h.get("ari_per_100k") is not None]
        if valid:
            last = valid[-1]
            data["current"] = {
                "week": last["week"],
                "ari_per_100k": last["ari_per_100k"],
                "ili_per_100k": last.get("ili_per_100k"),
                "load_pct": round(last["ari_per_100k"] / data["meta"]["baseline_ari_per_100k"], 4),
                "trend": "klesajici",
            }
        data["forecast"] = compute_forecast(data["history"])
        print(f"Pridano {len(new_data)} novych zaznamu.")
    else:
        print("Zadna nova data nalezena.")

    data["meta"]["last_updated"] = date.today().isoformat()
    save_data(data)
    return 0

if __name__ == "__main__":
    sys.exit(main())
