# ERR "Kuuldemängud" Podcasti RSS Generaator & Automaatne Uuendus

Tegemist on automaatse süsteemiga, mis kraabib ERR-i Arhiivi API-st kõik **"Kuuldemängud"** (Raadioteatri) episoodid ja andmed, genereerib neist standardse ja täielikult valideeruva RSS 2.0 (XML) sööda ning avaldab selle GitHub Pagesi kaudu.

## Otselink Pocket Castsi jaoks
👉 **`https://reemet16.github.io/err-podcasts/feed_kuuldemang.xml`**

## Omadused

- **Täielik arhiiv (2099 osa)**: Pagineerib läbi kogu ERR-i arhiivi ja laeb alla kõik registreeritud kuuldemängud (1941–2026).
- **Automaatne uuendus (GitHub Actions)**: Iga päev kell 03:00 UTC käivitub automaatne töövoog, mis kontrollib uute kuuldemängude lisandumist ja uuendab RSS-söödet.
- **Tõhus puhverdamine (`kuuldemang_cache.json`)**: Salvestab helifailide suurused ja tüübid puhvrisse, et skripti käivitus kesta vaid paari sekundi.
- **Otseviited meediafailidele**: Igal episoodil on korrektselt seadistatud `<enclosure>` silt koos pikkuse (`length`) ja meediatüübiga (`audio/x-m4a`), mis ühildub Pocket Castsi ja teiste mängijatega.

## Projekti failid

- `scrape_kuuldemang.py` - Kraapija ja RSS generaatori Pythoni skript.
- `kuuldemang_cache.json` - Tuvastatud meediafailide andmete puhver.
- `feed_kuuldemang.xml` - Genereeritud podcasti RSS fail.
- `.github/workflows/update_rss.yml` - GitHub Actions automaatse uuendamise töövoog.
