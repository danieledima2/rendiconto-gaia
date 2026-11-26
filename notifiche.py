"""
Notifiche web (Web Push) e calendario per l'iPhone.

- Le chiavi VAPID vengono ricavate da PASSWORD_PAGINA: nessun Secret in piu'.
- Le iscrizioni dei telefoni stanno nel Secret PUSH_ISCRIZIONI (una per riga).
- Lo stato (cosa e' gia' stato notificato) sta in stato/stato.json, conservato
  tra un avvio e l'altro dalla cache di GitHub Actions.
"""

import base64
import hashlib
import hmac
import json
import os
import struct
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CURVA = ec.SECP256R1()
ORDINE_CURVA = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def da_b64u(s):
    s = s.strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _hkdf(salt, ikm, info, lunghezza):
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:lunghezza]


def pubblica_raw(chiave_privata):
    return chiave_privata.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


# ------------------------------------------------------------------ chiavi
def chiavi_vapid(password_pagina):
    """Chiave VAPID fissa, ricavata dalla password della pagina."""
    seme = hashlib.pbkdf2_hmac("sha256", password_pagina.encode(), b"rendiconto-gaia-vapid", 100_000)
    numero = int.from_bytes(seme, "big") % (ORDINE_CURVA - 1) + 1
    privata = ec.derive_private_key(numero, CURVA)
    return privata, b64u(pubblica_raw(privata))


def nome_calendario(password_pagina):
    h = hashlib.sha256(("calendario:" + password_pagina).encode()).hexdigest()[:24]
    return f"turni-{h}.ics"


