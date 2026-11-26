# Rendiconto turni GAIA CRI

Ogni 3 ore GitHub entra in GAIA con il tuo account e crea una pagina web protetta da password con:

- **Riepilogo**: turni effettuati, monte ore per anno (ore GAIA e ore reali), monte ore ufficiale GAIA, ore per attività, turni con durata sospetta
- **Effettuati**, **Da fare**, **In sospeso** (dallo storico delle richieste)
- **Disponibili**: turni del calendario, con quelli scoperti in evidenza
- **Corsi**: corsi aperti, prima quelli del tuo comitato, poi quelli della regione

## Secrets da impostare (Settings → Secrets and variables → Actions)

| Nome | Cosa contiene |
|---|---|
| `GAIA_EMAIL` | email di accesso a GAIA |
| `GAIA_PASSWORD` | password di GAIA |
| `PASSWORD_PAGINA` | password a tua scelta per aprire la pagina |

Le credenziali non sono mai scritte nel codice. La pagina pubblicata è cifrata (AES-256): senza `PASSWORD_PAGINA` si vede solo la schermata di accesso.

## Impostazioni facoltative

Nel file `.github/workflows/rendiconto.yml`, sotto `env:`, puoi aggiungere:
`COMITATO` (predefinito `Casarano`), `REGIONE` (`puglia`), `SETTIMANE_CALENDARIO` (`4`), `TETTO_ORE_TURNO` (`12`).
