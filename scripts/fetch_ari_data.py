#!/usr/bin/env python3
"""
ARI Dashboard – automatický update dat ze SZÚ
Spouštěno každý pondělí přes GitHub Actions.
Scrape: https://szu.gov.cz/aktuality/
"""

import json
import re
import sys
import time
from datetime import datetime, date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

DATA_FILE = Path(__file__).parent.parent / "data" / "ari_data.json"
SZU_AKTUALITY = "https://szu.gov.cz/aktuality/"
SZU_WEEKLY    = "https://szu.gov.cz/zpravy-chripka-sars-cov-2-ari-ili/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ARI-Dashboard-Bot/1.0; +https://github.com)"
}

# Regex na tvar: "V X. týdnu roku 20XX dosáhla … nemocnost … úrovně NNNN na 100 000"
RE_WEEK = re.compile(r"V\s+(\d+)\.\s+t[ýy]dnu\s+roku\s+(\d{4})")
RE_ARI  = re.compile(r"[Nn]emocnost\s+(?:akutn[íi]ch?\s+respira[čc]n[íi]ch?\s+infekc[íi]\s+\(ARI\)\s+)?[úu]rovn[ěe]\s+([\d\s]+(?:,\d+)?)\s+na\s+100[\s\u00a0]000")
RE_ILI  = re.compile(r"(?:ILI|onemocn[ěe]n[íi]\s+podobn[áa]\s+ch[řr]ipce).*?([\d]+)\s+na\s+100[\s\u00a0]000", re.IGNORECASE)


def load_data() -> dict:
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_data(data: dict):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅  Data uložena: {DATA_FILE}")


def iso_week_key(year: int, week: int) -> str:
    return f"{year}-W{week:02d}"


def get_page(url: str) -> BeautifulSoup | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()
        r.encoding = "utf-8"
        return BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        print(f"⚠️  Chyba při načítání {url}: {e}")
        return None


def find_ari_links(soup: BeautifulSoup) -> list[str]:
    """Najde v přehledu aktualit odkazy na zprávy o ARI nemocnosti."""
    links = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True).lower()
        if any(kw in text for kw in ["nemocnost", "respiračními", "ari", "chřipk", "akutní respir"]):
            href = a["href"]
            if not href.startswith("http"):
                href = "https://szu.gov.cz" + href
            links.append(href)
    return list(dict.fromkeys(links))[:10]   # deduplicate, max 10


def parse_ari_from_page(soup: BeautifulSoup) -> dict | None:
    """Extrahuje číslo ARI/100k, týden a rok z textu stránky."""
    text = soup.get_text(" ", strip=True)

    m_week = RE_WEEK.search(text)
    m_ari  = RE_ARI.search(text)
    if not (m_week and m_ari):
        return None

    iso_week = int(m_week.group(1))
    year     = int(m_week.group(2))
    ari_str  = m_ari.group(1).replace(" ", "").replace("\u00a0", "").replace(",", ".")
    try:
        ari_val = float(ari_str)
    except ValueError:
        return None

    m_ili = RE_ILI.search(text)
    ili_val = float(m_ili.group(1)) if m_ili else None

    return {
        "week": iso_week_key(year, iso_week),
        "year": year,
        "iso_week": iso_week,
        "ari_per_100k": round(ari_val),
        "ili_per_100k": round(ili_val) if ili_val else None,
    }


def compute_forecast(history: list[dict], n: int = 4) -> list[dict]:
    """
    Jednoduchý forecast: 50 % krátkodobý trend + 50 % sezónní srovnání s loňskem.
    Pokud nemáme loňský týden, použijeme jen trend.
    """
    if len(history) < 4:
        return []

    last = history[-1]
    last_ari  = last["ari_per_100k"]
    last_year = last["year"]
    last_week = last["iso_week"]

    # 4týdenní klouzavý průměr (krátkodobý trend)
    recent_vals = [h["ari_per_100k"] for h in history[-4:] if h["ari_per_100k"] > 0]
    if not recent_vals:
        return []
    ma4 = sum(recent_vals) / len(recent_vals)

    # YoY srovnání
    prior_by_week = {h["iso_week"]: h["ari_per_100k"] for h in history if h["year"] == last_year - 1}

    forecasts = []
    for i in range(1, n + 1):
        fw = last_week + i
        fy = last_year
        if fw > 52:
            fw -= 52
            fy += 1

        prior_ari = prior_by_week.get(fw)
        if prior_ari and prior_ari > 0:
            yoy_ratio = last_ari / prior_ari if prior_ari else 1.0
            fc = round(0.5 * ma4 + 0.5 * prior_ari * yoy_ratio, 1)
        else:
            fc = round(ma4 * 0.92, 1)   # fallback: mírný pokles

        fc = max(0, fc)
        direction = "spíš nižší" if fc < last_ari else "spíš vyšší"

        forecasts.append({
            "week": iso_week_key(fy, fw),
            "ari_forecast": fc,
            "direction": direction,
        })

    return forecasts


def update_data(data: dict, new_entries: list[dict]) -> tuple[int, bool]:
    """Přidá nové záznamy do history. Vrátí (počet přidaných, changed)."""
    existing_weeks = {h["week"] for h in data["history"]}
    added = 0
    for entry in new_entries:
        if entry["week"] not in existing_weeks:
            data["history"].append(entry)
            existing_weeks.add(entry["week"])
            added += 1
            print(f"   ➕ Přidán týden {entry['week']}: ARI={entry['ari_per_100k']}/100k")

    if added:
        data["history"].sort(key=lambda h: (h["year"], h["iso_week"]))
        last = data["history"][-1]
        data["current"]["week"]        = last["week"]
        data["current"]["ari_per_100k"]= last["ari_per_100k"]
        if last.get("ili_per_100k"):
            data["current"]["ili_per_100k"] = last["ili_per_100k"]
        load = last["ari_per_100k"] / data["meta"]["baseline_ari_per_100k"]
        data["current"]["load_pct"] = round(load, 4)

        data["forecast"] = compute_forecast(data["history"])
        data["meta"]["last_updated"] = date.today().isoformat()

    return added, added > 0


def main():
    print("🔍  Načítám data ze SZÚ …")
    data = load_data()
    new_entries = []

    # --- Pokus 1: stránka zpráv ---
    soup = get_page(SZU_WEEKLY)
    links = find_ari_links(soup) if soup else []

    # --- Pokus 2: aktuality ---
    if len(links) < 2:
        soup2 = get_page(SZU_AKTUALITY)
        if soup2:
            links += find_ari_links(soup2)

    print(f"   Nalezeno {len(links)} relevantních odkazů")
    for url in links[:8]:
        time.sleep(1)
        detail = get_page(url)
        if not detail:
            continue
        entry = parse_ari_from_page(detail)
        if entry:
            new_entries.append(entry)
            print(f"   📄  {url}  →  {entry['week']} ARI={entry['ari_per_100k']}")

    added, changed = update_data(data, new_entries)

    if changed:
        save_data(data)
        print(f"✅  Hotovo – přidáno {added} nových týdnů.")
    else:
        print("ℹ️   Žádná nová data nenalezena. Soubor beze změn.")
        data["meta"]["last_updated"] = date.today().isoformat()
        save_data(data)

    return 0


if __name__ == "__main__":
    sys.exit(main())
