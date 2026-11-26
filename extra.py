"""
Funzioni aggiuntive del rendiconto:
- meteo (Open-Meteo, gratuito) e ora di partenza per i turni da fare
- lista "cosa portare" per tipo di servizio
- grafici (SVG) e resoconto ore stampabile
"""

import html
import json
import os
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("Europe/Rome")

# Sede CRI di Casarano (partenza dei servizi)
SEDE_LAT = float(os.environ.get("SEDE_LAT", "40.014198"))
SEDE_LON = float(os.environ.get("SEDE_LON", "18.174528"))
VIAGGIO_MINUTI = int(os.environ.get("VIAGGIO_MINUTI", "15"))   # da casa (Matino) alla sede
ANTICIPO_MINUTI = int(os.environ.get("ANTICIPO_MINUTI", "15"))  # arrivo in sede prima dell'inizio

MESI_BREVI = ["", "Gen", "Feb", "Mar", "Apr", "Mag", "Giu", "Lug", "Ago", "Set", "Ott", "Nov", "Dic"]


def e(x):
    return html.escape(str(x if x is not None else ""))


def f_num(x, dec=1):
    return f"{x:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ------------------------------------------------------------------ meteo
CODICI_METEO = {
    0: ("☀️", "sereno"), 1: ("🌤️", "poco nuvoloso"), 2: ("⛅", "parz. nuvoloso"), 3: ("☁️", "nuvoloso"),
    45: ("🌫️", "nebbia"), 48: ("🌫️", "nebbia"),
    51: ("🌦️", "pioviggine"), 53: ("🌦️", "pioviggine"), 55: ("🌦️", "pioviggine"),
    56: ("🌧️", "pioggia gelata"), 57: ("🌧️", "pioggia gelata"),
    61: ("🌧️", "pioggia debole"), 63: ("🌧️", "pioggia"), 65: ("🌧️", "pioggia forte"),
    66: ("🌧️", "pioggia gelata"), 67: ("🌧️", "pioggia gelata"),
    71: ("🌨️", "neve"), 73: ("🌨️", "neve"), 75: ("🌨️", "neve forte"), 77: ("🌨️", "neve"),
    80: ("🌦️", "rovesci"), 81: ("🌧️", "rovesci"), 82: ("⛈️", "rovesci forti"),
    85: ("🌨️", "neve"), 86: ("🌨️", "neve"), 95: ("⛈️", "temporale"), 96: ("⛈️", "temporale"), 99: ("⛈️", "temporale"),
}


def scarica_meteo(file_prova=None):
    """Previsioni orarie per 16 giorni sulla sede. Ritorna {datetime_ora: dati} oppure {}."""
    try:
        if file_prova:
            dati = json.loads(Path(file_prova).read_text(encoding="utf-8"))
        else:
            url = ("https://api.open-meteo.com/v1/forecast"
                   f"?latitude={SEDE_LAT}&longitude={SEDE_LON}"
                   "&hourly=temperature_2m,precipitation_probability,weather_code,wind_speed_10m"
                   "&timezone=Europe%2FRome&forecast_days=16")
            with urllib.request.urlopen(url, timeout=20) as r:
                dati = json.loads(r.read())
        h = dati["hourly"]
        meteo = {}
        for i, t in enumerate(h["time"]):
            ora = datetime.fromisoformat(t).replace(tzinfo=FUSO)
            meteo[ora] = {"temp": h["temperature_2m"][i], "pioggia": h["precipitation_probability"][i] or 0,
                          "codice": h["weather_code"][i], "vento": h["wind_speed_10m"][i]}
        return meteo
    except Exception:
        print("Avviso: previsioni meteo non disponibili.")
        return {}


def meteo_turno(t, meteo):
    """Riassunto del meteo nelle ore del turno; None se fuori dalle previsioni."""
    ora = t["inizio"].replace(minute=0, second=0, microsecond=0)
    ore = []
    while ora < t["fine"] and len(ore) < 24:
        if ora in meteo:
            ore.append(meteo[ora])
        ora += timedelta(hours=1)
    if not ore:
        return None
    peggiore = max(ore, key=lambda o: (o["codice"] or 0))
    icona, descr = CODICI_METEO.get(peggiore["codice"], ("🌡️", ""))
    return {
        "icona": icona, "descrizione": descr,
        "min": min(o["temp"] for o in ore), "max": max(o["temp"] for o in ore),
        "pioggia": max(o["pioggia"] for o in ore), "vento": max(o["vento"] for o in ore),
    }


