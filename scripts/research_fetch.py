#!/usr/bin/env python3
"""Jednorázový průzkum zdrojů historických dat ARI (běží na GitHub runneru).

Stáhne surové stránky/datové soubory do research/raw/ a sepíše index.
Slouží k nalezení dlouhé historie týdenní nemocnosti ARI/ILI v ČR.
"""
import io, json, re, sys, time
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

OUT = Path(__file__).parent.parent / "research" / "raw"
OUT.mkdir(parents=True, exist_ok=True)
H = {"User-Agent": "Mozilla/5.0 (compatible; ARI-Dashboard-research/1.0)"}
INDEX = []

def safe_name(url):
    p = urlparse(url)
    s = (p.netloc + p.path + ("_" + p.query if p.query else "")).strip("/")
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s)
    return s[:180] or "root"

def fetch(url, save=True, binary=None, timeout=40):
    try:
        r = requests.get(url, headers=H, timeout=timeout)
        ct = r.headers.get("content-type", "")
        rec = {"url": url, "status": r.status_code, "ctype": ct, "bytes": len(r.content)}
        if save and r.ok:
            name = safe_name(url)
            if not Path(name).suffix:
                if "html" in ct: name += ".html"
                elif "json" in ct: name += ".json"
                elif "csv" in ct: name += ".csv"
            (OUT / name).write_bytes(r.content)
            rec["file"] = name
        INDEX.append(rec)
        print(f"{r.status_code} {len(r.content):>9} {url}")
        return r if r.ok else None
    except Exception as e:
        INDEX.append({"url": url, "error": repr(e)[:200]})
        print(f"ERR {url}: {e!r}"[:300])
        return None

def links(html, base):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for tag, attr in (("a", "href"), ("script", "src"), ("iframe", "src"),
                      ("link", "href"), ("img", "src"), ("frame", "src")):
        for t in soup.find_all(tag):
            v = t.get(attr)
            if v:
                out.append((tag, t.get_text(" ", strip=True)[:80], urljoin(base, v)))
    return out

DATA_EXT = (".js", ".json", ".csv", ".txt", ".xlsx", ".xls", ".dat", ".xml", ".tsv")

# ── 1) SZÚ dlouhodobý graf ARI (apps.szu.cz) ─────────────────────────────────
def explore_apps():
    seen = set()
    queue = ["https://apps.szu.cz/ari/ARO.html", "https://apps.szu.cz/ari/",
             "https://apps.szu.cz/ari/index.html", "https://apps.szu.cz/ari/ILI.html",
             "https://apps.szu.cz/ari/ARI.html", "https://apps.szu.cz/"]
    while queue and len(seen) < 60:
        u = queue.pop(0)
        if u in seen: continue
        seen.add(u)
        r = fetch(u)
        if not r: continue
        ct = r.headers.get("content-type", "")
        text = r.text if ("html" in ct or "javascript" in ct or u.endswith(".js")) else ""
        if "html" in ct:
            for tag, txt, href in links(text, u):
                if "apps.szu.cz" in href and href not in seen:
                    if href.lower().endswith(DATA_EXT) or href.lower().endswith(".html") or href.endswith("/"):
                        queue.append(href)
        # URL/datové soubory odkazované uvnitř JS/HTML (fetch('...'), "data/..csv" apod.)
        for m in re.finditer(r"""["']([^"'\s]+?\.(?:csv|json|js|txt|xlsx?|tsv|dat|xml))["']""", text):
            href = urljoin(u, m.group(1))
            if "apps.szu.cz" in href and href not in seen:
                queue.append(href)

