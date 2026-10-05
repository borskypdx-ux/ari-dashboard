#!/usr/bin/env python3
"""Zpětné doplnění historie ARI/ILI ze SZÚ (běží na GitHub runneru).

1) Tabulková týdenní PDF SZÚ („WW_tyden.pdf", od sezóny 2023/24):
   národní řádek „Česká republika – the Czech Republic" pro ARI i ILI
   (věkové skupiny + Celkem). Týden a rok se berou z HLAVIČKY PDF
   („39 týden 2026"), ne z cesty souboru (W52 bývá nahrán v lednu).
2) Prozaické zprávy NRL (2011–2023): hodnota ARI tam, kde je v textu uvedena
   („…nemocnost … ARI … na úrovni 1 110 nemocných na 100 000 obyvatel").

Výstup: research/backfill/szu_tables.json, nrl_prose.json, errors.json
"""
import io, json, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
import pdfplumber

ROOT = Path(__file__).parent.parent
RAW = ROOT / "research" / "raw"
OUT = ROOT / "research" / "backfill"
OUT.mkdir(parents=True, exist_ok=True)
H = {"User-Agent": "Mozilla/5.0 (compatible; ARI-Dashboard-backfill/1.0)"}
SESSION = requests.Session()
SESSION.headers.update(H)

BASE = ("https://szu.gov.cz/temata-zdravi-a-bezpecnosti/a-z-infekce/ch/chripka/"
        "zprava-o-chripkove-aktivite-hlaseni-a-vysledky-laboratornich-vysetreni/")
PAGES = [
    "https://szu.gov.cz/publikace-szu/data/akutni-respiracni-infekce-chripka/",
    BASE,
] + [f"{BASE}chripkova-sezona-{y}-{y+1}/" for y in range(2011, 2027)]

RE_TABLE_PDF = re.compile(r"/(\d{1,2})_tyden\.pdf$", re.I)
RE_NRL_PDF = re.compile(r"Zprava_NRL_(\d{1,2})_?tyden_(\d{4})", re.I)
RE_HDR = re.compile(r"(\d{1,2})\s*\.?\s*t[ýy]den\s+(\d{4})", re.I)
RE_CR_ROW = re.compile(r"[ČC]esk[áa]\s+republika\s*-\s*the\s+Czech\s+Republic\s+([\d][\d\s,\.]*)")
RE_PROSE = re.compile(
    r"(?:Ve?|V)\s+(\d{1,2})\.\s*(?:kalendářním\s+)?t[ýy]dnu.{0,220}?"
    r"(?:ARI|akutních\s+respiračních\s+infekcí).{0,160}?"
    r"(?:na\s+)?úrovn[iě]\s+([\d][\d\s ]{0,7})\s*(?:nemocných|případů|onemocnění)?\s*na\s+100[\s ]?000",
    re.I | re.S)

def get(url, binary=False, timeout=45):
    for attempt in range(3):
        try:
            r = SESSION.get(url, timeout=timeout)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.content if binary else r.text
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return None

def pdf_text(content):
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)

def nums(s):
    return [float(x.replace(",", ".")) for x in s.split() if re.fullmatch(r"[\d.,]+", x)]

def collect_links():
    table, nrl = set(), set()
    for p in PAGES:
        html = get(p)
        if not html:
            print(f"page miss {p}")
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = urljoin(p, a["href"]).split("#")[0]
            if RE_TABLE_PDF.search(href):
                table.add(href)
            elif RE_NRL_PDF.search(href) and href.lower().endswith(".pdf"):
                nrl.add(href)
        print(f"page ok {p}: table={len(table)} nrl={len(nrl)}")
    return sorted(table), sorted(nrl)

def parse_table_pdf(url):
    c = get(url, binary=True)
    if not c:
        return {"url": url, "error": "download"}
    try:
        t = pdf_text(c)
    except Exception as e:
        return {"url": url, "error": f"pdf {e!r}"[:200]}
    hdr = RE_HDR.search(t)
    rows = RE_CR_ROW.findall(t)
    rec = {"url": url, "hdr": hdr.group(0) if hdr else None,
           "week": int(hdr.group(1)) if hdr else None,
           "year": int(hdr.group(2)) if hdr else None}
    if rows:
        a = nums(rows[0]); rec["ari_groups"] = a
        rec["ari"] = a[-1] if a else None
    if len(rows) > 1:
        i = nums(rows[1]); rec["ili_groups"] = i
        rec["ili"] = i[-1] if i else None
    if not rows:
        rec["error"] = "no CR row"
        rec["text_head"] = t[:600]
    return rec

def parse_nrl_pdf(url):
    m = RE_NRL_PDF.search(url)
    c = get(url, binary=True)
    if not c:
        return {"url": url, "error": "download"}
    try:
        t = pdf_text(c)
    except Exception as e:
        return {"url": url, "error": f"pdf {e!r}"[:200]}
    flat = re.sub(r"\s+", " ", t)
    hits = []
    for pm in RE_PROSE.finditer(flat):
        try:
            v = float(re.sub(r"[\s ]", "", pm.group(2)))
        except ValueError:
            continue
        if 50 <= v <= 6000:
            hits.append({"week": int(pm.group(1)), "ari": v, "ctx": flat[max(0, pm.start()-20):pm.end()+10][:400]})
    return {"url": url, "file_week": int(m.group(1)), "file_year": int(m.group(2)), "hits": hits}

def run_pool(fn, items, workers=8):
    out = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, u): u for u in items}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                out.append(f.result())
            except Exception as e:
                out.append({"url": futs[f], "error": repr(e)[:200]})
            if i % 25 == 0:
                print(f"  {fn.__name__}: {i}/{len(items)}")
    return out

if __name__ == "__main__":
    t0 = time.time()
    table, nrl = collect_links()
    print(f"table PDFs: {len(table)}, NRL PDFs: {len(nrl)}")
    tab = run_pool(parse_table_pdf, table)
    (OUT / "szu_tables.json").write_text(json.dumps(sorted(tab, key=lambda r: (r.get("year") or 0, r.get("week") or 0)), ensure_ascii=False, indent=1))
    print(f"tables done in {time.time()-t0:.0f}s; ok={sum(1 for r in tab if r.get('ari') is not None)}")
    pro = run_pool(parse_nrl_pdf, nrl)
    (OUT / "nrl_prose.json").write_text(json.dumps(sorted(pro, key=lambda r: (r.get("file_year") or 0, r.get("file_week") or 0)), ensure_ascii=False, indent=1))
    print(f"nrl done in {time.time()-t0:.0f}s; with hits={sum(1 for r in pro if r.get('hits'))}")