def testo_meteo(m):
    if not m:
        return ""
    temp = (f"{round(m['min'])}°" if round(m["min"]) == round(m["max"])
            else f"{round(m['min'])}–{round(m['max'])}°")
    parti = [f"{m['icona']} {temp}"]
    piove = any(x in m["descrizione"] for x in ("pioggia", "pioviggine", "rovesci", "temporale", "neve"))
    if m["descrizione"]:
        parti.append(m["descrizione"] + (f" ({m['pioggia']}%)" if piove and m["pioggia"] >= 30 else ""))
    if m["pioggia"] >= 30 and not piove:
        parti.append(f"pioggia {m['pioggia']}%")
    if m["vento"] >= 30:
        parti.append(f"vento {round(m['vento'])} km/h")
    return ", ".join(parti)


def ora_partenza(t):
    return t["inizio"] - timedelta(minutes=VIAGGIO_MINUTI + ANTICIPO_MINUTI)


# ------------------------------------------------------------------ cosa portare
COSA_PORTARE_PREDEFINITO = {
    "sempre": ["Divisa completa", "Tesserino CRI", "Scarpe antinfortunistiche", "Guanti monouso"],
    "regole": [
        {"parole": ["stadio", "partita", "casarano -", "calcio", "lecce -"],
         "aggiungi": ["Giubbino alta visibilità", "Telefono carico", "Acqua"]},
        {"parole": ["concerto", "festa", "event", "ass. sanitaria", "assistenza", "taranta", "cover", "carnevale", "processione", "ballo", "notte"],
         "aggiungi": ["Giubbino alta visibilità", "Telefono carico", "Acqua", "Snack"]},
        {"parole": ["ts ", "ts con", "ambulanza", "ospedale", "ps casarano", "taxi"],
         "aggiungi": ["Patente (se guidi)", "Mascherina FFP2", "Penna per la scheda servizio"]},
        {"parole": ["scuol", "assemblea", "riunione", "corso", "esercitazione"],
         "aggiungi": ["Polo o felpa CRI", "Penna e blocco note"]},
        {"parole": ["gara", "podistica", "test atletici", "casarun", "gioc"],
         "aggiungi": ["Giubbino alta visibilità", "Acqua", "Cappello"]},
    ],
}


def carica_cosa_portare():
    f = Path("cosa_portare.json")
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            print("Avviso: cosa_portare.json non valido, uso la lista predefinita.")
    return COSA_PORTARE_PREDEFINITO


def cosa_portare(t, m, regole):
    testo = f' {t.get("attivita", "")} {t.get("turno", "")} '.lower()
    lista = list(regole.get("sempre", []))
    for r in regole.get("regole", []):
        if any(p.lower() in testo for p in r.get("parole", [])):
            lista += r.get("aggiungi", [])
    if m:
        if m["pioggia"] >= 40 or "pioggia" in m["descrizione"] or "rovesci" in m["descrizione"] or "temporale" in m["descrizione"]:
            lista.append("Giacca impermeabile")
        if m["min"] <= 10:
            lista.append("Giacca pesante")
        if m["max"] >= 28:
            lista += ["Crema solare", "Acqua in più"]
    if t["fine"].hour >= 19 or t["fine"].date() > t["inizio"].date() or t["inizio"].hour >= 18:
        lista.append("Torcia")
    visti, finale = set(), []
    for x in lista:
        if x.lower() not in visti:
            visti.add(x.lower())
            finale.append(x)
    return finale


def arricchisci_turni(turni, meteo, regole):
    for t in turni:
        t["meteo"] = meteo_turno(t, meteo) if meteo else None
        t["partenza"] = ora_partenza(t)
        t["portare"] = cosa_portare(t, t["meteo"], regole)


