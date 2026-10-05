# Metodika ARI dashboardu

Dashboard ukazuje týdenní nemocnost akutními respiračními infekcemi (ARI) v ČR na 100 000 obyvatel, předpověď na 8 týdnů a doporučení pro plánování akutní kapacity ordinace. Tento dokument popisuje, odkud data jsou, jak se počítá předpověď a jak se z ní odvozují doporučení.

## 1. Data

### Zdroj aktuálních dat – SZÚ
Státní zdravotní ústav zveřejňuje každý týden PDF report na [datové stránce ARI/chřipka](https://szu.gov.cz/publikace-szu/data/akutni-respiracni-infekce-chripka/). Report obsahuje tabulku *Relativní nemocnost na 100000 obyvatel* a národní řádek *Česká republika – the Czech Republic* (věkové skupiny 0–5, 6–14, 15–24, 25–64, 65+ a **Celkem**). Dashboard bere sloupec **Celkem** pro ARI i ILI.

- **Týden a rok se berou z hlavičky PDF** („39 týden 2026"), ne z cesty souboru – report za W52 bývá nahrán až v lednu (`/2026/01/52_tyden.pdf`) a podle cesty by se přiřadil ke špatnému roku.
- Datová stránka drží zhruba poslední rok reportů; každý běh doplní všechny týdny, které ještě nemají oficiální hodnotu (dřívější verze doplňovala jen týdny po posledním známém, takže jednou vynechaný týden už nikdy nedoplnila).
- SZÚ zveřejňuje report za týden v průběhu následujícího týdne. Kontrola proto běží dvakrát denně a data jsou obvykle 1–2 týdny „pozadu" za kalendářem; dashboard to přiznává (týdny označené *proběhlo · čeká na data*).

### Historie 2009–2023 – WHO FluID
Pro sezónní srovnání a pro předpovědní model je potřeba víc sezón. Starší data jsou z databáze [WHO FluID](https://www.who.int/tools/flunet) (API `xmart-api-public.who.int/FLUMART/VIW_FID_EPI`, země CZE): týdenní `ARI_CASE / ARI_POP_COV` sečtené přes věkové skupiny 0–4, 5–14, 15–64 a 65+.

WHO a SZÚ počítají nemocnost s jinou pokrytou populací, proto se hodnoty WHO přepočítávají na měřítko SZÚ:

| Kalibrace | Hodnota | Podklad |
|---|---|---|
| ARI SZÚ / WHO | **1,20** | medián poměru ve 136 společných týdnech 2023–2026, rozptyl ~5 %, stejný v létě, na podzim, v zimě i na jaře |
| nezávislá kontrola | 1,18 | 45 hodnot ARI uvedených v textu zpráv NRL SZÚ 2018–2020 |
| ILI SZÚ / WHO | 1,163 | 136 společných týdnů |

Delší mezery ve WHO datech (léto 2011 a 2014) se nevyplňují; jednotlivý chybějící týden mezi dvěma známými se dopočte geometrickým průměrem a je označen `source: "interpolated"`.

### Opravy oproti dřívějším datům
Při zpětném stažení všech oficiálních PDF (153 týdnů od 2023-W40) se ukázalo, že část dřívějších hodnot neodpovídala SZÚ:
- **2026-W13 až W19** byly jen odhady z krajských dat (KHS Středočeský kraj × koeficient) – nahrazeny oficiálními hodnotami.
- **2025-W26 až W42** pocházely z pracovního Excelu s chybou ve vzorcích (např. W36: 643 místo 468, W37: 811 místo 621).
- **2026-W02 = 1 416** byla omylem zkopírovaná hodnota z 2025-W02 (oficiálně 1 583).
- Týden **2026-W27** SZÚ nezveřejnil (dopočten), **2024-W45** a **2025-W52** jsou z WHO FluID × 1,20.

Každý týden v `data/ari_data.json` má pole `source`: `szu` (oficiální PDF), `who_scaled` (WHO × 1,20) nebo `interpolated`.

## 2. Sezónní průběh (co je v datech vidět)
Medián týdenní změny přes 15 sezón (2009–2025, bez covidových let):

- **W34 → W38:** nemocnost roste **každý rok** (začátek škol), nejprudčeji W36→W38 (+29 % a +31 % týdně).
- **W38 → W41:** růst obvykle pokračuje (v 64–100 % let), už mírněji.
- **W43 → W44:** pravidelný pokles hlášené nemocnosti (podzimní prázdniny + 28. října), po něm návrat.
- **W46 → W49:** další růst k zimě; **W51 → W52 ≈ −42 %** (Vánoce – lidé nechodí k lékaři), pak prudký návrat.
- Chřipková vlna od ledna, **vrchol obvykle W05–W07**, od února pokles až do léta.
- Velikost vrcholu se v některých obdobích **střídá silná / slabší sezóna** (2011/12–2016/17 i po covidu).

## 3. Předpověď

### Zpětné testování
Každý kandidátní model se testoval tak, že „předpovídal" každý týden minulých sezón **jen z dat dostupných k danému týdnu** (rolling origin) na 1–8 týdnů dopředu. Sezóny byly rozdělené:

- **ladicí 2012/13–2018/19** – jen na nich se smělo ladit,
- **kontrolní 2022/23–2025/26** – jen pro nezávislé ověření,
- covidové sezóny 2019/20–2021/22 se nehodnotí (lockdowny, netypický průběh).

Měřítko = medián |chyby| v % pro každý horizont; skóre = průměr přes horizonty 1–8.

### Model
*(viz sekce níže – doplněno po výběru modelu)*

### Intervaly a pravděpodobnosti
Intervaly nejsou „od oka": při každé aktualizaci se model zpětně otestuje na všech historických sezónách a použijí se jeho skutečné chyby z týdnů **ve stejné fázi sezóny** (±6 týdnů od aktuálního týdne). Proto je nejistota v klidném podzimu menší než při nástupu chřipkové vlny. Z rozdělení chyb se počítá:
- **50% a 80% interval** pro každý týden,
- **pravděpodobnost**, že nemocnost bude ≥ 750, ≥ 1 000 a ≥ 1 500/100k.

## 4. Doporučení
Doporučení nezávisí jen na aktuálním pásmu, ale na kombinaci:
1. **úrovně** (pásmo zelené < 750, žluté 750–999, oranžové 1 000–1 499, červené ≥ 1 500),
2. **trendu** – průměrná týdenní změna za poslední 2 týdny (> +5 % = roste, < −5 % = klesá),
3. **předpovědi na 4 týdny od dneška** – roste/klesá o víc než 5 % a pravděpodobnost přechodu do vyššího pásma.

| Pásmo | Roste (nebo předpověď roste) | Stabilní | Klesá (i předpověď) |
|---|---|---|---|
| 🔴 ≥ 1 500 | epidemie – akutní kapacita +20–40 %, triáž | epidemie | epidemie odeznívá – kapacitu zatím držet |
| 🟠 1 000–1 499 | zvýšená a roste – +10–20 %, očkování, triáž | udržet akutní čas | udržet, redukovat až po 2 týdnech pod 1 000 |
| 🟡 750–999 | nástup sezóny – nezkracovat, připravit +10–20 % | udržet | doznívání – pozvolná redukce |
| 🟢 < 750 | klid, ale roste – připravit podzim | klidná sezóna | klidná sezóna |

Zásada: **zkrácení akutního času se nikdy nedoporučí, když nemocnost nebo předpověď roste.** Oranžové pásmo se bere jako „roste" i tehdy, je-li pravděpodobnost epidemické úrovně do 4 týdnů ≥ 25 %; žluté, je-li pravděpodobnost zvýšené zátěže ≥ 50 %.

Svátkové týdny (28. října a podzimní prázdniny, 17. listopadu, Vánoce, Velikonoce) jsou v předpovědi označené – hlášená nemocnost v nich bývá uměle nižší.

## 5. Omezení
- Předpověď je statistický odhad z minulých sezón; neví o nových variantách virů ani o mimořádných opatřeních.
- Horizont 5–8 týdnů je orientační (širší intervaly), zvlášť kolem nástupu chřipkové vlny.
- Hodnoty SZÚ se po zveřejnění zpětně neopravují; dashboard drží první zveřejněnou hodnotu.

## 6. Soubory
| Soubor | Účel |
|---|---|
| `scripts/fetch_ari_data.py` | stažení a parsování PDF SZÚ, aktualizace dat, výpočet předpovědi |
| `scripts/ari_model.py` | produkční předpověď + kalibrované intervaly a pravděpodobnosti |
| `scripts/forecast.py` | ISO týdny a základní sezónní modely |
| `scripts/backtest.py` | zpětné testování modelů (`python3 scripts/backtest.py data/ari_data.json`) |
| `scripts/import_history.py` | jednorázové sestavení historie 2009–2023 (WHO FluID × 1,20) |
| `scripts/test_forecast.py` | testy spouštěné před každou aktualizací |
