# 🩺 ARI Dashboard – Návod k nasazení
**Automaticky aktualizovaný dashboard pro plánování akutní péče v ordinaci**

---

## Co dostanete

- **Webový dashboard** přístupný odkaz pro všechny kolegy (žádná instalace)
- **Automatická aktualizace** každé pondělí z webu SZÚ
- **Semafor a doporučení** – kolik akutních slotů plánovat tento týden
- **Forecast** na 4 týdny dopředu
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
2. Přetáhněte všechny soubory z tohoto ZIP (včetně složky `data/` a `.github/`)
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
- Každé **pondělí v 20:00** GitHub automaticky stáhne nová data ze SZÚ
- Pokud SZÚ data zveřejní, dashboard se sám aktualizuje
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
Každé pondělí zkontrolujte:
- https://szu.gov.cz/zpravy-chripka-sars-cov-2-ari-ili/
- Doplňte nový týden do Google Sheetu
- Dashboard v Looker Studio se automaticky aktualizuje

---

## MOŽNOST C – Otevřít lokálně (bez internetu)

Pro lokální otevření potřebujete spustit jednoduchý HTTP server:

```bash
# Python (nejjednodušší)
cd ari-dashboard
python -m http.server 8080
# Pak otevřete: http://localhost:8080
```

Nebo použijte VS Code s rozšířením "Live Server".

---

## Interpretace dashboardu

| Stav | ARI / 100k | % z maxima | Doporučení |
|------|-----------|------------|------------|
| 🟢 Klid | < 205 | < 10 % | Normální provoz |
| 🟡 Zvýšená zátěž | 205–680 | 10–33 % | +10–20 % akutní sloty |
| 🔴 Vysoká zátěž | > 680 | > 33 % | +20–40 % akutní sloty, triáž |

**Baseline:** 2 053 / 100k = průměrný ARI únor 2025 (nejhorší měsíc v 2025)

---

## Zdroje dat

- **SZÚ – týdenní zprávy:** https://szu.gov.cz/zpravy-chripka-sars-cov-2-ari-ili/
- **SZÚ – datová stránka ARI:** https://szu.gov.cz/publikace-szu/data/akutni-respiracni-infekce-chripka/
- Data jsou ze státního dohledu nad infekčními chorobami – důvěryhodný a oficiální zdroj

---

## Časté dotazy

**Q: Dashboard se neaktualizoval automaticky.**
A: Zkontrolujte záložku "Actions" v GitHub repozitáři – podívejte se, zda workflow proběhl. Pokud SZÚ nezveřejnil nová data, soubor zůstane beze změny.

**Q: Chci přidat data z minulých let.**
A: Editujte soubor `data/ari_data.json` – přidejte záznamy do pole `history`. Formát: `{"week":"2024-W01","year":2024,"iso_week":1,"ari_per_100k":600,"ili_per_100k":25}`.

**Q: Jak dostat dashboard na telefon?**
A: Otevřete URL v Chrome/Safari a klikněte "Přidat na plochu" – funguje jako app.

---

*Dashboard vytvořen pro optimalizaci akutní péče v ordinacích praktických lékařů ČR.*
*Data: SZÚ Praha | Kód: open-source, volně šiřitelný*