def riga_dettagli_turno(t):
    """Blocco con meteo, partenza e lista 'cosa portare' per la scheda Da fare."""
    parti = []
    if t.get("meteo"):
        parti.append(f'<div class="det">{e(testo_meteo(t["meteo"]))}</div>')
    if t.get("partenza"):
        parti.append(f'<div class="det">🚗 Parti da casa alle <b>{t["partenza"]:%H:%M}</b></div>')
    if t.get("portare"):
        voci = "".join(f"<li>{e(x)}</li>" for x in t["portare"])
        parti.append(f'<details class="portare"><summary>🎒 Cosa portare ({len(t["portare"])})</summary>'
                     f'<ul>{voci}</ul></details>')
    return f'<div class="dettagli">{"".join(parti)}</div>' if parti else ""


# ------------------------------------------------------------------ grafici (SVG)
ROSSO, BLU, AMBRA = "#cc0000", "#2f6fd0", "#c98a00"
GRIGLIA, TESTO, TESTO2 = "#e5e5ea", "#1d1d1f", "#6e6e73"


def _scala(v, divisioni=4):
    """Ritorna (massimo, passo) con numeri tondi per l'asse."""
    if v <= 0:
        return 10, 2.5
    import math
    grezzo = v / divisioni
    base = 10 ** math.floor(math.log10(grezzo))
    for k in (1, 2, 2.5, 5, 10):
        if k * base >= grezzo:
            passo = k * base
            break
    return passo * math.ceil(v / passo), passo


def grafico_ore_mese(effettuati, adesso):
    mesi = []
    a, m = adesso.year, adesso.month
    for _ in range(12):
        mesi.append((a, m))
        m -= 1
        if m == 0:
            a, m = a - 1, 12
    mesi.reverse()
    valori = []
    for (a, m) in mesi:
        sel = [t for t in effettuati if t["inizio"].year == a and t["inizio"].month == m]
        valori.append((a, m, len(sel), sum(t["ore_reali"] for t in sel)))
    L, H, sx, sb, st = 640, 260, 44, 34, 20
    larg = (L - sx - 10) / len(valori)
    tetto, passo = _scala(max(v[3] for v in valori))
    y = lambda v: st + (H - st - sb) * (1 - v / tetto)
    out = [f'<svg viewBox="0 0 {L} {H}" class="grafico" role="img" aria-label="Ore di servizio per mese">']
    for k in range(int(round(tetto / passo)) + 1):
        val = passo * k
        out.append(f'<line x1="{sx}" x2="{L-10}" y1="{y(val):.1f}" y2="{y(val):.1f}" stroke="{GRIGLIA}" stroke-width="1"/>'
                   f'<text x="{sx-6}" y="{y(val)+4:.1f}" text-anchor="end" font-size="11" fill="{TESTO2}">{f_num(val,0)}</text>')
    massimo = max(range(len(valori)), key=lambda i: valori[i][3])
    for i, (a, m, n, o) in enumerate(valori):
        x = sx + i * larg + 2
        w = larg - 4
        top = y(o)
        alt = max(H - sb - top, 0)
        if alt > 0:
            r = min(4, w / 2, alt)
            out.append(f'<path d="M{x:.1f},{H-sb} V{top+r:.1f} Q{x:.1f},{top:.1f} {x+r:.1f},{top:.1f} '
                       f'H{x+w-r:.1f} Q{x+w:.1f},{top:.1f} {x+w:.1f},{top+r:.1f} V{H-sb} Z" fill="{ROSSO}">'
                       f'<title>{MESI_BREVI[m]} {a}: {n} turni, {f_num(o)} ore</title></path>')
        # area di tocco/hover piu' grande della barra
        out.append(f'<rect x="{sx + i*larg:.1f}" y="{st}" width="{larg:.1f}" height="{H-st-sb}" fill="transparent">'
                   f'<title>{MESI_BREVI[m]} {a}: {n} turni, {f_num(o)} ore</title></rect>')
        if i == massimo or i == len(valori) - 1:
            if o > 0:
                out.append(f'<text x="{x + w/2:.1f}" y="{top-5:.1f}" text-anchor="middle" font-size="11" '
                           f'font-weight="600" fill="{TESTO}">{f_num(o,0)}</text>')
        etich = MESI_BREVI[m] + (f" {str(a)[2:]}" if m == 1 or i == 0 else "")
        out.append(f'<text x="{x + w/2:.1f}" y="{H-sb+16}" text-anchor="middle" font-size="10.5" fill="{TESTO2}">{etich}</text>')
    out.append(f'<line x1="{sx}" x2="{L-10}" y1="{H-sb}" y2="{H-sb}" stroke="#c7c7cc" stroke-width="1"/></svg>')
    tabella = "".join(f'<tr><td>{MESI_BREVI[m]} {a}</td><td class="n">{n}</td><td class="n">{f_num(o)}</td></tr>'
                      for a, m, n, o in reversed(valori))
    return ("".join(out),
            f'<details><summary class="sub">Vedi i numeri</summary><div class="scorri"><table><tr><th>Mese</th>'
            f'<th class="n">Turni</th><th class="n">Ore reali</th></tr>{tabella}</table></div></details>')


