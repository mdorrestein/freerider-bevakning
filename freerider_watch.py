#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hertz Freerider – bevakning av tur & retur
==========================================
Hämtar alla aktuella erbjudanden på hertzfreerider.se och skickar en
pushnotis via ntfy.sh när det finns BÅDE:

  * en utresa FRÅN startgruppen (t.ex. Uppsala/Stockholm) till en ort
    var som helst – dock inte till startgruppen själv och inte till
    orter i exkluderingslistan (t.ex. Gävle), och
  * en retur som startar NÄRA den destinationen (fågelavstånd, se
    RETUR_MAX_KM) och går tillbaka till startgruppen,

med datum som går ihop. Justera allt under KONFIGURATION nedan.
Ortmatchningen är "innehåller", så "stockholm" täcker även
"Stockholm City" och "Stockholm-Arlanda Flygplats".
"""

import json
import math
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

# ================== KONFIGURATION ==================
START_GRUPP = ["uppsala", "stockholm", "arlanda", "bromma"]  # där du startar och slutar
EXKLUDERA_MAL = ["gävle"]   # destinationer du INTE vill ha (tas bort ur listan om du ångrar dig)
MIN_RESA_KM = 50            # destinationen måste ligga minst så här långt från startgruppen
RETUR_MAX_KM = 100          # returen får starta max så här långt (fågelvägen) från destinationen
MAX_DAGAR_MELLAN = 7        # max dagar mellan utresans sista dag och returens första
NOTIFIERA_ENKELRESA = False  # True = larma även när bara ena riktningen finns (kan bli många notiser!)
SIDA = "https://www.hertzfreerider.se/sv-se/"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
STATE_FIL = Path("state.json")
DEBUG_MAPP = Path("debug")
# ===================================================

# Ungefärliga koordinater (lat, lon) för orter där Hertz kan tänkas ha stationer.
# Används för att avgöra vad som är "nära". Saknas en ort loggas den, och
# matchningen faller då tillbaka på att ortnamnen måste vara samma.
ORTER = {
    "stockholm": (59.33, 18.06), "arlanda": (59.65, 17.93), "bromma": (59.35, 17.94),
    "märsta": (59.62, 17.85), "södertälje": (59.20, 17.63), "norrtälje": (59.76, 18.70),
    "uppsala": (59.86, 17.64), "enköping": (59.64, 17.08), "nynäshamn": (58.90, 17.95),
    "nyköping": (58.75, 17.01), "skavsta": (58.79, 16.91), "katrineholm": (59.00, 16.20),
    "västerås": (59.61, 16.55), "eskilstuna": (59.37, 16.51), "örebro": (59.27, 15.21),
    "karlstad": (59.38, 13.50), "arvika": (59.65, 12.59), "kristinehamn": (59.31, 14.11),
    "filipstad": (59.71, 14.17), "torsby": (60.14, 13.00), "säffle": (59.13, 12.93),
    "åmål": (59.05, 12.70), "gävle": (60.67, 17.14), "sandviken": (60.62, 16.78),
    "falun": (60.61, 15.63), "borlänge": (60.48, 15.43), "avesta": (60.15, 16.17),
    "ludvika": (60.15, 15.19), "mora": (61.00, 14.54), "sälen": (61.16, 13.26),
    "idre": (61.86, 12.72), "hudiksvall": (61.73, 17.11), "söderhamn": (61.30, 17.06),
    "bollnäs": (61.35, 16.39), "ljusdal": (61.83, 16.09), "sveg": (62.03, 14.35),
    "sundsvall": (62.39, 17.31), "timrå": (62.49, 17.33), "härnösand": (62.63, 17.94),
    "kramfors": (62.93, 17.78), "sollefteå": (63.17, 17.27), "örnsköldsvik": (63.29, 18.72),
    "östersund": (63.18, 14.64), "åre": (63.40, 13.08), "strömsund": (63.85, 15.55),
    "umeå": (63.83, 20.26), "lycksele": (64.60, 18.67), "vilhelmina": (64.62, 16.66),
    "skellefteå": (64.75, 20.95), "arvidsjaur": (65.59, 19.17), "piteå": (65.32, 21.48),
    "luleå": (65.58, 22.15), "kallax": (65.54, 22.12), "boden": (65.83, 21.69),
    "kalix": (65.85, 23.13), "haparanda": (65.83, 24.14), "gällivare": (67.13, 20.66),
    "kiruna": (67.86, 20.23), "göteborg": (57.71, 11.97), "landvetter": (57.67, 12.29),
    "borås": (57.72, 12.94), "alingsås": (57.93, 12.53), "trollhättan": (58.28, 12.29),
    "vänersborg": (58.38, 12.32), "uddevalla": (58.35, 11.94), "strömstad": (58.94, 11.17),
    "lysekil": (58.27, 11.44), "kungälv": (57.87, 11.98), "varberg": (57.11, 12.25),
    "falkenberg": (56.90, 12.49), "halmstad": (56.67, 12.86), "helsingborg": (56.05, 12.69),
    "ängelholm": (56.24, 12.86), "landskrona": (55.87, 12.83), "lund": (55.70, 13.19),
    "malmö": (55.60, 13.00), "sturup": (55.55, 13.37), "trelleborg": (55.38, 13.16),
    "ystad": (55.43, 13.82), "simrishamn": (55.56, 14.35), "kristianstad": (56.03, 14.16),
    "hässleholm": (56.16, 13.77), "karlshamn": (56.17, 14.86), "ronneby": (56.21, 15.28),
    "karlskrona": (56.16, 15.59), "växjö": (56.88, 14.81), "alvesta": (56.90, 14.56),
    "ljungby": (56.83, 13.94), "värnamo": (57.19, 14.04), "gislaved": (57.30, 13.54),
    "jönköping": (57.78, 14.16), "nässjö": (57.65, 14.70), "eksjö": (57.67, 14.97),
    "vetlanda": (57.43, 15.08), "tranås": (58.04, 14.98), "oskarshamn": (57.27, 16.45),
    "västervik": (57.76, 16.64), "vimmerby": (57.67, 15.86), "kalmar": (56.66, 16.36),
    "nybro": (56.74, 15.91), "emmaboda": (56.63, 15.54), "visby": (57.64, 18.30),
    "linköping": (58.41, 15.62), "norrköping": (58.59, 16.19), "motala": (58.54, 15.04),
    "mjölby": (58.33, 15.13), "skövde": (58.39, 13.85), "lidköping": (58.50, 13.16),
    "mariestad": (58.71, 13.82), "falköping": (58.17, 13.55), "ulricehamn": (57.79, 13.42),
}

DATUM_RE = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")

FRAN_NYCKLAR = ("origin", "from", "pickup", "start", "departure")
TILL_NYCKLAR = ("destination", "dest", "to", "dropoff", "arrival", "return", "end")
DATUM_NYCKLAR = ("date", "time", "from", "to", "start", "end", "until", "available", "valid")
SKRAP_URLER = ("google", "gtm", "gtag", "doubleclick", "facebook", "hotjar",
               "clarity", "cookie", "onetrust", "linkedin", "analytics")


# ---------------- Geografi ----------------

def hitta_ort(namn):
    """Matchar ett stationsnamn mot ORTER. Längsta träff vinner."""
    n = namn.lower()
    battre = None
    for ort in ORTER:
        if ort in n and (battre is None or len(ort) > len(battre)):
            battre = ort
    return battre


def _haversine(p1, p2):
    lat1, lon1, lat2, lon2 = map(math.radians, (*p1, *p2))
    a = (math.sin((lat2 - lat1) / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2)
    return 2 * 6371 * math.asin(math.sqrt(a))


OKANDA_ORTER = set()


def ar_nara(namn1, namn2, max_km):
    """Ligger två stationer inom max_km fågelvägen från varandra?"""
    o1, o2 = hitta_ort(namn1), hitta_ort(namn2)
    if o1 and o2:
        return _haversine(ORTER[o1], ORTER[o2]) <= max_km
    for namn, ort in ((namn1, o1), (namn2, o2)):
        if not ort:
            OKANDA_ORTER.add(namn)
    # Okänd ort: kräv att första ordet i namnen är samma (t.ex. "Luleå ..." == "Luleå ...")
    t1 = re.split(r"[\s\-–,/]+", namn1.strip().lower())[0]
    t2 = re.split(r"[\s\-–,/]+", namn2.strip().lower())[0]
    return len(t1) >= 3 and t1 == t2


def min_avstand_till_start(namn):
    o = hitta_ort(namn)
    if not o:
        return None
    avstand = [_haversine(ORTER[o], ORTER[s]) for s in START_GRUPP if s in ORTER]
    return min(avstand) if avstand else None


# ---------------- Hämtning ----------------

def hamta_sidan():
    """Öppnar sajten i en headless webbläsare. Returnerar (json_svar, sidtext)."""
    svar = []
    sidtext = ""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(locale="sv-SE", viewport={"width": 1280, "height": 1000})
        responses = []
        page.on("response", lambda r: responses.append(r))
        page.goto(SIDA, wait_until="load", timeout=60_000)

        # Klicka bort ev. cookie-ruta (bästa försök – gör inget om den saknas)
        for knapptext in ("Godkänn", "Acceptera", "Tillåt alla", "Jag förstår", "OK"):
            try:
                page.get_by_role("button", name=re.compile(knapptext, re.I)).first.click(timeout=1200)
                break
            except Exception:
                pass

        page.wait_for_timeout(8000)  # låt erbjudandelistan ladda klart

        for r in responses:
            try:
                url = r.url.lower()
                if any(s in url for s in SKRAP_URLER):
                    continue
                ct = r.headers.get("content-type", "").lower()
                if "json" not in ct and not url.split("?")[0].endswith(".json"):
                    continue
                body = r.text()
                if not body or len(body) > 3_000_000:
                    continue
                svar.append({"url": r.url, "json": json.loads(body)})
            except Exception:
                continue

        try:
            sidtext = page.inner_text("body")
        except Exception:
            pass
        browser.close()
    return svar, sidtext


# ---------------- Tolkning ----------------

def _plats(v):
    """Försöker plocka ut ett stationsnamn ur ett värde."""
    if isinstance(v, str):
        s = v.strip()
        if (2 <= len(s) <= 60 and not s.lower().startswith("http")
                and not DATUM_RE.search(s)
                and not s.replace(":", "").replace("-", "").replace(" ", "").isdigit()):
            return s
        return None
    if isinstance(v, dict):
        for nyckel in ("name", "city", "title", "label", "station", "location", "address"):
            if isinstance(v.get(nyckel), str):
                s = _plats(v[nyckel])
                if s:
                    return s
        for vv in v.values():
            s = _plats(vv)
            if s:
                return s
    return None


def _datum(v):
    if isinstance(v, str):
        m = DATUM_RE.search(v)
        if m:
            try:
                return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                return None
    if isinstance(v, (int, float)):
        try:
            if v > 1e12:
                return datetime.fromtimestamp(v / 1000, tz=timezone.utc).date()
            if v > 1e9:
                return datetime.fromtimestamp(v, tz=timezone.utc).date()
        except Exception:
            return None
    return None


def _tolka_erbjudande(d):
    """Ser det här ut som ett erbjudande (från + till + ev. datum)?"""
    if not isinstance(d, dict):
        return None
    nycklar = {k.lower(): k for k in d if isinstance(k, str)}

    def hitta_plats(hintar):
        for lag, orig in nycklar.items():
            if any(h in lag for h in hintar) and "date" not in lag and "time" not in lag:
                s = _plats(d[orig])
                if s:
                    return s
        return None

    fran = hitta_plats(FRAN_NYCKLAR)
    till = hitta_plats(TILL_NYCKLAR)
    if not fran or not till or fran.lower() == till.lower():
        return None

    datumlista = []
    for lag, orig in nycklar.items():
        if any(h in lag for h in DATUM_NYCKLAR):
            v = d[orig]
            if isinstance(v, (list, tuple)):
                datumlista += [x for x in (_datum(i) for i in v) if x]
            else:
                x = _datum(v)
                if x:
                    datumlista.append(x)
    if not datumlista:
        for v in d.values():
            x = _datum(v)
            if x:
                datumlista.append(x)

    return {
        "fran": fran,
        "till": till,
        "start": min(datumlista).isoformat() if datumlista else None,
        "slut": max(datumlista).isoformat() if datumlista else None,
    }


def hitta_alla_erbjudanden(obj, ut):
    if isinstance(obj, list):
        for it in obj:
            hitta_alla_erbjudanden(it, ut)
    elif isinstance(obj, dict):
        e = _tolka_erbjudande(obj)
        if e:
            ut.append(e)
        else:
            for v in obj.values():
                hitta_alla_erbjudanden(v, ut)


def unika(erbjudanden):
    sedda, res = set(), []
    for e in erbjudanden:
        n = (e["fran"].lower(), e["till"].lower(), e["start"], e["slut"])
        if n not in sedda:
            sedda.add(n)
            res.append(e)
    return res


# ---------------- Matchning ----------------

def i_grupp(namn, grupp):
    n = namn.lower()
    return any(g in n for g in grupp)


def ar_giltig_utresa(e):
    if not i_grupp(e["fran"], START_GRUPP):
        return False
    if i_grupp(e["till"], START_GRUPP):        # inte tillbaka till hemmagruppen
        return False
    if i_grupp(e["till"], EXKLUDERA_MAL):      # inte t.ex. Gävle
        return False
    avstand = min_avstand_till_start(e["till"])
    if avstand is not None and avstand < MIN_RESA_KM:  # inga korta småhopp
        return False
    return True


def ar_giltig_retur(e):
    return i_grupp(e["till"], START_GRUPP) and not i_grupp(e["fran"], START_GRUPP)


def datum_gar_ihop(ut, hem):
    if not ut["start"] or not hem["slut"]:
        return True  # okända datum -> hellre en notis för mycket än en missad bil
    u_forsta = date.fromisoformat(ut["start"])
    u_sista = date.fromisoformat(ut["slut"] or ut["start"])
    h_forsta = date.fromisoformat(hem["start"] or hem["slut"])
    h_sista = date.fromisoformat(hem["slut"])
    if h_sista < u_forsta:
        return False  # returen stänger innan du ens hunnit fram
    if (h_forsta - u_sista).days > MAX_DAGAR_MELLAN:
        return False  # för lång väntan på plats
    return True


def beskriv(e):
    if e["start"] and e["slut"] and e["start"] != e["slut"]:
        dat = f'{e["start"]} – {e["slut"]}'
    else:
        dat = e["start"] or "datum okänt"
    return f'{e["fran"]} → {e["till"]} ({dat})'


def nyckel(e):
    return f'{e["fran"]}|{e["till"]}|{e["start"]}|{e["slut"]}'.lower()


# ---------------- State, notiser, debug ----------------

def las_state():
    if STATE_FIL.exists():
        try:
            return json.loads(STATE_FIL.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"larmade": [], "senast_kord": None}


def spara_state(state):
    state["larmade"] = state["larmade"][-300:]
    state["senast_kord"] = date.today().isoformat()  # daglig "puls" håller repot aktivt
    STATE_FIL.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def notisa(titel, meddelande, prio=5):
    print(f"NOTIS: {titel} | {meddelande}")
    if not NTFY_TOPIC:
        print("(NTFY_TOPIC saknas – ingen push skickad)")
        return
    try:
        requests.post(
            "https://ntfy.sh/",
            json={"topic": NTFY_TOPIC, "title": titel, "message": meddelande,
                  "priority": prio, "click": SIDA, "tags": ["red_car"]},
            timeout=20,
        )
    except Exception as fel:
        print("Kunde inte skicka notis:", fel)


def skriv_debug(svar, sidtext):
    DEBUG_MAPP.mkdir(exist_ok=True)
    try:
        (DEBUG_MAPP / "captured.json").write_text(
            json.dumps(svar, ensure_ascii=False, indent=1)[:400_000], encoding="utf-8")
    except Exception:
        pass
    (DEBUG_MAPP / "page.txt").write_text((sidtext or "")[:100_000], encoding="utf-8")


# ---------------- Huvudflöde ----------------

def main():
    forsta_korningen = not STATE_FIL.exists()
    state = las_state()

    svar, sidtext = hamta_sidan()

    erbjudanden = []
    for s in svar:
        hitta_alla_erbjudanden(s["json"], erbjudanden)
    erbjudanden = unika(erbjudanden)

    print(f"Hittade {len(erbjudanden)} erbjudanden totalt.")
    for e in erbjudanden:
        print("  -", beskriv(e))

    utresor = [e for e in erbjudanden if ar_giltig_utresa(e)]
    returer = [e for e in erbjudanden if ar_giltig_retur(e)]

    par = [(u, h) for u in utresor for h in returer
           if ar_nara(u["till"], h["fran"], RETUR_MAX_KM) and datum_gar_ihop(u, h)]
    nya_par = [(u, h) for (u, h) in par
               if f"par:{nyckel(u)}||{nyckel(h)}" not in state["larmade"]]

    if OKANDA_ORTER:
        print("Orter som saknas i koordinattabellen:", ", ".join(sorted(OKANDA_ORTER)))

    if nya_par:
        rader = [f"UT:  {beskriv(u)}\nHEM: {beskriv(h)}" for u, h in nya_par[:5]]
        notisa("🚗 Tur & retur hittad på Freerider!",
               "\n\n".join(rader) + "\n\nFörst till kvarn – boka direkt!")
        for u, h in nya_par:
            state["larmade"].append(f"par:{nyckel(u)}||{nyckel(h)}")

    if NOTIFIERA_ENKELRESA:
        for e in utresor + returer:
            k = f"enkel:{nyckel(e)}"
            if k not in state["larmade"]:
                notisa("🚗 Enkelresa på din sträcka", beskriv(e), prio=4)
                state["larmade"].append(k)

    # Om tolkningen inte hittade några erbjudanden alls: spara debugmaterial och
    # säg till EN gång (listan kan förstås också vara tom på riktigt).
    if not erbjudanden:
        behover_saga_till = ("kalibrering" not in state["larmade"]
                             and len(sidtext or "") > 500)
        if behover_saga_till or not (DEBUG_MAPP / "captured.json").exists():
            skriv_debug(svar, sidtext)
        if behover_saga_till:
            notisa("⚠️ Freerider-bevakningen hittar inga erbjudanden",
                   "Antingen är listan tom just nu, eller så behöver sidtolkningen "
                   "kalibreras. Kolla debug-mappen i ditt GitHub-repo och klistra in "
                   "innehållet till Claude så justeras tolkningen.", prio=4)
            state["larmade"].append("kalibrering")

    if forsta_korningen:
        if erbjudanden:
            notisa("✅ Freerider-bevakningen är igång",
                   f"Allt funkar! Just nu ser jag {len(erbjudanden)} erbjudanden på sajten, "
                   f"varav {len(utresor)} möjliga utresor från din startgrupp och "
                   f"{len(returer)} returer hem.", prio=3)
        else:
            notisa("✅ Bevakningen är igång – men behöver ev. kalibreras",
                   "Pushnotiserna funkar! Skriptet kunde dock inte tolka erbjudandelistan ännu. "
                   "Kolla debug-mappen i ditt GitHub-repo och klistra in innehållet till Claude "
                   "så justeras tolkningen.", prio=3)

    spara_state(state)
    print("Klart.")


if __name__ == "__main__":
    main()
