# Metodika ARI dashboardu

Dashboard ukazuje týdenní nemocnost akutními respiračními infekcemi (ARI) v ČR na 100 000 obyvatel, předpověď na 10 týdnů (≈ 2 měsíce od dneška) a doporučení pro plánování akutní kapacity ordinace. Tento dokument popisuje, odkud data jsou, jak se počítá předpověď a jak se z ní odvozují doporučení.

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
Každý kandidátní model se testoval tak, že „předpovídal" každý týden minulých sezón **jen z dat dostupných k danému týdnu** (rolling origin) na 1–10 týdnů dopředu. Sezóny byly rozdělené:

- **ladicí 2012/13–2018/19** – jen na nich se smělo ladit,
- **kontrolní 2022/23–2025/26** – jen pro nezávislé ověření,
- covidové sezóny 2019/20–2021/22 se nehodnotí (lockdowny, netypický průběh).

Měřítko = medián |chyby| v % pro každý horizont; skóre = průměr přes horizonty 1–8.

### Proč původní předpověď nefungovala
Dřívější vzorec `0,5 × průměr posledních 4 týdnů + 0,5 × loňský týden × meziroční poměr` nezná sezónní průběh: klouzavý průměr při podzimním nárůstu „táhne" předpověď dolů (z W38 předpověděl na W39 hodnotu 790, skutečnost byla 1 078) a odtud pak vycházelo i chybné doporučení „ke zvážení mírné redukce akutního času".

### Model
Předpověď je **vážený průměr dvou modelů v logaritmu**; oba se při každé aktualizaci počítají jen z dat do posledního zveřejněného týdne.

**1. Analogové sezóny** (`scripts/model_analog.py`) – „jak to pokračovalo v minulých letech, které vypadaly podobně":
- letošních posledních 6 týdnů se porovná se stejnými kalendářními týdny všech minulých sezón (2009/10–2025/26 bez covidu). Týdny se párují **podle data** (týden obsahující stejné datum jako letošní čtvrtek), takže Vánoce, podzimní prázdniny i týden 53 sedí na sebe;
- každá minulá sezóna pak „promítne" letošní úroveň dopředu svou vlastní křivkou. Sezóny s podobnějším tvarem posledních týdnů (novější týdny váží víc) mají větší váhu;
- rozdíl úrovní letos vs. minulá sezóna se z aktuální hodnoty postupně (×0,8 za týden) vrací k průměrnému rozdílu za poslední 2 roky – to zachytí vyšší úroveň po covidu i střídání silnějších a slabších sezón.

**2. Sezónní profil se svátky** (`scripts/model_profile_holiday.py`) – „navázat na aktuální úroveň a pokračovat typickým týdenním tempem":
- z historie se odhadne, o kolik % hlášená nemocnost klesá ve svátkových týdnech (podle **data**, ne čísla týdne): 28. září, 28. října + podzimní prázdniny, 17. listopadu, Vánoce, Nový rok, Velký pátek, Velikonoční pondělí, 1. a 8. května, 5.–6. července. U podzimních prázdnin, Vánoc a Velikonoc se od sezóny 2024/25 berou jen novější roky – propady jsou od zavedení automatizovaného hlášení hlubší (W44 −25 až −30 % místo −10 až −15 %);
- řada se o svátky očistí, z minulých sezón se spočítá **typická týdenní změna pro každý týden roku** (medián, ±1 týden) a ta se přičítá k aktuální očištěné úrovni; do cílových svátkových týdnů se propad zase vrátí.

**Váhy:** profil je přesnější na 1–3 týdny, analogy na delší horizont. Váha analogů je 0,4 / 0,5 / 0,6 / 0,7 / 0,8 / 0,9 pro horizont 1–6 týdnů a 1 od 7. týdne (zvoleno jen na ladicích sezónách).

**Trend a sezónnost:** pokračování růstu nebo poklesu model nebere jako „setrvačnost posledního týdne", ale z toho, jak se v daném období roku vyvíjely minulé sezóny s podobným průběhem. Zkoušená explicitní hybnost (letošní tempo nad rámec profilu) předpověď v testu zhoršila, proto se nepoužívá. Zima vysoko / léto nízko a podzimní nárůst W34–W41 vycházejí přímo z dat.

**Výsledky zpětného testu** (medián |chyby| v %, průměr přes horizont 1–8 týdnů):