def grafico_confronto_anni(effettuati, adesso):
    anni = sorted({t["inizio"].year for t in effettuati}, reverse=True)[:3]
    if not anni:
        return '<p class="vuoto">Nessun dato.</p>'
    colori = {a: c for a, c in zip(anni, (ROSSO, BLU, AMBRA))}
    L, H, sx, sb, st, dx = 640, 260, 44, 30, 16, 70
    serie = {}
    for a in anni:
        punti, cum = [(0, 0.0)], 0.0
        for t in sorted((t for t in effettuati if t["inizio"].year == a), key=lambda t: t["inizio"]):
            g = t["inizio"].timetuple().tm_yday
            punti.append((g, cum))
            cum += t["ore_reali"]
            punti.append((g, cum))
        fine = adesso.timetuple().tm_yday if a == adesso.year else 365
        punti.append((fine, cum))
        serie[a] = (punti, cum)
    tetto, passo = _scala(max(v[1] for v in serie.values()))
    X = lambda g: sx + (L - sx - dx) * g / 366
    Y = lambda v: st + (H - st - sb) * (1 - v / tetto)
    out = [f'<svg viewBox="0 0 {L} {H}" class="grafico" role="img" aria-label="Ore cumulate per anno">']
    for k in range(int(round(tetto / passo)) + 1):
        val = passo * k
        out.append(f'<line x1="{sx}" x2="{L-dx}" y1="{Y(val):.1f}" y2="{Y(val):.1f}" stroke="{GRIGLIA}"/>'
                   f'<text x="{sx-6}" y="{Y(val)+4:.1f}" text-anchor="end" font-size="11" fill="{TESTO2}">{f_num(val,0)}</text>')
    for m in range(1, 13, 2):
        g = datetime(2025, m, 1).timetuple().tm_yday
        out.append(f'<text x="{X(g):.1f}" y="{H-sb+16}" font-size="10.5" fill="{TESTO2}">{MESI_BREVI[m]}</text>')
    etichette = []
    for a in reversed(anni):
        punti, tot = serie[a]
        d = " ".join(f"{'M' if i == 0 else 'L'}{X(g):.1f},{Y(v):.1f}" for i, (g, v) in enumerate(punti))
        out.append(f'<path d="{d}" fill="none" stroke="#fff" stroke-width="5" stroke-linejoin="round"/>'
                   f'<path d="{d}" fill="none" stroke="{colori[a]}" stroke-width="2" stroke-linejoin="round">'
                   f'<title>{a}: {f_num(tot)} ore reali</title></path>')
        gx, gy = X(punti[-1][0]), Y(tot)
        out.append(f'<circle cx="{gx:.1f}" cy="{gy:.1f}" r="4" fill="{colori[a]}" stroke="#fff" stroke-width="2"/>')
        etichette.append([gy, a, tot])
    etichette.sort()
    for i in range(1, len(etichette)):  # evita etichette sovrapposte
        if etichette[i][0] - etichette[i-1][0] < 26:
            etichette[i][0] = etichette[i-1][0] + 26
    for gy, a, tot in etichette:
        out.append(f'<text x="{L-dx+8}" y="{gy-2:.1f}" font-size="11.5" font-weight="700" fill="{TESTO}">{a}</text>'
                   f'<text x="{L-dx+8}" y="{gy+11:.1f}" font-size="10.5" fill="{TESTO2}">{f_num(tot,0)} h</text>')
    out.append(f'<line x1="{sx}" x2="{L-dx}" y1="{H-sb}" y2="{H-sb}" stroke="#c7c7cc"/></svg>')
    legenda = "".join(f'<span class="leg"><i style="background:{colori[a]}"></i>{a}: {f_num(serie[a][1])} h</span>'
                      for a in anni)
    return f'<div class="legenda">{legenda}</div>' + "".join(out)


