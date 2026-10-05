# 🩺 ARI Dashboard – Návod k nasazení
**Automaticky aktualizovaný dashboard pro plánování akutní péče v ordinaci**

---

## Co dostanete

- **Webový dashboard** přístupný odkaz pro všechny kolegy (žádná instalace)
- **Automatická aktualizace** – kontrola webu SZÚ dvakrát denně, nová data se převezmou hned po zveřejnění
- **Semafor a doporučení** – kolik akutních slotů plánovat tento týden
- **Předpověď** na 10 týdnů (≈ 2 měsíce) s 50% a 80% rozmezím, ověřená na minulých sezónách
- **Zdarma** – využívá GitHub Pages + GitHub Actions (free tier)

---

## MOŽNOST A – GitHub Pages (doporučeno, zdarma, automatické)

### Krok 1 – Vytvořte si GitHub účet
1. Jděte na **https://github.com/signup**
2. Zaregistrujte se (zdarma)

### Krok 2 – Vytvořte nový repozitář
1. Přihlaste se na GitHub
2. Klikněte na zelené tlačítko **"New repository"**
3. Název: `ari-dashboard`
4. Nastavte: **Public** ✅ (nutné pro GitHub Pages free)
5. Klikněte **"Create repository"**

### Krok 3 – Nahrajte soubory
1. Na stránce repozitáře klikněte **"uploading an existing file"**
2. Přetáhněte všechny soubory z tohoto repozitáře (na GitHubu **Code → Download ZIP** stáhne vždy aktuální verzi), včetně složek `data/`, `scripts/` a `.github/`. Jednodušší alternativa místo kroků 2–3: na stránce repozitáře klikněte **Fork** – zkopíruje se vše najednou. **U forku jsou automatické běhy zpočátku vypnuté:** otevřete záložku **Actions**, klikněte na „I understand my workflows, go ahead and enable them“ (případně u „Weekly ARI Data Update“ na **Enable workflow**) a jednou ho spusťte přes **Run workflow** – musí doběhnout zeleně. Krok 4 (Settings → Pages) platí i pro fork.
3. Commit: "Přidání ARI dashboardu"
4. Klikněte **"Commit changes"**

**Důležité:** Složka `.github/workflows/` musí být nahrána správně.
Pokud váš počítač skryje složky začínající tečkou, použijte GitHub Desktop nebo web upload přes drag & drop celé složky.

### Krok 4 – Zapněte GitHub Pages
1. V repozitáři jděte do **Settings → Pages**
2. Source: **Deploy from a branch**
3. Branch: **main**, složka: **/ (root)**
4. Klikněte **Save**
5. Po cca 1 minutě dostanete URL ve tvaru:
   `https://VAŠE-JMÉNO.github.io/ari-dashboard/`

### Krok 5 – Sdílejte odkaz s kolegy
Tento odkaz funguje pro kohokoliv bez přihlašování. Sdílejte ho v ordinaci, přidejte do záložek telefonu nebo QR kód.

### Automatická aktualizace
- GitHub kontroluje datovou stránku SZÚ **dvakrát denně** (ráno a odpoledne). SZÚ zveřejňuje report za uplynulý týden v průběhu následujícího týdne – dashboard ho tak převezme ještě týž den.
- Při každé kontrole se doplní i týdny, které dřív chyběly, přepočítá se předpověď na 10 týdnů a doporučení.
- Commit (a přebudování stránky) vznikne jen tehdy, když přibudou nová data.
- Před každou aktualizací i po ní proběhnou automatické testy (`scripts/test_forecast.py`), takže se nikdy neuloží nesmyslná data.
- Když se stažení nepovede (web SZÚ nedostupný, nečitelný report, data starší než 4 týdny), běh skončí chybou a GitHub pošle vlastníkovi repozitáře e-mail.
- Ručně lze spustit: **Actions → Weekly ARI Data Update → Run workflow**

---

## MOŽNOST B – Google Sheets (pokud chcete editaci a sdílení v Google ekosystému)

### Krok 1 – Zkopírujte data do Google Sheets
1. Otevřete Google Tabulky na **sheets.google.com**
2. Vytvořte nový list
3. Importujte `data/ari_data.json` nebo zadávejte data ručně každý týden

### Krok 2 – Připojte Google Looker Studio (dashboard)
1. Jděte na **lookerstudio.google.com**
2. Klikněte **"Vytvořit" → "Sestava"**
3. Zdroj dat: **Google Tabulky**
4. Přidejte časový graf (ARI/100k) a KPI karty
5. Sdílejte odkaz s kolegy

