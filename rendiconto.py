"""
Rendiconto turni GAIA CRI
-------------------------
Entra in GAIA con le tue credenziali (prese dai Secrets di GitHub),
legge storico turni, monte ore, calendario dei turni disponibili e corsi aperti,
e crea una pagina web PROTETTA DA PASSWORD (cifrata) nella cartella "sito".

Avvio normale (su GitHub):   python rendiconto.py
Prova senza GAIA:            python rendiconto.py --prova CARTELLA
  (la cartella deve contenere storico.html, calendario.html, corsi.html)

ATTENZIONE: se il repository e' pubblico anche i log di GitHub sono pubblici,
quindi questo programma stampa solo numeri, mai nomi o dettagli dei turni.
"""

import base64
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

# ------------------------------------------------------------------ impostazioni
GAIA = "https://gaia.cri.it"
FUSO = ZoneInfo("Europe/Rome")
COMITATO = os.environ.get("COMITATO", "Casarano")       # i corsi di questo comitato vanno in cima
REGIONE = os.environ.get("REGIONE", "puglia")           # poi quelli di questa regione
SETTIMANE_CALENDARIO = int(os.environ.get("SETTIMANE_CALENDARIO", "4"))
TETTO_ORE_TURNO = float(os.environ.get("TETTO_ORE_TURNO", "12"))  # oltre, il turno e' "sospetto"
TURNI_RICHIESTI = int(os.environ.get("TURNI_RICHIESTI", "6"))      # regola: 6 turni...
MESI_PERIODO = int(os.environ.get("MESI_PERIODO", "2"))            # ...ogni 2 mesi (gen-feb, mar-apr, ...)
GIORNI_ALLARME = int(os.environ.get("GIORNI_ALLARME", "30"))       # avviso rosso negli ultimi 30 giorni

MESI = {"gen": 1, "feb": 2, "mar": 3, "apr": 4, "mag": 5, "giu": 6,
        "lug": 7, "ago": 8, "set": 9, "ott": 10, "nov": 11, "dic": 12}