def grafico_attivita(attivita):
    righe = attivita[:8]
    if not righe:
        return '<p class="vuoto">Nessun dato.</p>'
    L, riga, sx = 640, 30, 210
    H = riga * len(righe) + 8
    massimo = max(v["ore_reali"] for _, v in righe) or 1
    out = [f'<svg viewBox="0 0 {L} {H}" class="grafico" role="img" aria-label="Ore per attività">']
    for i, (nome, v) in enumerate(righe):
        y0 = 4 + i * riga
        w = (L - sx - 70) * v["ore_reali"] / massimo
        nome_breve = nome if len(nome) <= 30 else nome[:29] + "…"
        out.append(f'<text x="{sx-8}" y="{y0+18}" text-anchor="end" font-size="12" fill="{TESTO}">{e(nome_breve)}</text>')
        if w > 0:
            r = min(4, w)
            out.append(f'<path d="M{sx},{y0+5} H{sx+w-r:.1f} Q{sx+w:.1f},{y0+5} {sx+w:.1f},{y0+5+r} '
                       f'V{y0+23-r} Q{sx+w:.1f},{y0+23} {sx+w-r:.1f},{y0+23} H{sx} Z" fill="{ROSSO}">'
                       f'<title>{e(nome)}: {v["turni"]} turni, {f_num(v["ore_reali"])} ore</title></path>')
        out.append(f'<text x="{sx+w+6:.1f}" y="{y0+18}" font-size="11.5" fill="{TESTO2}">{f_num(v["ore_reali"],0)} h · {v["turni"]}</text>')
    out.append("</svg>")
    return "".join(out)


def scheda_grafici(d):
    if not d["effettuati"]:
        return '<p class="vuoto">Nessun turno effettuato da mostrare.</p>'
    barre, tabella = grafico_ore_mese(d["effettuati"], d["adesso"])
    return (f'<h3>Ore di servizio negli ultimi 12 mesi</h3><p class="sub">Ore reali (max 12 h per turno). '
            f'Tocca o passa sopra una barra per i dettagli.</p>{barre}{tabella}'
            f'<h3>Confronto tra gli anni</h3><p class="sub">Ore reali accumulate da gennaio.</p>'
            f'{grafico_confronto_anni(d["effettuati"], d["adesso"])}'
            f'<h3>Attività in cui fai più ore</h3><p class="sub">Ore reali e numero di turni.</p>'
            f'{grafico_attivita(d["attivita"])}')