# ── 2) SZÚ datová stránka + archivy sezón ────────────────────────────────────
def explore_szu():
    pages = [
        "https://szu.gov.cz/publikace-szu/data/akutni-respiracni-infekce-chripka/",
        "https://szu.gov.cz/en/publications-data/incidence-of-acute-respiratory-infections-and-influenza-in-the-czech-republic/",
        "https://szu.gov.cz/zpravy-chripka-sars-cov-2-ari-ili/",
        "https://szu.gov.cz/temata-zdravi-a-bezpecnosti/a-z-infekce/ch/chripka/",
        "https://szu.gov.cz/temata-zdravi-a-bezpecnosti/a-z-infekce/ch/chripka/zprava-o-chripkove-aktivite-hlaseni-a-vysledky-laboratornich-vysetreni/",
    ]
    season_pages = set()
    all_links = []
    for p in pages:
        r = fetch(p)
        if not r: continue
        for tag, txt, href in links(r.text, p):
            all_links.append({"page": p, "tag": tag, "text": txt, "href": href})
            low = href.lower()
            if "sezona-20" in low or "sezona_20" in low or "season-20" in low:
                season_pages.add(href.split("#")[0])
    # zkus i odhadnuté URL sezón 2010/11 .. 2025/26
    base = "https://szu.gov.cz/temata-zdravi-a-bezpecnosti/a-z-infekce/ch/chripka/zprava-o-chripkove-aktivite-hlaseni-a-vysledky-laboratornich-vysetreni/"
    for y in range(2010, 2026):
        season_pages.add(f"{base}chripkova-sezona-{y}-{y+1}/")
    season_links = {}
    for sp in sorted(season_pages):
        r = fetch(sp)
        if not r: continue
        lst = []
        for tag, txt, href in links(r.text, sp):
            low = href.lower()
            if low.endswith((".pdf", ".xlsx", ".xls", ".csv")):
                lst.append({"text": txt, "href": href})
        season_links[sp] = lst
        time.sleep(0.3)
    (OUT / "_szu_links.json").write_text(json.dumps(all_links, ensure_ascii=False, indent=1))
    (OUT / "_szu_season_links.json").write_text(json.dumps(season_links, ensure_ascii=False, indent=1))
    # stáhni všechny datové soubory (xlsx/xls/csv) odkudkoli z SZÚ stránek
    data_files = {l["href"] for l in all_links if l["href"].lower().endswith((".xlsx", ".xls", ".csv"))}
    for lst in season_links.values():
        data_files |= {l["href"] for l in lst if l["href"].lower().endswith((".xlsx", ".xls", ".csv"))}
    for f in sorted(data_files)[:40]:
        fetch(f)
    # vzorek týdenních PDF z různých sezón → text (formát se v čase mění)
    samples = []
    for sp, lst in season_links.items():
        pdfs = [l["href"] for l in lst if re.search(r"(\d{1,2})_tyden\.pdf$", l["href"], re.I)
                or "tyden" in l["href"].lower()]
        samples += pdfs[:2]
    data_pdfs = [l["href"] for l in all_links if re.search(r"/\d{1,2}_tyden\.pdf$", l["href"], re.I)]
    samples += data_pdfs[:1] + data_pdfs[-1:]
    texts = {}
    for u in samples[:40]:
        r = fetch(u, save=False)
        if not r or pdfplumber is None: continue
        try:
            with pdfplumber.open(io.BytesIO(r.content)) as pdf:
                texts[u] = "\n".join(pg.extract_text() or "" for pg in pdf.pages)[:6000]
        except Exception as e:
            texts[u] = f"PDF ERR {e!r}"
    (OUT / "_pdf_samples.json").write_text(json.dumps(texts, ensure_ascii=False, indent=1))

# ── 3) WHO FluID (epidemiologická data, CZE) ─────────────────────────────────
def explore_who():
    for u in [
        "https://xmart-api-public.who.int/FLUMART/VIW_FID_EPI?$format=csv&$filter=COUNTRY_CODE%20eq%20%27CZE%27",
        "https://xmart-api-public.who.int/FLUMART/VIW_FID?$format=csv&$filter=COUNTRY_CODE%20eq%20%27CZE%27",
        "https://xmart-api-public.who.int/FLUMART/VIW_FNT?$format=csv&$filter=COUNTRY_CODE%20eq%20%27CZE%27",
    ]:
        fetch(u, timeout=120)

if __name__ == "__main__":
    for fn in (explore_apps, explore_szu, explore_who):
        try:
            fn()
        except Exception as e:
            print(f"{fn.__name__} selhalo: {e!r}")
    (OUT / "_index.json").write_text(json.dumps(INDEX, ensure_ascii=False, indent=1))
    print(f"Hotovo, {len(INDEX)} požadavků.")