| Model | ladicí 2012–2018 | kontrolní 2022–2025 | z toho podzim | správný směr (h 1–4) |
|---|---|---|---|---|
| původní (MA4 + loňský rok) | 21,3 | 17,9 | 21,3 | 64–68 % |
| loňský týden × poměr | 13,5 | 16,7 | | |
| sezónní profil (bez svátků) | 11,4 | 9,5 | | |
| klimatologie + odchylka | 8,9 | 10,6 | | |
| sezónní profil se svátky | 8,5 | 8,8 | 10,8 | 89–93 % |
| analogové sezóny | 6,5 | 8,5 | | 90–93 % |
| **použitý vážený průměr** | **6,3** | **8,1** | **9,6** | **90–95 %** |

Kontrolní sezóny podle horizontu (použitý model vs. původní): 1 týden 4,3 % vs. 7,0 %, 2 týdny 6,1 vs. 11,1, 4 týdny 7,6 vs. 15,8, 8 týdnů 10,9 vs. 28,2, 10 týdnů 11,3 vs. 31,9. Nejtěžší je zima (nástup a vrchol chřipky): 9,5 % vs. 21,2 %.

Testy (`scripts/test_forecast.py`) navíc hlídají, že předpověď z W39/2026 roste do W41–W43 (v žádné sezóně 2009–2025 mimo covid nebyla hodnota ve W41–W43 nižší než ve W38/W39, s výjimkou svátkových týdnů), že W40 a W44 mají svátkový propad a že skutečná hodnota W39 leží v 80% intervalu předpovědi z W38.

### Intervaly a pravděpodobnosti
Intervaly nejsou „od oka": při každé aktualizaci se model zpětně otestuje na všech historických sezónách a použijí se jeho skutečné chyby z týdnů **ve stejné fázi sezóny** (±6 týdnů od aktuálního týdne). Proto je nejistota v klidném podzimu menší než při nástupu chřipkové vlny. Z rozdělení chyb se počítá:
- **50% a 80% interval** pro každý týden,
- **pravděpodobnost**, že nemocnost bude ≥ 750, ≥ 1 000 a ≥ 1 500/100k.

## 4. Doporučení
Doporučení vychází ze čtyř vstupů:

1. **Plánovaná hodnota** = vyšší z (poslední hodnota, **75. percentil předpovědi za 2 týdny od dneška**). Plánuje se na horní odhad, protože nedostatek akutní kapacity je pro ordinaci zhruba 3× horší než přebytek (přetížení, odmítnutí pacienti, přesčasy). Z ní se určí pásmo: 🟢 < 750, 🟡 750–999, 🟠 1 000–1 499, 🔴 ≥ 1 500 (SZÚ vyhlašuje epidemii obvykle nad 1 600–1 700).
2. **Trend** – týdenní log-růst z dvoutýdenních součtů `g = ½·ln((x₀+x₁)/(x₂+x₃))`: ≥ +10 % rychle roste, ≥ +3 % roste, ±3 % stabilní; **pokles se uzná jen tehdy, když hodnota klesla dva týdny po sobě** (jednorázový propad, typicky svátek, se nebere jako ústup). Za „roste" se bere i předpověď za 4 týdny o > 5 % výš.
3. **Fáze sezóny** – léto W22–W34, podzim W35–W47, zima před vrcholem, zima po vrcholu (vrchol ≥ 1 000 před ≥ 3 týdny, hodnota ≤ 85 % vrcholu a klesá), jaro W11–W21.
4. **Signál chřipky** – ILI ≥ 25/100k a nárůst ILI ×1,5 za 2 týdny (ILI předbíhá nástup chřipkové vlny o 1–3 týdny). **Svátkový týden** – poslední data ovlivněná volnými dny.