# ------------------------------------------------------------------ resoconto stampabile
def scheda_resoconto(d, nome_volontario=""):
    adesso = d["adesso"]
    righe = []
    for t in sorted(d["effettuati"], key=lambda t: t["inizio"]):
        orario = (f'{t["inizio"]:%H:%M}–{t["fine"]:%H:%M}' if t["inizio"].date() == t["fine"].date()
                  else f'{t["inizio"]:%H:%M} → {t["fine"]:%d/%m %H:%M}')
        righe.append(
            f'<tr class="res-riga" data-giorno="{t["inizio"]:%Y-%m-%d}" data-ore="{t["ore_reali"]:.4f}">'
            f'<td class="nowrap">{t["inizio"]:%d/%m/%Y}<div class="sub">{e(orario)}</div></td>'
            f'<td>{e(t["attivita"])}<div class="sub">{e(t["turno"])}</div></td>'
            f'<td class="n">{f_num(t["ore_reali"])}</td></tr>')
    anni = sorted({t["inizio"].year for t in d["effettuati"]}, reverse=True)
    un_anno = (adesso - timedelta(days=365)).date()
    scorciatoie = [("Ultimi 12 mesi", f"{un_anno:%Y-%m-%d}", f"{adesso:%Y-%m-%d}")]
    scorciatoie += [(str(a), f"{a}-01-01", f"{a}-12-31") for a in anni]
    bottoni = "".join(f'<button type="button" class="chip" data-res-da="{da}" data-res-a="{a}">{e(n)}</button>'
                      for n, da, a in scorciatoie)
    primo = d["effettuati"][-1]["inizio"] if d["effettuati"] else adesso
    return f"""
<div class="box non-stampa">
<p>Scegli il periodo e premi <b>Stampa / Salva PDF</b>. Sull'iPhone, nella schermata di stampa
tocca <b>Condividi</b> → <b>Salva su File</b> per avere il PDF.</p>
<div class="chips">{bottoni}</div>
<div class="campi"><label>Dal <input type="date" id="res-da" value="{primo:%Y-%m-%d}"></label>
<label>Al <input type="date" id="res-a" value="{adesso:%Y-%m-%d}"></label></div>
<label class="campo-nome">Nome sul resoconto <input type="text" id="res-nome" value="{e(nome_volontario)}" placeholder="Nome e cognome"></label>
<button type="button" id="res-stampa" class="bottone grande">🖨️ Stampa / Salva PDF</button>
</div>
<div class="resoconto-stampa">
<h2 class="res-titolo">Resoconto attività di volontariato – Croce Rossa Italiana</h2>
<p><b>Volontario:</b> <span id="res-nome-stampa">{e(nome_volontario)}</span><br>
<b>Periodo:</b> <span id="res-periodo"></span><br>
<b>Totale:</b> <span id="res-tot-turni"></span> turni · <span id="res-tot-ore"></span> ore di servizio</p>
<table class="res-tabella"><tr><th>Data e orario</th><th>Attività / turno</th><th class="n">Ore</th></tr>
{''.join(righe)}</table>
<p class="sub">Dati tratti dallo storico del portale GAIA. Ore calcolate dalla durata dei turni
(massimo 12 ore per turno). Documento generato il {adesso:%d/%m/%Y}.</p>
</div>"""


CSS_EXTRA = """
.dettagli{margin-top:6px;display:flex;flex-direction:column;gap:3px}
.det{font-size:.84rem}
.portare summary{cursor:pointer;font-size:.84rem;font-weight:600}
.portare ul{margin:4px 0 0;padding-left:20px;font-size:.84rem}
.grafico{width:100%;height:auto;background:#fff;border:1px solid var(--bordo);border-radius:12px;font-family:inherit}
.legenda{display:flex;gap:14px;flex-wrap:wrap;margin:4px 0 8px;font-size:.85rem}
.leg i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:5px;vertical-align:-1px}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}
.chip{border:1px solid var(--bordo);background:#fff;border-radius:999px;padding:6px 12px;font-size:.85rem;cursor:pointer;font-family:inherit}
.campi{display:flex;gap:12px;flex-wrap:wrap;margin:8px 0}
.campi input,.campo-nome input{font:inherit;padding:6px 8px;border:1px solid var(--bordo);border-radius:8px}
.campo-nome{display:block;margin:8px 0}
.campo-nome input{width:100%;box-sizing:border-box;margin-top:4px}
.resoconto-stampa{background:#fff;border:1px solid var(--bordo);border-radius:12px;padding:16px;margin-top:12px}
.res-titolo{font-size:1.1rem;margin:0 0 10px;color:var(--rosso)}
.res-tabella{font-size:.85rem}
.nowrap{white-space:nowrap}
.res-riga.fuori{display:none}
@media print{
  body *{visibility:hidden}
  .resoconto-stampa,.resoconto-stampa *{visibility:visible}
  .resoconto-stampa{position:absolute;left:0;top:0;width:100%;border:0;padding:0}
  .res-tabella tr{break-inside:avoid}
}
"""