# ------------------------------------------------------------------ Web Push (RFC 8291 + RFC 8292)
def cifra_messaggio(testo, p256dh, auth, effimera=None, sale=None):
    ua_pub = da_b64u(p256dh)
    segreto_auth = da_b64u(auth)
    effimera = effimera or ec.generate_private_key(CURVA)
    as_pub = pubblica_raw(effimera)
    ua_chiave = ec.EllipticCurvePublicKey.from_encoded_point(CURVA, ua_pub)
    ecdh = effimera.exchange(ec.ECDH(), ua_chiave)
    ikm = _hkdf(segreto_auth, ecdh, b"WebPush: info\x00" + ua_pub + as_pub, 32)
    sale = sale or os.urandom(16)
    cek = _hkdf(sale, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(sale, ikm, b"Content-Encoding: nonce\x00", 12)
    cifrato = AESGCM(cek).encrypt(nonce, testo + b"\x02", None)
    intestazione = sale + struct.pack(">I", 4096) + bytes([len(as_pub)]) + as_pub
    return intestazione + cifrato


def firma_vapid(privata, endpoint, contatto):
    u = urlparse(endpoint)
    testata = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    dati = b64u(json.dumps({"aud": f"{u.scheme}://{u.netloc}", "exp": int(time.time()) + 12 * 3600,
                            "sub": contatto}, separators=(",", ":")).encode())
    da_firmare = f"{testata}.{dati}".encode()
    r, s = decode_dss_signature(privata.sign(da_firmare, ec.ECDSA(hashes.SHA256())))
    firma = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return f"{testata}.{dati}.{b64u(firma)}"


def invia_push(iscrizione, messaggio, privata, contatto):
    """Ritorna il codice HTTP (201 = consegnata, 404/410 = iscrizione non piu' valida)."""
    corpo = cifra_messaggio(json.dumps(messaggio).encode(),
                            iscrizione["keys"]["p256dh"], iscrizione["keys"]["auth"])
    richiesta = urllib.request.Request(iscrizione["endpoint"], data=corpo, method="POST", headers={
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": "86400",
        "Urgency": "high",
        "Authorization": f"vapid t={firma_vapid(privata, iscrizione['endpoint'], contatto)}, "
                         f"k={b64u(pubblica_raw(privata))}",
    })
    try:
        with urllib.request.urlopen(richiesta, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as err:
        return err.code
    except Exception:
        return 0


def leggi_iscrizioni(testo):
    iscrizioni = []
    for riga in (testo or "").replace("}{", "}\n{").splitlines():
        riga = riga.strip()
        if not riga:
            continue
        try:
            dato = json.loads(riga)
            if dato.get("endpoint") and dato.get("keys", {}).get("p256dh"):
                iscrizioni.append(dato)
        except json.JSONDecodeError:
            pass
    return iscrizioni


# ------------------------------------------------------------------ cosa notificare
def _quando(t):
    giorni = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]
    return f'{giorni[t["inizio"].weekday()]} {t["inizio"]:%d/%m} {t["inizio"]:%H:%M}–{t["fine"]:%H:%M}'


def _descr(t, titolo_breve):
    return f'{_quando(t)} · {titolo_breve(t["turno"])} ({t["attivita"]})'


def _descr_completa(t, titolo_breve, portare=True):
    """Descrizione del turno con meteo, ora di partenza e (facoltativo) cosa portare."""
    righe = [_descr(t, titolo_breve)]
    try:
        import extra
        dettagli = []
        if t.get("meteo"):
            dettagli.append(extra.testo_meteo(t["meteo"]))
        if t.get("partenza"):
            dettagli.append(f'parti alle {t["partenza"]:%H:%M}')
        if dettagli:
            righe.append(" · ".join(dettagli))
        if portare and t.get("portare"):
            speciali = [x for x in t["portare"] if x not in ("Divisa completa", "Tesserino CRI",
                                                             "Scarpe antinfortunistiche", "Guanti monouso")]
            if speciali:
                righe.append("Porta: " + ", ".join(speciali[:5]).lower())
    except Exception:
        pass
    return "\n".join(righe)


def prepara_notifiche(dati, stato, adesso, titolo_breve, notifica_disponibili=False):
    """Confronta i dati con lo stato precedente e ritorna (messaggi, nuovo_stato)."""
    primo_avvio = not stato
    s = {k: dict(v) if isinstance(v, dict) else v for k, v in stato.items()}
    for k in ("approvati", "attesa", "promemoria_giorno", "promemoria_ora", "qualifiche", "disponibili"):
        s.setdefault(k, {})
    msg = []
    ora = adesso.isoformat()

    def aggiungi(titolo, testo, scheda):
        msg.append({"title": titolo, "body": testo, "tab": scheda})

    da_fare = {t["id"]: t for t in dati["da_fare"] if t["id"]}
    attesa = {t["id"]: t for t in dati["in_sospeso"] if t["id"]}

    for i, t in da_fare.items():
        if i not in s["approvati"]:
            aggiungi("✅ Turno approvato", _descr(t, titolo_breve), "dafare")
            s["approvati"][i] = ora
    for i, t in attesa.items():
        if i not in s["attesa"]:
            aggiungi("⏳ Richiesta in attesa", _descr(t, titolo_breve), "sospeso")
            s["attesa"][i] = ora
    for i in list(s["attesa"]):
        if i not in attesa:
            if i not in da_fare and not any(t["id"] == i for t in dati["effettuati"]):
                aggiungi("❌ Richiesta non approvata o ritirata",
                         "Una tua richiesta di partecipazione non è più in attesa.", "sospeso")
            del s["attesa"][i]

    for i, t in da_fare.items():
        mancano = (t["inizio"] - adesso).total_seconds() / 3600
        if 0 < mancano <= 4 and i not in s["promemoria_ora"]:
            aggiungi("🚑 Turno fra poco", _descr_completa(t, titolo_breve, portare=False), "dafare")
            s["promemoria_ora"][i] = ora
            s["promemoria_giorno"][i] = ora
        elif 4 < mancano <= 24 and i not in s["promemoria_giorno"]:
            quando = "Oggi" if t["inizio"].date() == adesso.date() else "Domani"
            aggiungi(f"📅 {quando} sei di turno", _descr_completa(t, titolo_breve), "dafare")
            s["promemoria_giorno"][i] = ora

    r = dati["regola"]
    chiave_periodo = r["nome"]
    precedente = s.get("regola_livello")
    ultimo_avviso = s.get("regola_avviso")
    if r["livello"] == "allarme":
        scaduto = (not ultimo_avviso or
                   adesso - datetime.fromisoformat(ultimo_avviso) > timedelta(days=7))
        if precedente != "allarme" or s.get("regola_periodo") != chiave_periodo or scaduto:
            aggiungi("⚠ Regola dei turni", r["messaggio"], "riepilogo")
            s["regola_avviso"] = ora
    elif r["livello"] == "ok" and precedente in ("allarme", "attenzione") and s.get("regola_periodo") == chiave_periodo:
        aggiungi("✔ Sei in regola", f'{r["nome"]}: {r["messaggio"]}', "riepilogo")
    s["regola_livello"] = r["livello"]
    s["regola_periodo"] = chiave_periodo

    for q in dati.get("qualifiche", []):
        k = f'{q["nome"]}|{q["data"]:%Y-%m-%d}' if q["data"] else q["nome"]
        if k not in s["qualifiche"]:
            aggiungi("🎓 Nuova qualifica su GAIA", q["nome"], "qualifiche")
            s["qualifiche"][k] = ora

    gruppi = {"comitato": (0,), "regione": (0, 1), "tutti": (0, 1, 2)}.get(
        os.environ.get("NOTIFICA_CORSI", "regione").lower(), ())
    prima_volta_corsi = "corsi" not in s
    s.setdefault("corsi", {})
    for c in dati.get("corsi", []):
        chiave = c["link"] or c["nome"] + c["codice"]
        if chiave in s["corsi"]:
            continue
        s["corsi"][chiave] = ora
        if not prima_volta_corsi and c["gruppo"] in gruppi and not c["iniziato"]:
            dove = c["sede"].replace("Comitato ", "").replace("di ", "", 1) if c["sede"] else ""
            quando = f'inizia {c["inizio"]:%d/%m}' if c["inizio"] else ""
            aggiungi(f"🎓 Nuovo corso · {dove}".strip(" ·"),
                     f'{c["nome"]}' + (f" — {quando}" if quando else "") + f' · {c["richieste"]} richieste',
                     "corsi")

    nuovi_scoperti = []
    for t in dati["disponibili"]:
        if t["id"] and t["id"] not in s["disponibili"]:
            s["disponibili"][t["id"]] = ora
            if t["scoperto"] and not t["mio"]:
                nuovi_scoperti.append(t)
    if notifica_disponibili and nuovi_scoperti:
        testo = "; ".join(f'{_quando(t)} {t["attivita"]}' for t in nuovi_scoperti[:4])
        if len(nuovi_scoperti) > 4:
            testo += f" e altri {len(nuovi_scoperti) - 4}"
        aggiungi(f"🙋 {len(nuovi_scoperti)} nuovi turni senza volontari", testo, "disponibili")

    # pulizia: dimentico le voci piu' vecchie di 120 giorni
    limite = adesso - timedelta(days=120)
    for k in ("approvati", "promemoria_giorno", "promemoria_ora", "disponibili", "corsi"):
        s[k] = {i: v for i, v in s[k].items() if datetime.fromisoformat(v) > limite}

    if primo_avvio:
        msg = []  # al primo avvio registro tutto senza mandare raffiche di notifiche
    return msg, s


def gestisci_notifiche(dati, adesso, password_pagina, titolo_breve, file_stato,
                       iscrizioni_testo, contatto, prova=False, notifica_disponibili=False):
    file_stato = Path(file_stato)
    stato = json.loads(file_stato.read_text()) if file_stato.exists() else {}
    messaggi, nuovo = prepara_notifiche(dati, stato, adesso, titolo_breve, notifica_disponibili)

    iscrizioni = leggi_iscrizioni(iscrizioni_testo)
    conosciute = set(nuovo.get("iscrizioni_note", []))
    for isc in iscrizioni:
        impronta = hashlib.sha256(isc["endpoint"].encode()).hexdigest()[:16]
        if impronta not in conosciute:
            messaggi.insert(0, {"title": "🔔 Notifiche attive",
                                "body": "Da ora riceverai qui gli avvisi dei tuoi turni CRI.",
                                "tab": "riepilogo", "solo": impronta})
            conosciute.add(impronta)
    nuovo["iscrizioni_note"] = sorted(conosciute)

    if len(messaggi) > 6:  # niente raffiche: le prime 5 e un riassunto
        resto = len(messaggi) - 5
        messaggi = messaggi[:5] + [{"title": "Altri aggiornamenti",
                                    "body": f"Ci sono altri {resto} avvisi: apri il rendiconto.",
                                    "tab": "riepilogo"}]

    inviati = 0
    if prova:
        for m in messaggi:
            print(f"[notifica di prova] {m['title']} — {m['body']}")
    elif iscrizioni and messaggi:
        privata, _ = chiavi_vapid(password_pagina)
        for isc in iscrizioni:
            impronta = hashlib.sha256(isc["endpoint"].encode()).hexdigest()[:16]
            for m in messaggi:
                if m.get("solo") and m["solo"] != impronta:
                    continue
                codice = invia_push(isc, {k: v for k, v in m.items() if k != "solo"}, privata, contatto)
                if codice in (200, 201, 202):
                    inviati += 1
                elif codice in (404, 410):
                    print("Avviso: un telefono non e' piu' iscritto alle notifiche (riattivale dalla pagina).")
                    break
                else:
                    print(f"Avviso: invio notifica non riuscito (codice {codice}).")
    file_stato.parent.mkdir(parents=True, exist_ok=True)
    file_stato.write_text(json.dumps(nuovo, ensure_ascii=False, indent=1))
    # solo numeri nei log
    print(f"Notifiche: {len(messaggi)} da inviare, {inviati} consegnate, {len(iscrizioni)} telefoni iscritti.")


# ------------------------------------------------------------------ calendario .ics
def _ics_testo(s):
    return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _ics_data(d):
    return d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def crea_calendario(dati, adesso, titolo_breve):
    righe = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Rendiconto GAIA//IT",
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:Turni CRI",
             "X-WR-TIMEZONE:Europe/Rome", "REFRESH-INTERVAL;VALUE=DURATION:PT3H",
             "X-PUBLISHED-TTL:PT3H"]
    recenti = [t for t in dati["effettuati"] if t["inizio"] > adesso - timedelta(days=90)]
    voci = [(t, "CONFIRMED") for t in dati["da_fare"] + recenti] + \
           [(t, "TENTATIVE") for t in dati["in_sospeso"]]
    for t, stato in voci:
        titolo = titolo_breve(t["turno"])
        if t["attivita"] and t["attivita"].lower() not in titolo.lower():
            titolo = f'{t["attivita"]} – {titolo}'
        if stato == "TENTATIVE":
            titolo = "(in attesa) " + titolo
        righe += ["BEGIN:VEVENT",
                  f'UID:turno-{t["id"] or _ics_data(t["inizio"])}@rendiconto-gaia',
                  f"DTSTAMP:{_ics_data(adesso)}",
                  f'DTSTART:{_ics_data(t["inizio"])}',
                  f'DTEND:{_ics_data(t["fine"])}',
                  f"SUMMARY:{_ics_testo('CRI · ' + titolo)}",
                  f"STATUS:{stato}",
                  f'URL:{t["link"]}',
                  f"DESCRIPTION:{_ics_testo('Turno su GAIA: ' + t['link'])}",
                  "END:VEVENT"]
    righe.append("END:VCALENDAR")
    # righe lunghe spezzate come vuole lo standard (75 byte)
    uscita = []
    for r in righe:
        while len(r.encode()) > 75:
            taglio = 74
            while len(r[:taglio].encode()) > 74:
                taglio -= 1
            uscita.append(r[:taglio])
            r = " " + r[taglio:]
        uscita.append(r)
    return "\r\n".join(uscita) + "\r\n"
