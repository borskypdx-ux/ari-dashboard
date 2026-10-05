# Metodika ARI dashboardu

Dashboard ukazuje týdenní nemocnost akutními respiračními infekcemi (ARI) v ČR na 100 000 obyvatel, předpověď na 10 týdnů (≈ 2 měsíce od dneška) a doporučení pro plánování akutní kapacity ordinace. Tento dokument popisuje, odkud data jsou, jak se počítá předpověď a jak se z ní odvozují doporučení.

## 1. Data

### Zdroj aktuálních dat – SZÚ
Státní zdravotní ústav zveřejňuje každý týden PDF report na [datové stránce ARI/chřipka](https://szu.gov.cz/publikace-szu/data/akutni-respiracni-infekce-chripka/). Report obsahuje tabulku *Relativní nemocnost na 100000 obyvatel* a národní řádek *Česká republika – the Czech Republic* (věkové skupiny 0–5, 6–14, 15–24, 25–64, 65+ a **Celkem**). Dashboard bere sloupec **Celkem** pro ARI i ILI.

- **Týden a rok se berou z hlavičky PDF** („39 týden 2026"), ne z cesty souboru – report za W52 bývá nahrán až v lednu (`/2026/01/52_tyden.pdf`) a podle cesty by se přiřadil ke špatnému roku.
- Datová stránka drží zhruba poslední rok reportů; každý běh doplní všechny týdny, které ještě nemají oficiální hodnotu (dřívější verze doplňovala jen týdny po posledním známém, takže jednou vynechaný týden už nikdy nedoplnila).
- SZÚ zveřejňuje report za týden v průběhu následujícího týdne. Kontrola proto běží dvakrát denně a data jsou obvykle 1–2 týdny „pozadu" za kalendářem; dashboard to přiznává (týdny označené *proběhlo · čeká na data*).
- **Kontrola načtených hodnot:** národní řádek musí mít přesně 6 čísel, Celkem musí ležet mezi hodnotami věkových skupin (je to jejich vážený průměr), ARI musí být výrazně vyšší než ILI a změna proti předchozímu týdnu v rozmezí ×0,4–×2,5. Report, který kontrolou neprojde, se neuloží (raději chybějící týden než špatné číslo).
- **Opravy SZÚ:** SZÚ občas zpětně opraví předchozí týden (pozdní hlášení); oprava je vidět jen v řádku *Změna [%]* následujícího reportu. Liší-li se z něj odvozená hodnota o víc než 2 %, dashboard předchozí týden opraví (i věkové skupiny) a označí poznámkou.
- **Upozornění na poruchu:** když se datovou stránku nepodaří načíst, některý report nejde přečíst nebo jsou poslední data starší než 4 týdny, automatický běh uloží, co se podařilo, ale skončí chybou – GitHub pak pošle správci e-mail. Stránka v takovém případě hlásí „SZÚ zatím nezveřejnil novější report, nebo selhala automatická aktualizace".

### Historie 2009–2023 – WHO FluID
Pro sezónní srovnání a pro předpovědní model je potřeba víc sezón. Starší data jsou z databáze [WHO FluID](https://www.who.int/tools/flunet) (API `xmart-api-public.who.int/FLUMART/VIW_FID_EPI`, země CZE): týdenní `ARI_CASE / ARI_POP_COV` sečtené přes věkové skupiny 0–4, 5–14, 15–64 a 65+.

WHO a SZÚ počítají nemocnost s jinou pokrytou populací, proto se hodnoty WHO přepočítávají na měřítko SZÚ:

| Kalibrace | Hodnota | Podklad |
|---|---|---|
| ARI SZÚ / WHO | **1,20** | medián poměru ve 136 společných týdnech 2023–2026, rozptyl ~5 %, stejný v létě, na podzim, v zimě i na jaře |
| nezávislá kontrola | 1,18 | 45 hodnot ARI uvedených v textu zpráv NRL SZÚ 2018–2020 |
| ILI SZÚ / WHO | 1,163 | 136 společných týdnů |

Delší mezery ve WHO datech (léto 2011 a 2014) se nevyplňují; jednotlivý chybějící týden mezi dvěma známými se dopočte geometrickým průměrem a je označen `source: "interpolated"`. **Svátkové týdny se nedopočítávají** – průměr sousedů by vánoční propad přecenil o 40–60 %.

### Opravy oproti dřívějším datům
Při zpětném stažení všech oficiálních PDF (153 týdnů od 2023-W40) se ukázalo, že část dřívějších hodnot neodpovídala SZÚ:
- **2026-W13 až W19** byly jen odhady z krajských dat (KHS Středočeský kraj × koeficient) – nahrazeny oficiálními hodnotami.
- **2025-W26 až W42** pocházely z pracovního Excelu s chybou ve vzorcích (např. W36: 643 místo 468, W37: 811 místo 621).
- **2026-W02 = 1 416** byla omylem zkopírovaná hodnota z 2025-W02 (oficiálně 1 583).
- **2023-W39** je oficiální hodnota SZÚ 709 (dřív WHO × 1,20 = 670); **2025-W38** je podle opravy v reportu za W39 890 (původně zveřejněno 851); dopočtený svátkový týden **2009-W53** byl odstraněn.
- Týden **2026-W27** SZÚ nezveřejnil (dopočten), **2024-W45** a **2025-W52** jsou z WHO FluID × 1,20.

Každý týden v `data/ari_data.json` má pole `source`: `szu` (oficiální PDF), `who_scaled` (WHO × 1,20) nebo `interpolated`; svátkové týdny mají navíc pole `holiday` (popis svátku).

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
- každá minulá sezóna pak „promítne" letošní úroveň dopředu svou vlastní křivkou. Sezóny s podobnějším tvarem posledních týdnů mají mírně větší váhu (v praxi jsou váhy téměř stejné – model je hlavně kalendářně zarovnaný průměr minulých sezón);
- rozdíl úrovní letos vs. minulá sezóna se z aktuální hodnoty postupně (×0,8 za týden) vrací k průměrnému rozdílu za poslední 2 roky – to zachytí vyšší úroveň po covidu i střídání silnějších a slabších sezón;
- sezóny se porovnávají na řadě **očištěné o svátky** (stejné svátkové efekty jako v modelu 2) a do cílových svátkových týdnů se propad vrátí – svátek tak sedí na svátek bez ohledu na to, na jaký den v týdnu připadne;
- chybí-li několik posledních týdnů dat, model pokračuje s průměrným posunem úrovní (nespadne do „ploché" předpovědi).

**2. Sezónní profil se svátky** (`scripts/model_profile_holiday.py`) – „navázat na aktuální úroveň a pokračovat typickým týdenním tempem":
- z historie se odhadne, o kolik % hlášená nemocnost klesá ve svátkových týdnech. Svátkový týden se určuje **podle data a dne v týdnu** (svátek o víkendu se nepočítá):
  - *Vánoce* = týden, ve kterém je 27. 12. (24.–26. 12. a „mezi svátky"); když 25. 12. připadne na víkend, hlavní propad je až v týdnu po něm (ověřeno na letech 2010, 2011, 2016, 2021, 2022),
  - *Nový rok* = týden s 1. 1. ve všední den, je-li to jiný týden,
  - *28. října a podzimní prázdniny* = 28. 10. ve všední den (připadne-li na víkend, propad v datech vidět není),
  - Velký pátek, Velikonoční pondělí, 1. a 8. května, 28. září, 17. listopadu, 5. a 6. července ve všední den, Štědrý den ve všední den mimo vánoční týden;
  
  u podzimních prázdnin, Vánoc a Velikonoc se od sezóny 2024/25 berou jen novější roky – propady jsou od zavedení automatizovaného hlášení hlubší (W44 −25 až −30 % místo −10 až −15 %);
- řada se o svátky očistí, z minulých sezón se spočítá **typická týdenní změna pro každý týden roku** (medián, ±1 týden) a ta se přičítá k aktuální očištěné úrovni; do cílových svátkových týdnů se propad zase vrátí.

**Váhy:** profil je přesnější na 1–3 týdny, analogy na delší horizont. Váha analogů je 0,4 / 0,5 / 0,6 / 0,7 / 0,8 / 0,9 pro horizont 1–6 týdnů a 1 od 7. týdne (zvoleno jen na ladicích sezónách).

**Trend a sezónnost:** pokračování růstu nebo poklesu model nebere jako „setrvačnost posledního týdne", ale z toho, jak se v daném období roku vyvíjely minulé sezóny s podobným průběhem. Zkoušená explicitní hybnost (letošní tempo nad rámec profilu) předpověď v testu zhoršila, proto se nepoužívá. Zima vysoko / léto nízko a podzimní nárůst W34–W41 vycházejí přímo z dat.

**Výsledky zpětného testu** (medián |chyby| v %, průměr přes horizont 1–8 týdnů):

| Model | ladicí 2012–2018 | kontrolní 2022–2025 | z toho podzim | správný směr (h 1–4) |
|---|---|---|---|---|
| původní (MA4 + loňský rok) | 21,3 | 18,0 | 21,6 | 64–68 % |
| loňský týden × poměr | 13,5 | 16,8 | | |
| sezónní profil (bez svátků) | 11,5 | 9,4 | | |
| klimatologie + odchylka | 8,9 | 10,6 | | |
| sezónní profil se svátky | 8,5 | 8,9 | 10,8 | 90–93 % |
| analogové sezóny (očištěné o svátky) | 6,4 | 8,0 | 9,6 | 92–94 % |
| **použitý vážený průměr** | **6,4** | **7,7** | **9,2** | **92–94 %** |

Kontrolní sezóny podle horizontu (použitý model vs. původní): 1 týden 4,5 % vs. 7,0 %, 2 týdny 5,7 vs. 11,1, 4 týdny 7,5 vs. 16,0, 8 týdnů 10,3 vs. 28,2, 10 týdnů 11,2 vs. 31,7. Zima (nástup a vrchol chřipky) 9,2 % vs. 21,2 %. Dashboard v tabulce „Jak přesná je předpověď" ukazuje právě čísla z kontrolních sezón (nepoužitých k ladění).

**Známé slabiny:** model nepozná předem netypicky časný vrchol (2022/23 s vrcholem v prosinci model z W51 nadhodnotil o desítky %) a velké chřipkové vrcholy spíš podhodnocuje (o 15–20 % na 2–4 týdny). Proto se doporučení plánují na horní odhad (75. percentil) a sledují se i ILI.

Testy (`scripts/test_forecast.py`) navíc hlídají, že předpověď z W39/2026 roste do W41–W43 (v žádné sezóně 2009–2025 mimo covid nebyla hodnota ve W41–W43 nižší než ve W38/W39, s výjimkou svátkových týdnů), že W40 a W44 mají svátkový propad a že skutečná hodnota W39 leží v 80% intervalu předpovědi z W38.

### Intervaly a pravděpodobnosti
Intervaly nejsou „od oka": při každé aktualizaci se model zpětně otestuje na všech historických sezónách a použijí se jeho skutečné chyby z týdnů **ve stejné fázi sezóny** (±6 týdnů od aktuálního týdne). Proto je nejistota v klidném podzimu menší než při nástupu chřipkové vlny. Chyby z pocovidových sezón (od 2022/23) mají trojnásobnou váhu a rozptyl se rozšiřuje ×1,15: samotné předcovidové chyby byly na dnešní dobu příliš „optimistické“ (80% interval v sezónách 2022–2025 pokryl jen 72 % případů, po úpravě 77 %, v sezónách 2024–2025 81 %; ověřeno v reálném čase, tj. jen z chyb známých k danému týdnu). Z rozdělení chyb se počítá:
- **50% a 80% interval** pro každý týden,
- **pravděpodobnost**, že nemocnost bude ≥ 750, ≥ 1 000 a ≥ 1 500/100k.

## 4. Doporučení
Doporučení vychází ze čtyř vstupů:

1. **Plánovaná hodnota** = vyšší z (poslední hodnota, **75. percentil předpovědi za 2 týdny od dneška**). Plánuje se na horní odhad, protože nedostatek akutní kapacity je pro ordinaci zhruba 3× horší než přebytek (přetížení, odmítnutí pacienti, přesčasy). Z ní se určí pásmo: 🟢 < 750, 🟡 750–999, 🟠 1 000–1 499, 🔴 ≥ 1 500. Pásma jsou orientační hranice dashboardu, ne oficiální prahy: za epidemii SZÚ obvykle považuje nemocnost kolem 1 600–1 700/100k a posuzuje i laboratorní data.
2. **Trend** – týdenní log-růst z dvoutýdenních součtů posledních 4 **nesvátkových** týdnů `g = ½·ln((x₀+x₁)/(x₂+x₃))`: ≥ +10 % rychle roste, ≥ +3 % roste, ±3 % stabilní; **pokles se uzná jen tehdy, když hodnota klesla dva týdny po sobě** (svátkový propad se do trendu nepočítá vůbec). Za „roste" se bere i předpověď: průměr příštích 1–4 týdnů od dneška (bez svátkových týdnů) o > 5 % nad poslední hodnotou.
3. **Fáze sezóny** – léto W22–W34 (od W33 se bere jako začátek podzimu), podzim W35–W47, zima před vrcholem, zima po vrcholu (vrchol ≥ 1 000 před ≥ 3 týdny, hodnota ≤ 85 % vrcholu a klesá), jaro W11–W21 (pozdní vlna, která v W11–W16 ještě roste, se bere jako zima před vrcholem).
4. **Signál chřipky** – ILI ≥ 25/100k a nárůst ILI ×1,5 za 2 týdny (ILI předbíhá nástup chřipkové vlny o 1–3 týdny). **Svátkový týden** – poslední data ovlivněná volnými dny.

| Situace | Kdy (pásmo = pásmo plánované hodnoty) |
|---|---|
| S1 Klid | 🟢, nic neroste; 🟡 v létě bez růstu i bez poklesu |
| S2 Nástup podzimu | 🟢 na podzim (nebo od W33), roste nebo P(🟡 do 4 týdnů) ≥ 30 % |
| S3 Rychle přibývá | 🟡 na podzim (od W33) / v zimě před vrcholem, roste nebo P(🟠 do 4 týdnů) ≥ 50 % |
| S4 Vysoká a roste | 🟠 na podzim (od W33), roste |
| S5 Plató | 🟠 na podzim bez růstu; 🟡 na podzim / v zimě před vrcholem bez růstu |
| S6 Nástup chřipky | signál ILI pod epidemickou úrovní (mimo jaro a zimu po vrcholu) – má přednost |
| S7 Blíží se epidemie | 🟠 v zimě před vrcholem, roste nebo P(🔴 do 4 týdnů) ≥ 30 % |
| S8 Zvýšená, stabilní | 🟠 v zimě před vrcholem bez růstu |
| S8j Po vrcholu | 🟠/🟡 po vrcholu nebo na jaře bez potvrzeného poklesu |
| S9 Epidemie | 🔴 a poslední hodnota ≥ 1 500 |
| S9a Očekává se epidemie | 🔴 jen podle horního odhadu předpovědi (poslední hodnota < 1 500) |
| S10 Za vrcholem | 🔴 po vrcholu bez růstu; 🟠 v zimě po vrcholu s potvrzeným poklesem |
| S11 Ústup | potvrzený pokles mimo podzim a zimu před vrcholem; 🟡 v létě s klesající předpovědí |
| S12 Svátky | poslední týden je svátkový a nic neroste ani potvrzeně neklesá |
| S14 Mimo sezónu | léto do W32: pozorovaný rychlý růst, nebo růst nad zeleným pásmem |

**Stupeň akutní kapacity** se odvozuje z pásma plánované hodnoty: 🟢 základ, 🟡 připravenost (připravená rezerva slotů), 🟠 navýšeno, 🔴 vysoce navýšeno; zimní krizový režim jen při skutečné epidemické úrovni (poslední hodnota ≥ 1 500) s růstem nebo v zimě před vrcholem. Při signálu chřipky nebo epidemii aspoň „vysoce navýšeno". Stupeň je slovní popis; konkrétní % je samostatný odhad (níže).

**Ochranná pravidla (asymetrie):**
- Navyšuje se hned, **snižuje se jen při potvrzeném poklesu** a jen na jaře, v zimě po vrcholu nebo v létě do W32 – nikdy na podzim ani v zimě před vrcholem.
- Bez potvrzeného poklesu se stupeň ani % **nesníží pod doporučení předchozího týdne**. Dashboard k tomu z předpovědí uložených za posledních 8 týdnů (`forecast_past`) dopočítá tehdejší doporučení a drží nejvyšší z nich, dokud pokles nepotvrdí data. Simulace všech týdnů 2024-W20 – 2026-W39 (data k danému týdnu, předpověď z tehdy dostupných dat, zobrazeno o 2 týdny později) nenašla jediný týden, kdy by doporučení kleslo při rostoucí nemocnosti nebo předpovědi.
- Na podzim je minimum stupeň „připravenost".
- **Zkrácení akutního času se nikdy nedoporučí, když nemocnost nebo předpověď roste.**

**Odhad akutní kapacity v %** (orientační, pro ordinaci pro dospělé): `M` = plánovaná hodnota ÷ letní medián (W27–W34 posledních 3 let) × podíl dospělých na růstu. Podíl dospělých = (index dospělých ÷ jeho letní medián) ÷ (celková ARI ÷ její letní medián), vyšší ze dvou posledních nesvátkových týdnů, omezený na 0,5–1,5; index dospělých = 0,114·ARI₁₅₋₂₄ + 0,636·ARI₂₅₋₆₄ + 0,25·ARI₆₅₊ (váhy podle počtu obyvatel). Kapacita = `(1 − s) + s·M`, kde `s` = podíl respiračních pacientů na akutních kontaktech v létě (25–45 %); výsledek je rozsah, zaokrouhlený na 5 %, nejméně 100 %.

Svátkové týdny (28. září, 28. října a podzimní prázdniny, 17. listopadu, Vánoce, Velikonoce, květnové a červencové svátky) jsou v předpovědi označené – hlášená nemocnost v nich bývá uměle nižší (W44 typicky −15 až −30 %, W52 kolem −40 %), po nich přichází skokový návrat.

## 5. Omezení
- Předpověď je statistický odhad z minulých sezón; neví o nových variantách virů ani o mimořádných opatřeních.
- Horizont 5–10 týdnů je orientační (širší intervaly), zvlášť kolem nástupu chřipkové vlny.
- Pásma a odhad % kapacity jsou orientační pomůcka pro plánování, ne oficiální prahy ani normativ.
- Starší historie (WHO × 1,20) se od úrovně SZÚ typicky liší o několik procent (v jednotlivých týdnech až ~15 %); tvar sezón (týdenní změny) obě řady sledují velmi přesně.

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