| Situace | Kdy (pásmo = pásmo plánované hodnoty) |
|---|---|
| S1 Klid | 🟢, nic neroste |
| S2 Nástup podzimu | 🟢 na podzim (nebo od W33), roste nebo P(🟡 do 4 týdnů) ≥ 30 % |
| S3 Rychle přibývá | 🟡 na podzim / v zimě před vrcholem, roste nebo P(🟠 do 4 týdnů) ≥ 50 % |
| S4 Vysoká a roste | 🟠 na podzim, roste |
| S5 Plató | 🟠 na podzim bez růstu; 🟡 bez růstu a bez potvrzeného poklesu |
| S6 Nástup chřipky | signál ILI (mimo jaro a zimu po vrcholu) – má přednost |
| S7 Blíží se epidemie | 🟠 v zimě před vrcholem, roste nebo P(🔴 do 4 týdnů) ≥ 30 % |
| S8 Zvýšená, stabilní | 🟠 v zimě před vrcholem bez růstu |
| S8j Po vrcholu | 🟠 po vrcholu / na jaře, 🟡 po vrcholu / na jaře – bez potvrzeného poklesu |
| S9 Epidemie | 🔴 |
| S10 Za vrcholem | 🔴 nebo 🟠 v zimě po vrcholu s potvrzeným poklesem |
| S11 Ústup | potvrzený pokles: 🟠 na jaře, 🟡 mimo podzim a zimu před vrcholem, 🟢 na jaře |
| S12 Svátky | poslední týden ovlivněný svátky a nic neroste |
| S14 Mimo sezónu | léto: rychlý růst, nebo růst nad zeleným pásmem |

**Stupeň akutní kapacity** se odvozuje z pásma plánované hodnoty: 🟢 základ (100 %), 🟡 připravenost (100–120 % + připravená rezerva), 🟠 navýšeno (130–150 %), 🔴 vysoce navýšeno (150–170 %), 🔴 s růstem nebo v zimě před vrcholem zimní krizový režim (170–200 %); při signálu chřipky aspoň „vysoce navýšeno".

**Ochranná pravidla (asymetrie):**
- Navyšuje se hned, **snižuje se jen při potvrzeném poklesu** a jen na jaře, v zimě po vrcholu nebo v létě – nikdy na podzim, v zimě před vrcholem ani ve svátkovém týdnu. Bez potvrzeného poklesu se stupeň nesníží pod úroveň odpovídající poslednímu ze dvou týdnů.
- Na podzim je minimum stupeň „připravenost".
- **Zkrácení akutního času se nikdy nedoporučí, když nemocnost nebo předpověď roste.**

**Odhad akutní kapacity v %** (orientační, pro ordinaci pro dospělé): respirační poptávka dospělých se počítá z věkových skupin SZÚ (index dospělých = 0,114·ARI₁₅₋₂₄ + 0,636·ARI₂₅₋₆₄ + 0,25·ARI₆₅₊, váhy podle počtu obyvatel) jako násobek `M` letního mediánu (W27–W34), přepočtený na plánovanou hodnotu. Kapacita = `(1 − s) + s·M`, kde `s` = podíl respiračních pacientů na akutních kontaktech v létě (25–45 %); výsledek je rozsah, zaokrouhlený na 5 %, nejméně 100 %.

Svátkové týdny (28. září, 28. října a podzimní prázdniny, 17. listopadu, Vánoce, Velikonoce, květnové a červencové svátky) jsou v předpovědi označené – hlášená nemocnost v nich bývá uměle nižší (W44 typicky −15 až −30 %, W52 kolem −40 %), po nich přichází skokový návrat.

## 5. Omezení
- Předpověď je statistický odhad z minulých sezón; neví o nových variantách virů ani o mimořádných opatřeních.
- Horizont 5–10 týdnů je orientační (širší intervaly), zvlášť kolem nástupu chřipkové vlny.
- SZÚ hodnotu předchozího týdne občas mírně opraví (např. 2025-W38: v reportu 851, podle změny v následujícím reportu ~890); dashboard drží hodnotu z reportu za daný týden.

## 6. Soubory
| Soubor | Účel |
|---|---|
| `scripts/fetch_ari_data.py` | stažení a parsování PDF SZÚ, aktualizace dat, výpočet předpovědi |
| `scripts/ari_model.py` | produkční předpověď + kalibrované intervaly a pravděpodobnosti |
| `scripts/model_analog.py` | model analogových sezón |
| `scripts/model_profile_holiday.py` | sezónní profil se svátkovými efekty (typy svátků podle data) |
| `scripts/forecast.py` | ISO týdny a základní sezónní modely |
| `scripts/backtest.py` | zpětné testování modelů (`python3 scripts/backtest.py data/ari_data.json --candidate cesta/k/modelu.py --phase`) |
| `scripts/import_history.py` | jednorázové sestavení historie 2009–2023 (WHO FluID × 1,20) |
| `scripts/test_forecast.py` | testy spouštěné před každou aktualizací |
