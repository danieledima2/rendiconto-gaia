"""
Lettura del calendario iCloud (CalDAV) per segnalare i giorni in cui sei gia' impegnato.

Secrets necessari su GitHub:
  ICLOUD_EMAIL         -> l'email del tuo ID Apple
  ICLOUD_PASSWORD_APP  -> una "password specifica per app" creata su account.apple.com
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

FUSO = ZoneInfo("Europe/Rome")


def _a_datetime(valore, fine=False):
    """Converte DTSTART/DTEND di un evento in datetime con fuso; dice se e' 'tutto il giorno'."""
    if isinstance(valore, datetime):
        if valore.tzinfo is None:
            valore = valore.replace(tzinfo=FUSO)
        return valore.astimezone(FUSO), False
    if isinstance(valore, date):
        return datetime.combine(valore, time(0, 0), tzinfo=FUSO), True
    return None, False


def evento_da_componente(comp):
    """Da un VEVENT (icalendar) a un dizionario semplice; None se da ignorare."""
    titolo = str(comp.get("SUMMARY", "") or "(senza titolo)").strip()
    uid = str(comp.get("UID", "") or "")
    # i turni CRI messi nel calendario da questa stessa app non contano come impegni
    if uid.endswith("@rendiconto-gaia") or titolo.startswith("CRI · "):
        return None
    if str(comp.get("STATUS", "")).upper() == "CANCELLED":
        return None
    if str(comp.get("TRANSP", "")).upper() == "TRANSPARENT":  # evento segnato come "libero"
        return None
    if "DTSTART" not in comp:
        return None
    inizio, tutto_giorno = _a_datetime(comp["DTSTART"].dt)
    if "DTEND" in comp:
        fine, _ = _a_datetime(comp["DTEND"].dt)
    elif "DURATION" in comp:
        fine = inizio + comp["DURATION"].dt
    else:
        fine = inizio + (timedelta(days=1) if tutto_giorno else timedelta(0))
    if inizio is None or fine is None:
        return None
    return {"titolo": titolo, "inizio": inizio, "fine": fine, "tutto_giorno": tutto_giorno}


def leggi_eventi_icloud(email, password_app, inizio, fine):
    """Legge gli eventi di tutti i calendari iCloud tra inizio e fine (ricorrenze espanse)."""
    import caldav
    client = caldav.DAVClient(url="https://caldav.icloud.com/", username=email, password=password_app)
    eventi, calendari = [], 0
    for cal in client.principal().calendars():
        try:
            trovati = cal.search(start=inizio, end=fine, event=True, expand=True)
        except Exception:
            continue  # calendari che non contengono eventi (es. promemoria)
        calendari += 1
        for oggetto in trovati:
            try:
                componenti = [c for c in oggetto.icalendar_instance.walk("VEVENT")]
            except Exception:
                continue
            for comp in componenti:
                ev = evento_da_componente(comp)
                if ev and ev["fine"] > inizio and ev["inizio"] < fine:
                    eventi.append(ev)
    return eventi, calendari


def conflitti(turno, eventi):
    """Ritorna (livello, eventi_del_giorno): 'sovrapposto', 'stesso_giorno' oppure None."""
    giorno_ini = datetime.combine(turno["inizio"].date(), time(0, 0), tzinfo=FUSO)
    giorno_fin = giorno_ini + timedelta(days=1)
    del_giorno, sovrapposto = [], False
    for ev in eventi:
        if ev["inizio"] < max(giorno_fin, turno["fine"]) and ev["fine"] > giorno_ini:
            del_giorno.append(ev)
            if not ev["tutto_giorno"] and ev["inizio"] < turno["fine"] and ev["fine"] > turno["inizio"]:
                sovrapposto = True
    if not del_giorno:
        return None, []
    del_giorno.sort(key=lambda ev: (not ev["tutto_giorno"], ev["inizio"]))
    return ("sovrapposto" if sovrapposto else "stesso_giorno"), del_giorno


def descrivi(ev):
    if ev["tutto_giorno"]:
        return f'tutto il giorno · {ev["titolo"]}'
    return f'{ev["inizio"]:%H:%M}–{ev["fine"]:%H:%M} · {ev["titolo"]}'