### Krok 3 – Ruční aktualizace (každý týden)
Jednou týdně zkontrolujte:
- https://szu.gov.cz/publikace-szu/data/akutni-respiracni-infekce-chripka/ (týdenní PDF „ARI+komentář", řádek „Česká republika", sloupec „Celkem")
- Doplňte nový týden do Google Sheetu
- Dashboard v Looker Studio se automaticky aktualizuje

---

## MOŽNOST C – Otevřít lokálně

Pro lokální otevření potřebujete spustit jednoduchý HTTP server (graf se načítá z internetu – knihovna Chart.js a písma, takže připojení je potřeba):

```bash
# Python (nejjednodušší)
cd ari-dashboard
python -m http.server 8080
# Pak otevřete: http://localhost:8080
```

Nebo použijte VS Code s rozšířením "Live Server".

---

## Interpretace dashboardu

| Pásmo | ARI / 100k | Typicky | Stupeň akutní kapacity |
|------|-----------|---------|-----------------|
| 🟢 Klidná sezóna | < 750 | léto | základ – akutní sloty obsazovat kontrolami a prevencí |
| 🟡 Mírně zvýšená | 750–999 | podzimní nástup, jarní ústup | připravenost – rezerva slotů, při růstu nezkracovat |
| 🟠 Zvýšená zátěž | 1 000–1 499 | podzim–zima | navýšeno |
| 🔴 Epidemická úroveň | ≥ 1 500 | chřipková vlna (prosinec–únor) | vysoce navýšeno; zimní krizový režim při epidemické úrovni (poslední hodnota ≥ 1 500) s růstem nebo v zimě před vrcholem |

Pásma jsou orientační hranice dashboardu (za epidemii SZÚ obvykle považuje ~1 600–1 700/100k). Konkrétní **% akutní kapacity** dashboard odhaduje zvlášť pro každý týden (vůči běžné letní úrovni ordinace pro dospělé) – viz METODIKA.md.

**Doporučení nezávisí jen na pásmu.** Dashboard kombinuje plánovanou hodnotu (horní odhad předpovědi za 2 týdny), trend bez svátkových týdnů, fázi sezóny, signál chřipky (ILI) a svátky. Navyšuje hned, ale snižuje jen při potvrzeném poklesu – zkrácení akutního času nikdy nedoporučí, když nemocnost nebo předpověď roste.

**Předpověď** na 10 týdnů (od posledních dat SZÚ, tj. zhruba 2 měsíce od dneška) je sezónní model ověřený zpětným testem na minulých sezónách; ukazuje nejpravděpodobnější hodnotu a 50% / 80% rozmezí. Podrobnosti v [METODIKA.md](METODIKA.md).

**Datum u týdne:** všude je uvedeno pondělí daného ISO týdne (např. „W39 · po 21. 9. 2026").

---

## Zdroje dat

- **SZÚ – datová stránka ARI (hlavní zdroj):** https://szu.gov.cz/publikace-szu/data/akutni-respiracni-infekce-chripka/
- **WHO FluID** – historie 2009–2023 (přepočtená na měřítko SZÚ, viz METODIKA.md)
- Data jsou ze státního dohledu nad infekčními chorobami – důvěryhodný a oficiální zdroj

---

## Časté dotazy

**Q: Dashboard se neaktualizoval automaticky.**
A: Zkontrolujte záložku "Actions" v GitHub repozitáři – podívejte se, zda workflow proběhl. Pokud SZÚ nezveřejnil nová data, soubor zůstane beze změny.

**Q: Odkud jsou data z minulých let?**
A: Od sezóny 2023/24 z oficiálních týdenních PDF SZÚ, starší roky (2009–2023) z databáze WHO FluID přepočtené na měřítko SZÚ. Každý týden v `data/ari_data.json` má pole `source` (`szu`, `who_scaled`, `interpolated`).

**Q: Proč je poslední týden o 1–2 týdny starší než dnešek?**
A: SZÚ zveřejňuje report za týden až v průběhu následujícího týdne. Týdny, které už proběhly, ale data k nim ještě nejsou, ukazuje dashboard jako odhad („proběhlo · čeká na data").

**Q: Jak dostat dashboard na telefon?**
A: Otevřete URL v Chrome/Safari a klikněte "Přidat na plochu" – funguje jako app.

---

*Dashboard vytvořen pro optimalizaci akutní péče v ordinacích praktických lékařů ČR.*
*Data: SZÚ Praha | Kód: open-source, volně šiřitelný*