GIORNI = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
MESI_NOMI = ["", "Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio",
             "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]


def pulisci(testo):
    return re.sub(r"\s+", " ", testo or "").strip()


def data_ora(testo):
    return datetime.strptime(pulisci(testo), "%d/%m/%Y %H:%M").replace(tzinfo=FUSO)


def link_assoluto(href):
    return GAIA + href if href and href.startswith("/") else (href or "")


def id_turno(href):
    m = re.search(r"link-permanente/(\d+)", href or "")
    return m.group(1) if m else None


# ------------------------------------------------------------------ lettura pagine
def leggi_statistiche(pagina):
    """Tabella 'Statistiche' della pagina storico (monte ore di GAIA)."""
    soup = BeautifulSoup(pagina, "html.parser")
    righe = []
    for h2 in soup.find_all("h2"):
        if "Statistiche" not in h2.get_text():
            continue
        pannello = h2.find_parent("div", class_="panel")
        tabella = pannello.find("table") if pannello else None
        if not tabella:
            continue
        for tr in tabella.find("tbody").find_all("tr"):
            celle = [pulisci(td.get_text()) for td in tr.find_all("td")]
            if len(celle) < 3:
                continue
            m = re.search(r"\(([\d.,]+) ore\)", celle[2])
            ore = float(m.group(1).replace(".", "").replace(",", ".")) if m else 0.0
            righe.append({"periodo": pulisci(celle[0].replace("—", "–")),
                          "turni": int(re.sub(r"\D", "", celle[1]) or 0),
                          "ore": ore, "testo": celle[2].split("(")[0].strip()})
    return righe


def leggi_storico(pagina):
    """Tabella 'Storico delle richieste di partecipazione'."""
    soup = BeautifulSoup(pagina, "html.parser")
    turni = []
    for h2 in soup.find_all("h2"):
        if "Storico delle richieste" not in h2.get_text():
            continue
        tabella = h2.find_parent("div", class_="panel").find("table")
        for tr in tabella.find("tbody").find_all("tr", recursive=False):
            celle = tr.find_all("td", recursive=False)
            if len(celle) < 3:
                continue
            link_turno = celle[0].find("a", href=re.compile("link-permanente"))
            link_att = [a for a in celle[0].find_all("a") if a is not link_turno]
            testo_data = celle[1].get_text(" ")
            ini = re.search(r"Inizio:\s*(\d\d/\d\d/\d{4} \d\d:\d\d)", testo_data)
            fin = re.search(r"Fine:\s*(\d\d/\d\d/\d{4} \d\d:\d\d)", testo_data)
            if not ini or not fin:
                continue
            stato_span = celle[2].find("span", class_="piu-grande")
            stato = pulisci(stato_span.get_text()) if stato_span else "?"
            scadenza = None
            bottone = celle[2].find("button", attrs={"data-content": True})
            if bottone:
                m = re.search(r"Negazione automatica il (\d\d/\d\d/\d{4} \d\d:\d\d)",
                              bottone["data-content"])
                if m:
                    scadenza = data_ora(m.group(1))
            richiesta = None
            if len(celle) > 3:
                m = re.search(r"Richiesta:\s*(\d\d/\d\d/\d{4} \d\d:\d\d)", celle[3].get_text(" "))
                if m:
                    richiesta = data_ora(m.group(1))
            turni.append({
                "turno": pulisci(link_turno.get_text()) if link_turno else "",
                "attivita": pulisci(link_att[0].get_text()) if link_att else "",
                "link": link_assoluto(link_turno["href"]) if link_turno else "",
                "id": id_turno(link_turno["href"]) if link_turno else None,
                "inizio": data_ora(ini.group(1)),
                "fine": data_ora(fin.group(1)),
                "stato": stato,
                "scadenza_richiesta": scadenza,
                "richiesta": richiesta,
            })
        break
    return turni


def _anno_per_mese(mese, oggi):
    """Il calendario mostra giorno e mese senza anno: scelgo l'anno piu' vicino a oggi."""
    anno = oggi.year
    if mese < oggi.month - 6:
        anno += 1
    elif mese > oggi.month + 6:
        anno -= 1
    return anno


def leggi_calendario(pagina, oggi):
    soup = BeautifulSoup(pagina, "html.parser")
    turni = []
    for tr in soup.find_all("tr"):
        cella_giorno = tr.find("td", class_="calendario-giorno")
        if not cella_giorno:
            continue
        try:
            giorno = int(pulisci(cella_giorno.find("div", class_="giorno").get_text()))
            mese = MESI[pulisci(cella_giorno.find("div", class_="mese").get_text())[:3].lower()]
        except (AttributeError, KeyError, ValueError):
            continue
        anno = _anno_per_mese(mese, oggi)
        for box in tr.find_all("div", class_="calendario-turno"):
            orario = box.find("span", class_="grassetto")
            orari = re.findall(r"\d\d:\d\d", orario.get_text() if orario else "")
            if len(orari) < 2:
                continue
            inizio = datetime(anno, mese, giorno, *map(int, orari[0].split(":")), tzinfo=FUSO)
            fine = datetime(anno, mese, giorno, *map(int, orari[1].split(":")), tzinfo=FUSO)
            if fine <= inizio:
                fine += timedelta(days=1)
            att = box.find("a", class_="piu-grande")
            tur = box.find("a", class_="grassetto")
            riga2 = box.find("div", class_="calendario-turno-riga2")
            testo2 = riga2.get_text(" ") if riga2 else ""
            pren = re.search(r"(\d+)\s*prenotati", testo2)
            mx = re.search(r"max\.\s*(\d+)", testo2)
            colonne = riga2.find_all("div", recursive=False) if riga2 else []
            luogo = pulisci(colonne[1].get_text()) if len(colonne) > 1 else ""
            comitato = pulisci(colonne[2].get_text()) if len(colonne) > 2 else ""
            href = tur["href"] if tur else ""
            turni.append({
                "attivita": pulisci(att.get_text()) if att else "",
                "turno": pulisci(tur.get_text()) if tur else "",
                "link": link_assoluto(href),
                "id": id_turno(href),
                "inizio": inizio, "fine": fine,
                "prenotati": int(pren.group(1)) if pren else 0,
                "massimo": int(mx.group(1)) if mx else None,
                "scoperto": "alert-warning" in (box.get("class") or []),
                "luogo": "" if luogo.lower() == "locazione sconosciuta" else luogo,
                "comitato": comitato,
            })
    return turni


def leggi_corsi(pagina):
    soup = BeautifulSoup(pagina, "html.parser")
    tabella = soup.find("table", class_="aspirante-corsi-table")
    corsi = []
    if not tabella:
        return corsi
    for tr in tabella.find_all("tr"):
        celle = tr.find_all("td", recursive=False)
        if len(celle) < 3:
            continue
        titolo = celle[0].find("strong")
        a_titolo = titolo.find("a") if titolo else None
        a_sede = celle[0].find("a", href=re.compile("/informazioni/sedi/"))
        testo0 = celle[0].get_text("\n")
        righe0 = [pulisci(r) for r in testo0.split("\n") if pulisci(r)]
        indirizzo = righe0[-1] if len(righe0) >= 3 else ""
        info = celle[1].get_text(" ")
        ini = re.search(r"(Inizia|Iniziato):\s*(\d\d/\d\d/\d{4} \d\d:\d\d)", info)
        esa = re.search(r"Esami:\s*(\d\d/\d\d/\d{4} \d\d:\d\d)", info)
        evento = celle[1].find("a", href=re.compile("/evento/"))
        rich = re.search(r"(\d+)", celle[2].get_text())
        nome = pulisci(a_titolo.get_text()) if a_titolo else ""
        m_cod = re.search(r"\s([A-Z]{3}/\d{4}/.+)$", nome)
        corsi.append({
            "nome": nome[:m_cod.start()].strip() if m_cod else nome,
            "codice": m_cod.group(1) if m_cod else "",
            "link": link_assoluto(a_titolo["href"]) if a_titolo else "",
            "sede": pulisci(a_sede.get_text()) if a_sede else "",
            "sede_link": a_sede["href"] if a_sede else "",
            "indirizzo": indirizzo,
            "inizio": data_ora(ini.group(2)) if ini else None,
            "iniziato": bool(ini and ini.group(1) == "Iniziato"),
            "esami": data_ora(esa.group(1)) if esa else None,
            "online": "Corso online" in info,
            "evento": pulisci(evento.get_text()) if evento else "",
            "richieste": int(rich.group(1)) if rich else 0,
        })
    return corsi


def leggi_qualifiche(pagina):
    """Pagina Curriculum > Qualifiche CRI (/utente/curriculum/TC/)."""
    soup = BeautifulSoup(pagina, "html.parser")
    qualifiche = []
    for tr in soup.find_all("tr"):
        celle = tr.find_all("td", recursive=False)
        if len(celle) < 2:
            continue
        titolo = celle[0].find("p", class_="grassetto")
        if not titolo:
            continue
        attestato = celle[0].find("a", href=re.compile("attestato"))
        stato = celle[0].find("span")
        dettagli = celle[1].get_text(" ")
        data = re.search(r"(\d\d/\d\d/\d{4})", dettagli)
        tipo = re.search(r"Corso formazione asincrono[^\n<]*", dettagli)
        qualifiche.append({
            "nome": pulisci(titolo.get_text()),
            "stato": pulisci(stato.get_text()) if stato else "",
            "data": datetime.strptime(data.group(1), "%d/%m/%Y").replace(tzinfo=FUSO) if data else None,
            "attestato": link_assoluto(attestato["href"]) if attestato else "",
            "online": bool(tipo) or "online" in titolo.get_text().lower(),
        })
    # se la stessa qualifica compare piu' volte (rinnovi), segno la piu' recente
    piu_recente = {}
    for q in qualifiche:
        k = q["nome"].lower()
        if q["data"] and (k not in piu_recente or q["data"] > piu_recente[k]["data"]):
            piu_recente[k] = q
    for q in qualifiche:
        uguali = [x for x in qualifiche if x["nome"].lower() == q["nome"].lower()]
        q["volte"] = len(uguali)
        q["superata"] = len(uguali) > 1 and piu_recente.get(q["nome"].lower()) is not q
    return sorted(qualifiche, key=lambda q: q["data"] or datetime.min.replace(tzinfo=FUSO), reverse=True)


# ------------------------------------------------------------------ calcoli
def ore(t):
    return (t["fine"] - t["inizio"]).total_seconds() / 3600


def analizza(storico, statistiche, calendario, corsi, adesso, qualifiche=None, eventi=None):
    approvati = [t for t in storico if t["stato"].lower().startswith("approvat")]
    effettuati = sorted([t for t in approvati if t["fine"] <= adesso],
                        key=lambda t: t["inizio"], reverse=True)
    da_fare = sorted([t for t in approvati if t["fine"] > adesso], key=lambda t: t["inizio"])
    in_sospeso = sorted([t for t in storico if "attesa" in t["stato"].lower()],
                        key=lambda t: t["inizio"])
    altri = [t for t in storico if t not in approvati and t not in in_sospeso]

    for t in effettuati + da_fare:
        t["ore"] = ore(t)
        t["ore_reali"] = min(t["ore"], TETTO_ORE_TURNO)
        t["sospetto"] = t["ore"] > TETTO_ORE_TURNO

    anni = {}
    for t in effettuati:
        a = anni.setdefault(t["inizio"].year, {"turni": 0, "ore": 0.0, "ore_reali": 0.0})
        a["turni"] += 1
        a["ore"] += t["ore"]
        a["ore_reali"] += t["ore_reali"]

    attivita = {}
    for t in effettuati:
        a = attivita.setdefault(t["attivita"], {"turni": 0, "ore_reali": 0.0})
        a["turni"] += 1
        a["ore_reali"] += t["ore_reali"]

    un_anno_fa = adesso - timedelta(days=365)
    ultimo_anno = [t for t in effettuati if t["inizio"] >= un_anno_fa]

    prenotati = {t["id"] for t in da_fare + in_sospeso if t["id"]}
    disponibili = [t for t in calendario if t["fine"] > adesso]
    for t in disponibili:
        t["mio"] = t["id"] in prenotati
        t["liberi"] = None if t["massimo"] is None else max(t["massimo"] - t["prenotati"], 0)

    def priorita(c):
        if COMITATO.lower() in c["sede"].lower():
            return 0
        if REGIONE.lower() in c["sede_link"].lower():
            return 1
        return 2
    corsi_ord = sorted(corsi, key=lambda c: (priorita(c), c["iniziato"],
                                              c["inizio"] or adesso))
    for c in corsi_ord:
        c["gruppo"] = priorita(c)
        if c["inizio"] and c["inizio"] <= adesso:
            c["iniziato"] = True

    regola = calcola_regola(effettuati, da_fare, adesso)

    import calendario_icloud
    for t in disponibili + da_fare:
        t["impegno"], t["impegni"] = (calendario_icloud.conflitti(t, eventi) if eventi else (None, []))

    return {
        "adesso": adesso,
        "regola": regola,
        "effettuati": effettuati, "da_fare": da_fare, "in_sospeso": in_sospeso, "altri": altri,
        "statistiche": statistiche, "anni": dict(sorted(anni.items(), reverse=True)),
        "attivita": sorted(attivita.items(), key=lambda kv: -kv[1]["ore_reali"]),
        "ultimo_anno": {"turni": len(ultimo_anno),
                        "ore": sum(t["ore"] for t in ultimo_anno),
                        "ore_reali": sum(t["ore_reali"] for t in ultimo_anno)},
        "totale": {"turni": len(effettuati),
                   "ore": sum(t["ore"] for t in effettuati),
                   "ore_reali": sum(t["ore_reali"] for t in effettuati)},
        "sospetti": [t for t in effettuati + da_fare if t["sospetto"]],
        "disponibili": sorted(disponibili, key=lambda t: t["inizio"]),
        "corsi": corsi_ord,
        "qualifiche": qualifiche or [],
        "calendario_letto": eventi is not None,
    }


def inizio_periodo(d):
    """Periodi fissi di MESI_PERIODO mesi: con 2 mesi -> gen-feb, mar-apr, mag-giu, ..."""
    m = ((d.month - 1) // MESI_PERIODO) * MESI_PERIODO + 1
    return datetime(d.year, m, 1, tzinfo=FUSO)


def periodo_dopo(inizio):
    m = inizio.month - 1 + MESI_PERIODO
    return datetime(inizio.year + m // 12, m % 12 + 1, 1, tzinfo=FUSO)


def nome_periodo(inizio):
    fine = periodo_dopo(inizio) - timedelta(days=1)
    if inizio.month == fine.month:
        return f"{MESI_NOMI[inizio.month]} {inizio.year}"
    return f"{MESI_NOMI[inizio.month][:3]}–{MESI_NOMI[fine.month][:3]} {fine.year}"


def calcola_regola(effettuati, da_fare, adesso):
    """Controllo della regola 'TURNI_RICHIESTI turni ogni MESI_PERIODO mesi'."""
    ini = inizio_periodo(adesso)
    fine = periodo_dopo(ini)
    fatti = [t for t in effettuati if ini <= t["inizio"] < fine]
    previsti = [t for t in da_fare if ini <= t["inizio"] < fine]
    mancano = max(TURNI_RICHIESTI - len(fatti), 0)
    mancano_con_previsti = max(TURNI_RICHIESTI - len(fatti) - len(previsti), 0)
    giorni_rimasti = (fine.date() - adesso.date()).days
    if mancano == 0:
        livello, messaggio = "ok", f"In regola: {len(fatti)} turni fatti su {TURNI_RICHIESTI}."
    elif mancano_con_previsti == 0:
        livello, messaggio = "attenzione", (
            f"{len(fatti)} turni fatti su {TURNI_RICHIESTI}: sarai in regola se svolgi "
            f"i {len(previsti)} turni già programmati.")
    elif giorni_rimasti > GIORNI_ALLARME:
        livello, messaggio = "attenzione", (
            f"{len(fatti)} turni fatti su {TURNI_RICHIESTI} (già programmati {len(previsti)}): "
            f"ne mancano {mancano_con_previsti}, hai tempo fino al {(fine - timedelta(days=1)):%d/%m}.")
    else:
        livello, messaggio = "allarme", (
            f"Ti mancano {mancano_con_previsti} turni da prenotare entro "
            f"{(fine - timedelta(days=1)):%d/%m} ({giorni_rimasti} giorni). "
            f"Fatti {len(fatti)}, già programmati {len(previsti)}, richiesti {TURNI_RICHIESTI}.")

    # periodi passati, dal primo turno dello storico
    storico = []
    if effettuati:
        p = inizio_periodo(min(t["inizio"] for t in effettuati))
        while p < ini:
            f = periodo_dopo(p)
            n = sum(1 for t in effettuati if p <= t["inizio"] < f)
            storico.append({"nome": nome_periodo(p), "turni": n, "ok": n >= TURNI_RICHIESTI})
            p = f
    return {"nome": nome_periodo(ini), "fatti": len(fatti), "previsti": len(previsti),
            "mancano": mancano_con_previsti, "giorni_rimasti": giorni_rimasti,
            "livello": livello, "messaggio": messaggio, "storico": list(reversed(storico))}


# ------------------------------------------------------------------ pagina web
def e(x):
    return html.escape(str(x if x is not None else ""))


def f_ore(x):
    return f"{x:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")


def f_data(d, con_ora=True):
    if not d:
        return ""
    s = f"{GIORNI[d.weekday()]} {d:%d/%m/%Y}"
    return s + f" {d:%H:%M}" if con_ora else s


def f_orario(t):
    if t["inizio"].date() == t["fine"].date():
        return f"{f_data(t['inizio'])}–{t['fine']:%H:%M}"
    return f"{f_data(t['inizio'])} → {f_data(t['fine'])}"


def titolo_breve(testo):
    """Toglie le note tra parentesi con link (es. moduli Google) dai titoli dei turni."""
    return pulisci(re.sub(r"\([^)]*https?://[^)]*\)", "", testo))


def tabella_turni(turni, colonne_extra=None, vuoto="Nessun turno.", per_mese=False):
    """Elenco a schede (si legge bene anche sul telefono)."""
    if not turni:
        return f'<p class="vuoto">{vuoto}</p>'
    parti, mese_corrente = [], None
    for t in turni:
        if per_mese:
            m = (t["inizio"].year, t["inizio"].month)
            if m != mese_corrente:
                if mese_corrente is not None:
                    parti.append("</div>")
                stesso = [x for x in turni if (x["inizio"].year, x["inizio"].month) == m]
                ore_mese = sum(x.get("ore_reali", 0) for x in stesso)
                parti.append(f'<h4>{MESI_NOMI[m[1]]} {m[0]} · {len(stesso)} turni · '
                             f'{f_ore(ore_mese)} h</h4><div class="lista">')
                mese_corrente = m
        elif not parti:
            parti.append('<div class="lista">')
        extra = colonne_extra(t) if colonne_extra else ""
        segno = ('<span class="tag rosso">⚠ durata sospetta</span>' if t.get("sospetto") else "")
        if t["inizio"].date() == t["fine"].date():
            quando = f'{f_data(t["inizio"], False)}<br>{t["inizio"]:%H:%M}–{t["fine"]:%H:%M}'
        else:
            quando = f'{f_data(t["inizio"])}<br>→ {f_data(t["fine"])}'
        parti.append(
            f'<a class="voce" href="{e(t["link"])}" target="_blank" rel="noopener">'
            f'<div class="ora">{quando}</div>'
            f'<div class="cosa"><b>{e(titolo_breve(t["turno"]))}</b>'
            f'<div class="sub">{e(t["attivita"])}</div>{segno}{riga_impegni(t)}</div>'
            f'<div class="posti">{extra}</div></a>')
    parti.append("</div>")
    return "".join(parti)


def riga_impegni(t):
    """Avviso 'sei gia' impegnato' con gli eventi del calendario iPhone di quel giorno."""
    if not t.get("impegno"):
        return ""
    import calendario_icloud
    voci = "<br>".join(e(calendario_icloud.descrivi(ev)) for ev in t["impegni"][:3])
    if len(t["impegni"]) > 3:
        voci += f"<br>e altri {len(t['impegni']) - 3}"
    if t["impegno"] == "sovrapposto":
        return f'<div class="impegno rosso">⚠ <b>Sei già impegnato a quest\'ora</b><br>{voci}</div>'
    return f'<div class="impegno giallo">📅 <b>Questo giorno hai già un altro evento</b><br>{voci}</div>'


def costruisci_pagina(d, file_calendario=""):
    adesso = d["adesso"]
    tot, ua = d["totale"], d["ultimo_anno"]
    stat_gaia = d["statistiche"][-1] if d["statistiche"] else None

    # --- riepilogo
    cartelle = [
        ("Turni effettuati", len(d["effettuati"]), "dall'inizio dello storico"),
        ("Ore reali", f_ore(tot["ore_reali"]), f"max {TETTO_ORE_TURNO:g} h per turno"),
        ("Da fare", len(d["da_fare"]), "turni approvati in arrivo"),
        ("In sospeso", len(d["in_sospeso"]), "richieste in attesa"),
    ]
    html_cartelle = "".join(
        f'<div class="card"><div class="num">{e(v)}</div><div class="lab">{e(n)}</div>'
        f'<div class="sub">{e(s)}</div></div>' for n, v, s in cartelle)

    prossimo = ""
    if d["da_fare"]:
        p = d["da_fare"][0]
        prossimo = (f'<div class="box evidenza"><b>Prossimo turno:</b> '
                    f'<a href="{e(p["link"])}" target="_blank" rel="noopener">{e(titolo_breve(p["turno"]))}</a> '
                    f'({e(p["attivita"])}) — {e(f_orario(p))}</div>')

    righe_gaia = "".join(
        f'<tr><td>{e(s["periodo"])}</td><td class="n">{s["turni"]}</td>'
        f'<td class="n">{f_ore(s["ore"])}</td></tr>' for s in reversed(d["statistiche"]))
    tab_gaia = (f'<table><tr><th>Periodo (365 giorni)</th><th class="n">Turni</th>'
                f'<th class="n">Ore GAIA</th></tr>{righe_gaia}</table>'
                if righe_gaia else '<p class="vuoto">Statistiche GAIA non disponibili.</p>')

    righe_anni = "".join(
        f'<tr><td>{a}</td><td class="n">{v["turni"]}</td><td class="n">{f_ore(v["ore"])}</td>'
        f'<td class="n"><b>{f_ore(v["ore_reali"])}</b></td></tr>' for a, v in d["anni"].items())
    tab_anni = (f'<table><tr><th>Anno</th><th class="n">Turni</th><th class="n">Ore GAIA</th>'
                f'<th class="n">Ore reali</th></tr>{righe_anni}'
                f'<tr class="tot"><td>Totale</td><td class="n">{tot["turni"]}</td>'
                f'<td class="n">{f_ore(tot["ore"])}</td><td class="n">{f_ore(tot["ore_reali"])}</td></tr>'
                f'</table>')

    controllo = ""
    if stat_gaia:
        diff = abs(stat_gaia["ore"] - ua["ore"])
        esito = "✔ coincide con lo storico" if diff < 0.5 else f"⚠ differenza di {f_ore(diff)} ore con lo storico"
        controllo = (f'<p class="sub">Ultimi 365 giorni secondo GAIA: <b>{stat_gaia["turni"]} turni, '
                     f'{f_ore(stat_gaia["ore"])} ore</b> ({e(esito)}). Ore reali nello stesso periodo: '
                     f'<b>{f_ore(ua["ore_reali"])}</b>.</p>')

    sospetti = ""
    if d["sospetti"]:
        voci = "".join(
            f'<li><a href="{e(t["link"])}" target="_blank" rel="noopener">{e(titolo_breve(t["turno"]))}</a> '
            f'({e(t["attivita"])}): {e(f_orario(t))} = <b>{f_ore(t["ore"])} ore</b> su GAIA</li>'
            for t in d["sospetti"])
        sospetti = (f'<details class="box avviso"><summary><b>⚠ {len(d["sospetti"])} turni con durata '
                    f'sospetta</b> <span class="sub">(tocca per vedere)</span></summary>'
                    f'<p class="sub">Durano più di {TETTO_ORE_TURNO:g} ore, probabilmente per una data di fine '
                    f'sbagliata su GAIA. Nelle "ore reali" contano {TETTO_ORE_TURNO:g} ore ciascuno.</p>'
                    f'<ul>{voci}</ul></details>')

    r = d["regola"]
    icone = {"ok": "✔", "attenzione": "⏳", "allarme": "⚠"}
    pallini = "".join(
        f'<span class="pallino {"pieno" if i < r["fatti"] else "previsto" if i < r["fatti"] + r["previsti"] else ""}"></span>'
        for i in range(max(TURNI_RICHIESTI, r["fatti"] + r["previsti"])))
    storico_regola = "".join(
        f'<span class="tag {"verde" if p["ok"] else "rosso"}">{e(p["nome"])}: {p["turni"]}</span> '
        for p in r["storico"][:6])
    regola = (f'<div class="box regola {r["livello"]}"><div class="riga-regola">'
              f'<b>{icone[r["livello"]]} Regola {TURNI_RICHIESTI} turni ogni {MESI_PERIODO} mesi · '
              f'{e(r["nome"])}</b><div class="pallini">{pallini}</div></div>'
              f'<div>{e(r["messaggio"])}</div>'
              + (f'<details><summary class="sub">Periodi precedenti</summary><div>{storico_regola}</div></details>'
                 if storico_regola else "") + '</div>')

    righe_att = "".join(
        f'<tr><td>{e(n)}</td><td class="n">{v["turni"]}</td><td class="n">{f_ore(v["ore_reali"])}</td></tr>'
        for n, v in d["attivita"])
    tab_att = (f'<table><tr><th>Attività</th><th class="n">Turni</th><th class="n">Ore reali</th></tr>'
               f'{righe_att}</table>')

    riepilogo = (f'{regola}<div class="cards">{html_cartelle}</div>{prossimo}{sospetti}'
                 f'<h3>Monte ore per anno solare</h3><div class="scorri">{tab_anni}</div>{controllo}'
                 f'<h3>Monte ore ufficiale GAIA</h3><div class="scorri">{tab_gaia}</div>'
                 f'<h3>Per attività</h3><div class="scorri">{tab_att}</div>')

    # --- schede turni
    effettuati = tabella_turni(
        d["effettuati"], lambda t: f'<b>{f_ore(t["ore_reali"])} h</b>',
        "Nessun turno effettuato.", per_mese=True)

    def fra_quanto(t):
        giorni = (t["inizio"].date() - adesso.date()).days
        q = "oggi" if giorni == 0 else "domani" if giorni == 1 else f"fra {giorni} giorni"
        if t["inizio"] <= adesso:
            q = "in corso"
        return f'<span class="tag verde">{q}</span>'
    da_fare = tabella_turni(d["da_fare"], fra_quanto, "Nessun turno in programma.")

    def sospeso(t):
        s = (f'risposta entro {t["scadenza_richiesta"]:%d/%m %H:%M}'
             if t["scadenza_richiesta"] else "in attesa")
        return f'<span class="tag giallo">{e(s)}</span>'
    in_sospeso = tabella_turni(d["in_sospeso"], sospeso, "Nessuna richiesta in attesa di approvazione.")

    # --- disponibili, raggruppati per giorno
    if d["disponibili"]:
        blocchi, giorno_corrente = [], None
        for t in d["disponibili"]:
            g = t["inizio"].date()
            if g != giorno_corrente:
                if giorno_corrente is not None:
                    blocchi.append("</div>")
                blocchi.append(f'<h4>{e(f_data(t["inizio"], False))}</h4><div class="lista">')
                giorno_corrente = g
            posti = (f'{t["prenotati"]}/{t["massimo"]}' if t["massimo"] is not None
                     else f'{t["prenotati"]} prenotati')
            tag = ""
            if t["mio"]:
                tag = '<span class="tag verde">sei prenotato</span>'
            elif t["scoperto"]:
                tag = '<span class="tag rosso">servono volontari</span>'
            elif t["liberi"] == 0:
                tag = '<span class="tag grigio">completo</span>'
            luogo = f' · {e(t["luogo"])}' if t["luogo"] else ""
            blocchi.append(
                f'<a class="voce{" mia" if t["mio"] else ""}" href="{e(t["link"])}" target="_blank" rel="noopener">'
                f'<div class="ora">{t["inizio"]:%H:%M}–{t["fine"]:%H:%M}</div>'
                f'<div class="cosa"><b>{e(t["attivita"])}</b>, {e(titolo_breve(t["turno"]))}'
                f'<div class="sub">{e(t["comitato"])}{luogo}</div>'
                f'{"" if t["mio"] else riga_impegni(t)}</div>'
                f'<div class="posti">{e(posti)}<br>{tag}</div></a>')
        blocchi.append("</div>")
        scoperti = sum(1 for t in d["disponibili"] if t["scoperto"] and not t["mio"])
        occupati = sum(1 for t in d["disponibili"] if not t["mio"] and t.get("impegno") == "sovrapposto")
        nota_cal = (f' {occupati} si sovrappongono a impegni del tuo calendario.' if d["calendario_letto"]
                    else ' (Calendario iPhone non collegato.)')
        disponibili = (f'<p class="sub">{len(d["disponibili"])} turni nel calendario, '
                       f'{scoperti} con volontari mancanti.{nota_cal} Tocca un turno per aprirlo su GAIA.</p>'
                       + "".join(blocchi))
    else:
        disponibili = '<p class="vuoto">Nessun turno disponibile nel calendario.</p>'

    # --- corsi
    nomi_gruppi = {0: f"Comitato di {COMITATO}", 1: f"Regione {REGIONE.capitalize()}", 2: "Resto d'Italia"}
    parti = []
    for gruppo in (0, 1, 2):
        lista = [c for c in d["corsi"] if c["gruppo"] == gruppo]
        if not lista:
            continue
        voci = []
        for c in lista:
            stato = ('<span class="tag grigio">già iniziato</span>' if c["iniziato"]
                     else '<span class="tag verde">da iniziare</span>')
            extra = []
            if c["online"]:
                extra.append('<span class="tag blu">online</span>')
            if c["evento"]:
                extra.append(f'<span class="tag blu">{e(c["evento"])}</span>')
            voci.append(
                f'<a class="voce" href="{e(c["link"])}" target="_blank" rel="noopener">'
                f'<div class="ora">{e(f_data(c["inizio"]))}</div>'
                f'<div class="cosa"><b>{e(c["nome"])}</b> {" ".join(extra)}'
                f'<div class="sub">{e(c["sede"])} · {e(c["indirizzo"])}<br>'
                f'Esami: {e(f_data(c["esami"]))} · {e(c["codice"])}</div></div>'
                f'<div class="posti">{c["richieste"]} richieste<br>{stato}</div></a>')
        parti.append(f'<h4>{e(nomi_gruppi[gruppo])} ({len(lista)})</h4><div class="lista">{"".join(voci)}</div>')
    corsi = "".join(parti) or '<p class="vuoto">Nessun corso aperto.</p>'

    # --- qualifiche
    if d["qualifiche"]:
        voci = []
        for q in d["qualifiche"]:
            tag = []
            if q["superata"]:
                tag.append('<span class="tag grigio">rinnovata</span>')
            elif q["volte"] > 1:
                tag.append('<span class="tag verde">più recente</span>')
            if q["online"]:
                tag.append('<span class="tag blu">online</span>')
            if q["stato"] and q["stato"].lower() != "confermato":
                tag.append(f'<span class="tag giallo">{e(q["stato"])}</span>')
            pdf = (f'<a class="bottone" href="{e(q["attestato"])}" target="_blank" rel="noopener">📄 Attestato</a>'
                   if q["attestato"] else '<span class="sub">nessun attestato</span>')
            voci.append(
                f'<div class="voce{" spenta" if q["superata"] else ""}">'
                f'<div class="ora">{e(f_data(q["data"], False))}</div>'
                f'<div class="cosa"><b>{e(q["nome"])}</b><div>{" ".join(tag)}</div></div>'
                f'<div class="posti">{pdf}</div></div>')
        qualifiche = (f'<p class="sub">{len(d["qualifiche"])} qualifiche su GAIA. L\'attestato si scarica '
                      f'da GAIA: se non sei già entrato, GAIA ti chiede prima l\'accesso.</p>'
                      f'<div class="lista">{"".join(voci)}</div>')
    else:
        qualifiche = '<p class="vuoto">Qualifiche non disponibili.</p>'

    # --- notifiche e calendario
    notifiche = f"""
<div class="box"><h3 style="margin-top:0">🔔 Notifiche sul telefono</h3>
<div id="stato-notifiche" class="sub">Controllo lo stato delle notifiche…</div>
<button type="button" id="attiva-notifiche" class="bottone grande">Attiva notifiche</button>
<div id="blocco-codice" style="display:none">
<p><b>Ultimo passo:</b> copia questo codice e incollalo su GitHub in un Secret chiamato
<code>PUSH_ISCRIZIONI</code> (Settings → Secrets and variables → Actions).
Se hai già il Secret, aggiungi il codice su una nuova riga.</p>
<textarea id="codice-iscrizione" readonly rows="5"></textarea>
<button type="button" id="copia-codice" class="bottone">Copia codice</button>
</div>
<details><summary class="sub">Come funziona</summary>
<ol class="sub"><li>Sull'iPhone apri questa pagina in <b>Safari</b>, tocca <b>Condividi</b> → <b>Aggiungi alla schermata Home</b>.</li>
<li>Apri il rendiconto <b>dall'icona sulla Home</b>, vieni in questa scheda e tocca <b>Attiva notifiche</b>, poi <b>Consenti</b>.</li>
<li>Copia il codice e mettilo nel Secret <code>PUSH_ISCRIZIONI</code> su GitHub.</li>
<li>Al prossimo aggiornamento arriva la notifica <b>“Notifiche attive”</b>.</li></ol></details>
<p class="sub">Riceverai: turno approvato, richiesta in attesa o non approvata, promemoria il giorno prima e
qualche ora prima del turno, avviso se non sei in regola con i {TURNI_RICHIESTI} turni ogni {MESI_PERIODO} mesi,
nuove qualifiche. Le notifiche arrivano al massimo ogni 3 ore, quando GitHub aggiorna i dati.</p></div>

<div class="box"><h3 style="margin-top:0">📅 Turni nel calendario dell'iPhone</h3>
<p>Tocca il link e conferma con <b>Abbonati</b>: i turni approvati compaiono nel Calendario e si aggiornano da soli.</p>
<p><a class="bottone" data-calendario="{e(file_calendario)}" href="#">Aggiungi al Calendario</a></p>
<details><summary class="sub">Se il link non funziona</summary>
<p class="sub">Impostazioni → Calendario → Account → Aggiungi account → Altro → Aggiungi calendario sottoscritto,
e incolla questo indirizzo:</p><p class="indirizzo" data-calendario-testo="{e(file_calendario)}"></p></details>
<p class="sub">L'indirizzo del calendario è segreto e impossibile da indovinare, ma chi lo conosce può vedere i tuoi turni: non condividerlo.</p></div>
"""

    schede = [
        ("riepilogo", "Riepilogo", riepilogo),
        ("effettuati", f"Effettuati ({len(d['effettuati'])})", effettuati),
        ("dafare", f"Da fare ({len(d['da_fare'])})", da_fare),
        ("sospeso", f"In sospeso ({len(d['in_sospeso'])})", in_sospeso),
        ("disponibili", f"Disponibili ({len(d['disponibili'])})", disponibili),
        ("qualifiche", f"Qualifiche ({len(d['qualifiche'])})", qualifiche),
        ("corsi", f"Corsi ({len(d['corsi'])})", corsi),
        ("notifiche", "🔔 Notifiche", notifiche),
    ]
    radio = "".join(
        f'<input type="radio" name="tab" id="t-{k}"{" checked" if i == 0 else ""}>'
        for i, (k, _, _) in enumerate(schede))
    etichette = "".join(f'<label for="t-{k}">{e(n)}</label>' for k, n, _ in schede)
    pannelli = "".join(f'<section class="pan p-{k}">{c}</section>' for k, _, c in schede)
    css_tab = " ".join(
        f'#t-{k}:checked~nav label[for=t-{k}]{{background:var(--rosso);color:#fff}}'
        f'#t-{k}:checked~main .p-{k}{{display:block}}' for k, _, _ in schede)

    return (f'<style>{CSS_INTERNO}{css_tab}</style>'
            f'<div class="app">{radio}<header><h1>Rendiconto turni CRI</h1>'
            f'<div class="sub">Aggiornato {e(f_data(adesso))}</div></header>'
            f'<nav>{etichette}</nav><main>{pannelli}</main>'
            f'<footer>Dati letti da GAIA. Le "ore reali" contano al massimo {TETTO_ORE_TURNO:g} ore per turno.</footer></div>')


CSS_INTERNO = """
:root{--rosso:#cc0000;--scuro:#1d1d1f;--grigio:#6e6e73;--bordo:#e5e5ea;--sfondo:#f5f5f7}
.app{max-width:960px;margin:0 auto;padding:16px}
.app>input{display:none}
header h1{margin:0;font-size:1.5rem;color:var(--rosso)}
header{margin-bottom:12px}
nav{display:flex;gap:6px;overflow-x:auto;padding-bottom:6px;margin-bottom:14px}
nav label{white-space:nowrap;padding:8px 12px;border-radius:999px;background:#fff;border:1px solid var(--bordo);cursor:pointer;font-size:.9rem}
.pan{display:none}
h3{margin:22px 0 8px;font-size:1.05rem}
h4{margin:18px 0 6px;font-size:.95rem;color:var(--grigio);text-transform:uppercase;letter-spacing:.03em}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.card{background:#fff;border:1px solid var(--bordo);border-radius:14px;padding:14px}
.card .num{font-size:1.8rem;font-weight:700;color:var(--rosso);font-variant-numeric:tabular-nums}
.card .lab{font-weight:600}
.sub{color:var(--grigio);font-size:.85rem}
.box{border-radius:12px;padding:12px 14px;margin-top:12px;background:#fff;border:1px solid var(--bordo)}
.box.evidenza{border-left:4px solid #1a7f37}
.box.avviso{border-left:4px solid #d97706;background:#fffbeb}
.box ul{margin:8px 0 0;padding-left:20px}
details.box summary{cursor:pointer;list-style:none}
details.box summary::-webkit-details-marker{display:none}
details.box summary::before{content:"▸ ";color:var(--grigio)}
details.box[open] summary::before{content:"▾ "}
.regola{margin:0 0 12px;border-left:4px solid}
.regola.ok{border-left-color:#1a7f37;background:#f0faf3}
.regola.attenzione{border-left-color:#d97706;background:#fffbeb}
.regola.allarme{border-left-color:#cc0000;background:#fdecec}
.regola details{margin-top:8px}.regola details summary{cursor:pointer}
.riga-regola{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:4px}
.pallini{display:flex;gap:5px}
.pallino{width:14px;height:14px;border-radius:50%;border:2px solid #bbb;background:#fff}
.pallino.pieno{background:#1a7f37;border-color:#1a7f37}
.pallino.previsto{border-color:#1a7f37;background:#cdebd5}
.scorri{overflow-x:auto}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--bordo);border-radius:12px;overflow:hidden;font-size:.92rem}
th,td{padding:9px 10px;border-bottom:1px solid var(--bordo);text-align:left;vertical-align:top}
th{background:var(--sfondo);font-weight:600}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
tr.tot td{font-weight:700;background:var(--sfondo)}
a{color:inherit}
td a{font-weight:600;text-decoration:none}
.lista{display:flex;flex-direction:column;gap:6px}
.voce{display:grid;grid-template-columns:130px 1fr auto;gap:10px;align-items:start;background:#fff;border:1px solid var(--bordo);border-radius:12px;padding:10px 12px;text-decoration:none}
.voce.mia{border-left:4px solid #1a7f37}
.voce .ora{font-weight:700;font-variant-numeric:tabular-nums;font-size:.88rem;line-height:1.35}
.voce .posti{text-align:right;font-size:.85rem;white-space:nowrap}
.tag{display:inline-block;font-size:.75rem;padding:2px 8px;border-radius:999px;margin-top:3px;font-weight:600}
.tag.rosso{background:#fde8e8;color:#b00000}.tag.verde{background:#e6f4ea;color:#1a7f37}
.tag.giallo{background:#fff4d6;color:#8a5a00}.tag.grigio{background:#eee;color:#555}.tag.blu{background:#e7f0fd;color:#1859b5}
.vuoto{color:var(--grigio);font-style:italic}
.voce.spenta{opacity:.6}
.impegno{margin-top:6px;padding:6px 9px;border-radius:9px;font-size:.82rem;line-height:1.35}
.impegno.rosso{background:#fde8e8;color:#8f0000}.impegno.giallo{background:#fff4d6;color:#6b4600}
.bottone.grande{padding:12px 18px;font-size:1rem;border:0;margin:10px 0;cursor:pointer}
button.bottone{border:0;cursor:pointer;font-family:inherit}
textarea{width:100%;box-sizing:border-box;font-family:monospace;font-size:.75rem;border:1px solid var(--bordo);border-radius:10px;padding:8px;margin:6px 0}
.indirizzo{word-break:break-all;font-family:monospace;font-size:.8rem;background:var(--sfondo);padding:8px;border-radius:8px}
code{background:var(--sfondo);padding:1px 5px;border-radius:5px}
.bottone{display:inline-block;padding:7px 12px;border-radius:10px;background:var(--rosso);color:#fff!important;text-decoration:none;font-weight:600;font-size:.85rem}
footer{margin-top:28px;color:var(--grigio);font-size:.8rem}
@media(max-width:600px){.voce{grid-template-columns:1fr auto}.voce .ora{grid-column:1/-1}}
"""


# ------------------------------------------------------------------ cifratura
def cifra(testo, password):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    sale, iv = os.urandom(16), os.urandom(12)
    chiave = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=sale,
                        iterations=250_000).derive(password.encode())
    dati = AESGCM(chiave).encrypt(iv, testo.encode(), None)
    b64 = lambda b: base64.b64encode(b).decode()
    return {"s": b64(sale), "i": b64(iv), "d": b64(dati)}


PAGINA_ESTERNA = """<!DOCTYPE html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<meta name="apple-mobile-web-app-title" content="Turni CRI">
<meta name="theme-color" content="#cc0000">
<link rel="manifest" href="manifest.webmanifest">
<link rel="apple-touch-icon" href="apple-touch-icon.png">
<link rel="icon" href="icona-192.png">
<title>Rendiconto turni CRI</title>
<style>
body{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#f5f5f7;color:#1d1d1f}
#blocco{max-width:360px;margin:18vh auto;padding:24px;background:#fff;border-radius:18px;border:1px solid #e5e5ea;text-align:center}
#blocco h1{color:#cc0000;font-size:1.3rem;margin:0 0 6px}
#blocco input[type=password]{width:100%;box-sizing:border-box;padding:12px;font-size:1rem;border:1px solid #ccc;border-radius:10px;margin:14px 0 10px}
#blocco button{width:100%;padding:12px;font-size:1rem;border:0;border-radius:10px;background:#cc0000;color:#fff;font-weight:600;cursor:pointer}
#blocco label{font-size:.85rem;color:#6e6e73;display:block;margin-top:10px}
#errore{color:#b00000;font-size:.9rem;min-height:1.2em}
</style></head><body>
<form id="blocco" onsubmit="apri(event)">
<h1>Rendiconto turni CRI</h1><div style="color:#6e6e73;font-size:.9rem">Pagina protetta</div>
<input type="password" id="pw" placeholder="Password" autocomplete="current-password" autofocus>
<button type="submit">Apri</button>
<label><input type="checkbox" id="ricorda"> Ricorda su questo dispositivo</label>
<div id="errore"></div>
</form>
<div id="contenuto"></div>
<script>
const DATI = __DATI__;
const VAPID = "__VAPID__";
const b = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
async function decifra(pw){
  const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(pw), "PBKDF2", false, ["deriveKey"]);
  const key = await crypto.subtle.deriveKey({name:"PBKDF2", salt:b(DATI.s), iterations:250000, hash:"SHA-256"},
    base, {name:"AES-GCM", length:256}, false, ["decrypt"]);
  const out = await crypto.subtle.decrypt({name:"AES-GCM", iv:b(DATI.i)}, key, b(DATI.d));
  return new TextDecoder().decode(out);
}
async function mostra(pw, salva){
  try{
    const h = await decifra(pw);
    document.getElementById("blocco").remove();
    document.getElementById("contenuto").innerHTML = h;
    dopoApertura();
    if(salva){ try{ localStorage.setItem("rendiconto_pw", pw); }catch(e){} }
    return true;
  }catch(e){ return false; }
}
async function apri(ev){
  ev.preventDefault();
  document.getElementById("errore").textContent = "";
  const ok = await mostra(document.getElementById("pw").value, document.getElementById("ricorda").checked);
  if(!ok){ document.getElementById("errore").textContent = "Password sbagliata"; }
}
if ("serviceWorker" in navigator) { navigator.serviceWorker.register("sw.js").catch(()=>{}); }
function apriScheda(){
  const k = (location.hash || "").slice(1);
  const r = k && document.getElementById("t-" + k);
  if (r) { r.checked = true; }
}
window.addEventListener("hashchange", apriScheda);
function chiaveBin(s){
  s = s.replace(/-/g,"+").replace(/_/g,"/"); while (s.length % 4) s += "=";
  return Uint8Array.from(atob(s), c => c.charCodeAt(0));
}
function scrivi(id, t){ const el = document.getElementById(id); if (el) el.textContent = t; }
function mostraCodice(sub){
  document.getElementById("blocco-codice").style.display = "block";
  document.getElementById("codice-iscrizione").value = JSON.stringify(sub);
}
async function statoNotifiche(){
  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
  const installata = window.navigator.standalone === true || matchMedia("(display-mode: standalone)").matches;
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    scrivi("stato-notifiche", ios && !installata
      ? "Per attivare le notifiche, prima aggiungi questa pagina alla schermata Home (Safari → Condividi → Aggiungi alla schermata Home) e aprila da lì."
      : "Questo browser non supporta le notifiche web. Sull'iPhone serve iOS 16.4 o successivo.");
    document.getElementById("attiva-notifiche").style.display = "none";
    return;
  }
  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub && Notification.permission === "granted") {
      scrivi("stato-notifiche", "✔ Notifiche attive su questo dispositivo. Se non l'hai ancora fatto, copia il codice qui sotto nel Secret PUSH_ISCRIZIONI.");
      document.getElementById("attiva-notifiche").textContent = "Mostra di nuovo il codice";
      document.getElementById("attiva-notifiche").dataset.giaAttive = "1";
    } else if (Notification.permission === "denied") {
      scrivi("stato-notifiche", "Le notifiche sono bloccate: riattivale da Impostazioni → Notifiche → Turni CRI.");
    } else {
      scrivi("stato-notifiche", "Le notifiche non sono ancora attive su questo dispositivo.");
    }
  } catch (e) { scrivi("stato-notifiche", "Le notifiche non sono ancora attive su questo dispositivo."); }
}
async function attivaNotifiche(){
  try {
    const permesso = await Notification.requestPermission();
    if (permesso !== "granted") { scrivi("stato-notifiche", "Permesso non concesso: le notifiche restano spente."); return; }
    const reg = await navigator.serviceWorker.ready;
    let sub = await reg.pushManager.getSubscription();
    if (!sub) { sub = await reg.pushManager.subscribe({userVisibleOnly: true, applicationServerKey: chiaveBin(VAPID)}); }
    mostraCodice(sub);
    scrivi("stato-notifiche", "✔ Notifiche attivate su questo dispositivo. Ora copia il codice qui sotto su GitHub.");
  } catch (e) { scrivi("stato-notifiche", "Attivazione non riuscita: " + e.message); }
}
function dopoApertura(){
  apriScheda();
  document.querySelectorAll("[data-calendario]").forEach(a => {
    const u = new URL(a.dataset.calendario, location.href);
    a.href = "webcal://" + u.host + u.pathname;
  });
  document.querySelectorAll("[data-calendario-testo]").forEach(p => {
    const u = new URL(p.dataset.calendarioTesto, location.href);
    p.textContent = "webcal://" + u.host + u.pathname;
  });
  const b = document.getElementById("attiva-notifiche");
  if (b) {
    b.addEventListener("click", async () => {
      if (b.dataset.giaAttive) {
        const reg = await navigator.serviceWorker.ready;
        const sub = await reg.pushManager.getSubscription();
        if (sub) { mostraCodice(sub); return; }
      }
      attivaNotifiche();
    });
    statoNotifiche();
  }
  const c = document.getElementById("copia-codice");
  if (c) c.addEventListener("click", async () => {
    const t = document.getElementById("codice-iscrizione");
    try { await navigator.clipboard.writeText(t.value); c.textContent = "Copiato ✔"; }
    catch (e) { t.select(); document.execCommand("copy"); c.textContent = "Copiato ✔"; }
  });
}
(async()=>{
  let pw = null; try{ pw = localStorage.getItem("rendiconto_pw"); }catch(e){}
  if(pw && !(await mostra(pw,false))){ try{ localStorage.removeItem("rendiconto_pw"); }catch(e){} }
})();
</script></body></html>"""


SERVICE_WORKER = """self.addEventListener("install", e => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));
self.addEventListener("push", e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = {body: e.data ? e.data.text() : ""}; }
  e.waitUntil(self.registration.showNotification(d.title || "Turni CRI", {
    body: d.body || "", icon: "icona-192.png", badge: "icona-192.png", data: {tab: d.tab || ""}
  }));
});
self.addEventListener("notificationclick", e => {
  e.notification.close();
  const url = self.registration.scope + (e.notification.data && e.notification.data.tab ? "#" + e.notification.data.tab : "");
  e.waitUntil(clients.matchAll({type: "window", includeUncontrolled: true}).then(lista => {
    for (const c of lista) { if ("focus" in c) { c.navigate(url).catch(() => {}); return c.focus(); } }
    return clients.openWindow(url);
  }));
});
"""

MANIFEST = {
    "name": "Rendiconto turni CRI", "short_name": "Turni CRI",
    "start_url": "./", "scope": "./", "display": "standalone",
    "background_color": "#f5f5f7", "theme_color": "#cc0000",
    "icons": [{"src": "icona-192.png", "sizes": "192x192", "type": "image/png"},
              {"src": "icona-512.png", "sizes": "512x512", "type": "image/png"}],
}


def disegna_icona(lato):
    """Icona semplice: calendario bianco con spunta su fondo rosso."""
    from PIL import Image, ImageDraw
    k = lato / 512
    img = Image.new("RGB", (lato, lato), (204, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([96 * k, 120 * k, 416 * k, 420 * k], radius=36 * k, fill="white")
    d.rectangle([96 * k, 190 * k, 416 * k, 200 * k], fill=(204, 0, 0))
    for x in (176, 336):
        d.rounded_rectangle([(x - 14) * k, 88 * k, (x + 14) * k, 160 * k], radius=14 * k, fill="white")
    d.line([(176 * k, 310 * k), (236 * k, 368 * k), (346 * k, 250 * k)], fill=(204, 0, 0), width=int(34 * k), joint="curve")
    return img


def scrivi_sito(interno, password, cartella="sito", vapid_pubblica="", calendario=None):
    Path(cartella).mkdir(exist_ok=True)
    pagina = (PAGINA_ESTERNA.replace("__DATI__", json.dumps(cifra(interno, password)))
              .replace("__VAPID__", vapid_pubblica))
    Path(cartella, "index.html").write_text(pagina, encoding="utf-8")
    Path(cartella, "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    Path(cartella, "sw.js").write_text(SERVICE_WORKER, encoding="utf-8")
    Path(cartella, "manifest.webmanifest").write_text(json.dumps(MANIFEST, ensure_ascii=False), encoding="utf-8")
    for nome, lato in (("icona-192.png", 192), ("icona-512.png", 512), ("apple-touch-icon.png", 180)):
        disegna_icona(lato).save(Path(cartella, nome))
    if calendario:
        nome_file, contenuto = calendario
        Path(cartella, nome_file).write_text(contenuto, encoding="utf-8", newline="")


# ------------------------------------------------------------------ GAIA (Playwright)
def scarica_da_gaia(email, password):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pagina = browser.new_page(locale="it-IT", timezone_id="Europe/Rome")
        pagina.set_default_timeout(45_000)

        print("Accesso a GAIA...")
        pagina.goto(f"{GAIA}/login/")
        pagina.fill('input[name="auth-username"]', email)
        pagina.fill('input[name="auth-password"]', password)
        pagina.press('input[name="auth-password"]', "Enter")
        pagina.wait_for_load_state("networkidle")
        if "/login" in pagina.url:
            browser.close()
            sys.exit("ERRORE: accesso a GAIA non riuscito. Controlla GAIA_EMAIL e GAIA_PASSWORD nei Secrets.")
        print("Accesso riuscito.")

        print("Lettura storico e monte ore...")
        pagina.goto(f"{GAIA}/attivita/storico/")
        try:
            pagina.select_option("#id_periodo", "365")
            pagina.click("form:has(#id_periodo) button[type=submit]")
            pagina.wait_for_load_state("networkidle")
        except Exception:
            print("Avviso: pulsante Calcola non trovato, uso le statistiche gia' mostrate.")
        storico = pagina.content()

        print("Lettura calendario turni disponibili...")
        calendari, visti = [], set()
        pagina.goto(f"{GAIA}/attivita/calendario/")
        for n in range(SETTIMANE_CALENDARIO):
            calendari.append(pagina.content())
            visti.add(pagina.url)
            avanti = None
            for a in pagina.query_selector_all('a[href*="/attivita/calendario/"]'):
                interno = (a.inner_html() or "").lower()
                if any(x in interno for x in ("succ", "avanti", "chevron-right", "arrow-right",
                                              "angle-right", "»", "›", "&gt;")):
                    avanti = a.get_attribute("href")
            if not avanti or link_assoluto(avanti) in visti or avanti in visti:
                break
            pagina.goto(link_assoluto(avanti))
        print(f"Settimane di calendario lette: {len(calendari)}")

        print("Lettura corsi aperti...")
        pagina.goto(f"{GAIA}/aspirante/corsi/")
        corsi = pagina.content()

        print("Lettura qualifiche...")
        try:
            pagina.goto(f"{GAIA}/utente/curriculum/TC/")
            qualifiche = pagina.content()
        except Exception:
            print("Avviso: pagina qualifiche non letta.")
            qualifiche = ""
        browser.close()
    return storico, calendari, corsi, qualifiche


def leggi_impegni(calendario, adesso):
    """Eventi del calendario iCloud (o del file di prova). None = calendario non collegato."""
    fine = max([t["fine"] for t in calendario] + [adesso]) + timedelta(days=2)
    if len(sys.argv) >= 3 and sys.argv[1] == "--prova":
        f = Path(sys.argv[2]) / "eventi.json"
        if not f.exists():
            return None
        eventi = []
        for ev in json.loads(f.read_text(encoding="utf-8")):
            eventi.append({"titolo": ev["titolo"], "tutto_giorno": ev.get("tutto_giorno", False),
                           "inizio": datetime.fromisoformat(ev["inizio"]).replace(tzinfo=FUSO),
                           "fine": datetime.fromisoformat(ev["fine"]).replace(tzinfo=FUSO)})
        return eventi
    email, pw = os.environ.get("ICLOUD_EMAIL"), os.environ.get("ICLOUD_PASSWORD_APP")
    if not email or not pw:
        return None
    try:
        import calendario_icloud
        inizio = adesso.replace(hour=0, minute=0, second=0, microsecond=0)
        eventi, n_cal = calendario_icloud.leggi_eventi_icloud(email, pw, inizio, fine)
        print(f"Calendario iCloud: {n_cal} calendari letti, {len(eventi)} eventi.")
        return eventi
    except Exception as errore:
        print(f"Avviso: calendario iCloud non letto ({type(errore).__name__}). Controlla ICLOUD_EMAIL e ICLOUD_PASSWORD_APP.")
        return None


# ------------------------------------------------------------------ avvio
def main():
    adesso = datetime.now(FUSO)
    if len(sys.argv) >= 3 and sys.argv[1] == "--prova":
        cartella = Path(sys.argv[2])
        storico = (cartella / "storico.html").read_text(encoding="utf-8")
        calendari = [(cartella / "calendario.html").read_text(encoding="utf-8")]
        corsi = (cartella / "corsi.html").read_text(encoding="utf-8")
        file_q = cartella / "qualifiche.html"
        qualifiche = file_q.read_text(encoding="utf-8") if file_q.exists() else ""
        if len(sys.argv) >= 4:
            adesso = datetime.strptime(sys.argv[3], "%Y-%m-%d %H:%M").replace(tzinfo=FUSO)
        password_pagina = os.environ.get("PASSWORD_PAGINA", "prova")
    else:
        email = os.environ.get("GAIA_EMAIL")
        password = os.environ.get("GAIA_PASSWORD")
        password_pagina = os.environ.get("PASSWORD_PAGINA")
        mancanti = [n for n, v in (("GAIA_EMAIL", email), ("GAIA_PASSWORD", password),
                                   ("PASSWORD_PAGINA", password_pagina)) if not v]
        if mancanti:
            sys.exit("ERRORE: mancano questi Secrets su GitHub: " + ", ".join(mancanti))
        storico, calendari, corsi, qualifiche = scarica_da_gaia(email, password)

    calendario, visti = [], set()
    for c in calendari:
        for t in leggi_calendario(c, adesso):
            chiave = t["id"] or (t["link"], t["inizio"])
            if chiave not in visti:
                visti.add(chiave)
                calendario.append(t)

    eventi = leggi_impegni(calendario, adesso)
    dati = analizza(leggi_storico(storico), leggi_statistiche(storico),
                    calendario, leggi_corsi(corsi), adesso, leggi_qualifiche(qualifiche), eventi)

    if not dati["effettuati"] and not dati["da_fare"] and not dati["in_sospeso"]:
        sys.exit("ERRORE: nello storico non ho trovato nessun turno. La pagina di GAIA potrebbe essere cambiata.")

    import notifiche
    prova = len(sys.argv) >= 3 and sys.argv[1] == "--prova"
    file_cal = notifiche.nome_calendario(password_pagina)
    _, vapid_pubblica = notifiche.chiavi_vapid(password_pagina)
    scrivi_sito(costruisci_pagina(dati, file_cal), password_pagina, vapid_pubblica=vapid_pubblica,
                calendario=(file_cal, notifiche.crea_calendario(dati, adesso, titolo_breve)))
    notifiche.gestisci_notifiche(
        dati, adesso, password_pagina, titolo_breve,
        file_stato=os.environ.get("FILE_STATO", "stato/stato.json"),
        iscrizioni_testo=os.environ.get("PUSH_ISCRIZIONI", ""),
        contatto="mailto:" + (os.environ.get("GAIA_EMAIL") or "rendiconto@example.com"),
        prova=prova,
        notifica_disponibili=os.environ.get("NOTIFICA_DISPONIBILI", "no").lower() in ("si", "sì", "yes", "1"))
    # Solo numeri nei log (i log di un repository pubblico sono visibili a tutti)
    print(f"Fatto: {len(dati['effettuati'])} effettuati, {len(dati['da_fare'])} da fare, "
          f"{len(dati['in_sospeso'])} in sospeso, {len(dati['disponibili'])} disponibili, "
          f"{len(dati['corsi'])} corsi, {len(dati['qualifiche'])} qualifiche.")


if __name__ == "__main__":
    main()
