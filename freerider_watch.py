#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Hertz Freerider – bevakning av tur & retur (v3.2)
=================================================
Nytt i v3.2: returen behöver INTE starta nära destinationen – det räcker
att den ligger inom RETUR_MAX_KM (du kan ta tåg/buss en bit). Däremot
får returen inte starta nästan hemma (RETUR_MIN_HEM_KM), för då är den
poänglös. Hittade par rankas med närmaste retur först och notisen visar
avståndet mellan destinationen och returens startort.

Skriptet loggar in med ditt Freerider-konto (GitHub Secrets:
FREERIDER_EMAIL och FREERIDER_PASSWORD) och skickar pushnotis via
ntfy.sh när det finns BÅDE:

  * en utresa FRÅN startgruppen (t.ex. Uppsala/Stockholm) till en ort
    var som helst – dock inte till startgruppen själv och inte till
    orter i exkluderingslistan (t.ex. Gävle), och
  * en retur tillbaka till startgruppen som startar inom rimlig
    räckvidd från destinationen – men inte alldeles hemmavid,

med datum som går ihop. Justera allt under KONFIGURATION nedan.
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
EXKLUDERA_MAL = ["gävle"]   # destinationer du INTE vill ha (töm listan om du ångrar dig)
MIN_RESA_KM = 50            # destinationen måste ligga minst så här långt från startgruppen
RETUR_MAX_KM = 70          # returen får starta max så här långt (fågelvägen) från destinationen
RETUR_MIN_HEM_KM = 80       # ... men minst så här långt hemifrån (annars är den poänglös)
MAX_DAGAR_MELLAN = 7        # max dagar mellan utresans sista dag och returens första
NOTIFIERA_ENKELRESA = False  # True = larma även när bara ena riktningen finns (många notiser!)
SIDA = "https://www.hertzfreerider.se/sv-se/"
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
FREERIDER_EMAIL = os.environ.get("FREERIDER_EMAIL", "").strip()
FREERIDER_PASSWORD = os.environ.get("FREERIDER_PASSWORD", "").strip()
STATE_FIL = Path("state.json")
DEBUG_MAPP = Path("debug")
# ===================================================

# Ungefärliga koordinater (lat, lon) för orter där Hertz kan tänkas ha stationer.
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
    "älmhult": (56.55, 14.14), "arboga": (59.39, 15.84), "eslöv": (55.84, 13.30),
    "finspång": (58.71, 15.77), "höganäs": (56.20, 12.56), "hörby": (55.85, 13.66),
    "karlsborg": (58.53, 14.51), "karlskoga": (59.33, 14.52), "kumla": (59.13, 15.14),
    "kungsbacka": (57.49, 12.08), "markaryd": (56.46, 13.60), "olofström": (56.28, 14.53),
    "osby": (56.38, 13.99), "östhammar": (60.26, 18.37), "skara": (58.39, 13.44),
    "sölvesborg": (56.05, 14.58),
}

FRAN_NYCKLAR = ("origin", "from", "pickup", "start", "departure")
TILL_NYCKLAR = ("destination", "dest", "to", "dropoff", "arrival", "return", "end")
DATUM_NYCKLAR = ("date", "time", "from", "to", "start", "end", "until",
                 "available", "valid", "period", "window")
SKRAP_URLER = ("google", "gtm", "gtag", "doubleclick", "facebook", "hotjar",
               "clarity", "cookie", "onetrust", "linkedin", "analytics")

EPOST_FALT = ('#signInName', 'input[type="email"]', 'input[name*="email" i]',
              'input[id*="email" i]', 'input[name*="signin" i]',
              'input[id*="user" i]', 'input[name*="user" i]')
LOSEN_FALT = ('#password', 'input[type="password"]')

# ---------------- Datumtolkning ----------------

MANADER = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "maj": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12}
ISO_RE = re.compile(r"(20\d{2})-(\d{1,2})-(\d{1,2})")
EU_RE = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](20\d{2})\b")
SV_RE = re.compile(r"\b(\d{1,2})\s*(jan|feb|mar|apr|maj|jun|jul|aug|sep|okt|nov|dec)"
                   r"[a-zåäö]*\.?\s*(20\d{2})?", re.I)
NETDATUM_RE = re.compile(r"/Date\((\d{10,13})")


def _epok(n):
    try:
        if n > 1e12:
            return datetime.fromtimestamp(n / 1000, tz=timezone.utc).date()
        if n > 1e9:
            return datetime.fromtimestamp(n, tz=timezone.utc).date()
    except Exception:
        pass
    return None


def _datum_i_strang(s):
    ut = []
    for m in ISO_RE.finditer(s):
        try:
            ut.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except ValueError:
            pass
    for m in EU_RE.finditer(s):
        d_, mo, ar = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if mo > 12 and d_ <= 12:      # amerikansk ordning? byt plats
            d_, mo = mo, d_
        try:
            ut.append(date(ar, mo, d_))
        except ValueError:
            pass
    for m in NETDATUM_RE.finditer(s):
        x = _epok(int(m.group(1)))
        if x:
            ut.append(x)
    idag = date.today()
    for m in SV_RE.finditer(s):
        try:
            d2 = date(int(m.group(3)) if m.group(3) else idag.year,
                      MANADER[m.group(2).lower()[:3]], int(m.group(1)))
        except ValueError:
            continue
        if not m.group(3) and (d2 - idag).days < -60:
            try:
                d2 = d2.replace(year=d2.year + 1)  # "10 jan" i augusti = nästa år
            except ValueError:
                continue
        ut.append(d2)
    return ut


def _alla_datum(v, djup=0):
    """Letar datum rekursivt i strängar, tal, listor och dictar."""
    if djup > 5:
        return []
    ut = []
    if isinstance(v, bool):
        pass
    elif isinstance(v, str):
        ut += _datum_i_strang(v)
    elif isinstance(v, (int, float)):
        x = _epok(v)
        if x:
            ut.append(x)
    elif isinstance(v, dict):
        for k, vv in v.items():
            kl = str(k).lower()
            if any(u in kl for u in ("created", "publish", "updated", "modified")):
                continue  # metadatum, inte resedatum
            ut += _alla_datum(vv, djup + 1)
    elif isinstance(v, (list, tuple)):
        for vv in v:
            ut += _alla_datum(vv, djup + 1)
    return ut


def _rimliga_datum(datumlista):
    """Behåll bara datum som kan vara resedatum (inte gamla id:n m.m.)."""
    idag = date.today()
    return [d for d in datumlista if -60 <= (d - idag).days <= 550]


# ---------------- Geografi ----------------

OKANDA_ORTER = set()


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


def avstand_mellan(namn1, namn2):
    """Fågelavstånd i km mellan två stationsnamn, eller None om okänd ort."""
    o1, o2 = hitta_ort(namn1), hitta_ort(namn2)
    if o1 and o2:
        return _haversine(ORTER[o1], ORTER[o2])
    for namn, ort in ((namn1, o1), (namn2, o2)):
        if not ort:
            OKANDA_ORTER.add(namn)
    return None


def _samma_ortnamn(namn1, namn2):
    """Reserv för okända orter: samma första ord (t.ex. 'Luleå ...' == 'Luleå ...')."""
    t1 = re.split(r"[\s\-–,/]+", namn1.strip().lower())[0]
    t2 = re.split(r"[\s\-–,/]+", namn2.strip().lower())[0]
    return len(t1) >= 3 and t1 == t2


def ortnamn(namn):
    """Kort ortnamn för notiser."""
    o = hitta_ort(namn)
    if o:
        return o.capitalize()
    return re.split(r"[\s\-–,/]+", namn.strip())[0].capitalize()


def min_avstand_till_start(namn):
    o = hitta_ort(namn)
    if not o:
        return None
    avstand = [_haversine(ORTER[o], ORTER[s]) for s in START_GRUPP if s in ORTER]
    return min(avstand) if avstand else None


# ---------------- Hämtning & inloggning ----------------

def _klicka_bort_cookies(page):
    for knapptext in ("Godkänn", "Acceptera", "Tillåt alla", "Jag förstår",
                      "Accept", "Allow all", "OK"):
        try:
            page.get_by_role("button", name=re.compile(knapptext, re.I)).first.click(timeout=1200)
            return
        except Exception:
            continue


def _ser_ut_som_inloggning(page):
    try:
        if page.locator('input[type="password"]').count() > 0:
            return True
    except Exception:
        pass
    u = page.url.lower()
    return "b2clogin" in u or "/login" in u or "signin" in u


def _fyll_falt(page, selektorer, varde):
    for sel in selektorer:
        try:
            falt = page.locator(sel).first
            if falt.count() and falt.is_visible():
                falt.fill(varde, timeout=3000)
                return True
        except Exception:
            continue
    return False


def _klicka_skicka(page):
    for sel in ('#next', 'button[type="submit"]', 'input[type="submit"]'):
        try:
            page.locator(sel).first.click(timeout=3000)
            return True
        except Exception:
            continue
    try:
        page.get_by_role("button", name=re.compile("sign in|logga in|log in", re.I)).first.click(timeout=3000)
        return True
    except Exception:
        return False


def _logga_in(page):
    print("Inloggningssida upptäckt – loggar in ...")
    ok_epost = _fyll_falt(page, EPOST_FALT, FREERIDER_EMAIL)
    ok_losen = _fyll_falt(page, LOSEN_FALT, FREERIDER_PASSWORD)
    if ok_epost and not ok_losen:
        _klicka_skicka(page)
        page.wait_for_timeout(2500)
        ok_losen = _fyll_falt(page, LOSEN_FALT, FREERIDER_PASSWORD)
    if not (ok_epost and ok_losen):
        print("Hittade inte inloggningsfälten.")
        return False
    if not _klicka_skicka(page):
        print("Hittade ingen inloggningsknapp.")
        return False
    try:
        page.wait_for_url(re.compile(r"hertzfreerider\.se"), timeout=30_000)
    except Exception:
        pass
    page.wait_for_timeout(6000)
    inloggad = not _ser_ut_som_inloggning(page)
    print("Inloggningen lyckades." if inloggad else "Verkar fortfarande stå på inloggningssidan.")
    return inloggad


def hamta_sidan():
    """Öppnar sajten, loggar in vid behov.
    Returnerar (json_svar, sidtext, meta, skarmdump, alla_urler)."""
    svar, alla_urler = [], []
    sidtext, skarmdump = "", None
    meta = {"slutlig_url": "", "kravde_inloggning": False, "inloggad": False}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            locale="sv-SE", viewport={"width": 1280, "height": 1000},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"))
        responses = []
        page.on("response", lambda r: responses.append(r))
        page.goto(SIDA, wait_until="load", timeout=60_000)
        _klicka_bort_cookies(page)
        page.wait_for_timeout(6000)

        if _ser_ut_som_inloggning(page):
            meta["kravde_inloggning"] = True
            if FREERIDER_EMAIL and FREERIDER_PASSWORD:
                meta["inloggad"] = _logga_in(page)
                _klicka_bort_cookies(page)
                page.wait_for_timeout(6000)

        for r in responses:
            try:
                url = r.url
                alla_urler.append(f"{r.status} {url[:300]}")
                lurl = url.lower()
                if any(s in lurl for s in SKRAP_URLER):
                    continue
                ct = r.headers.get("content-type", "").lower()
                if "json" not in ct and not lurl.split("?")[0].endswith(".json"):
                    continue
                body = r.text()
                if not body or len(body) > 3_000_000:
                    continue
                svar.append({"url": url, "json": json.loads(body)})
            except Exception:
                continue

        try:
            sidtext = page.inner_text("body")
        except Exception:
            pass
        try:
            skarmdump = page.screenshot(full_page=True)
        except Exception:
            pass
        meta["slutlig_url"] = page.url
        browser.close()
    return svar, sidtext, meta, skarmdump, alla_urler


# ---------------- Tolkning ----------------

def _plats(v):
    """Försöker plocka ut ett stationsnamn ur ett värde."""
    if isinstance(v, str):
        s = v.strip()
        if (2 <= len(s) <= 60 and not s.lower().startswith("http")
                and not ISO_RE.search(s) and not EU_RE.search(s)
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
            datumlista += _alla_datum(d[orig])
    if not datumlista:
        datumlista = _alla_datum(d)
    datumlista = _rimliga_datum(datumlista)

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
    if i_grupp(e["till"], START_GRUPP):
        return False
    if i_grupp(e["till"], EXKLUDERA_MAL):
        return False
    avstand = min_avstand_till_start(e["till"])
    if avstand is not None and avstand < MIN_RESA_KM:
        return False
    return True


def ar_giltig_retur(e):
    if not i_grupp(e["till"], START_GRUPP) or i_grupp(e["fran"], START_GRUPP):
        return False
    hem = min_avstand_till_start(e["fran"])
    if hem is not None and hem < RETUR_MIN_HEM_KM:
        return False  # startar nästan hemma -> poänglös som returbil
    return True


def retur_passar(utresa, retur):
    """Returnerar avstånd destination->returstart i km om paret är okej, annars None."""
    d = avstand_mellan(utresa["till"], retur["fran"])
    if d is None:
        return 0.0 if _samma_ortnamn(utresa["till"], retur["fran"]) else None
    return d if d <= RETUR_MAX_KM else None


def datum_gar_ihop(ut, hem):
    if not ut["start"] or not hem["slut"]:
        return True  # okända datum -> hellre en notis för mycket än en missad bil
    u_forsta = date.fromisoformat(ut["start"])
    u_sista = date.fromisoformat(ut["slut"] or ut["start"])
    h_forsta = date.fromisoformat(hem["start"] or hem["slut"])
    h_sista = date.fromisoformat(hem["slut"])
    if h_sista < u_forsta:
        return False
    if (h_forsta - u_sista).days > MAX_DAGAR_MELLAN:
        return False
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
    state["senast_kord"] = date.today().isoformat()
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


def _engangsnotis(state, nyckel_, titel, meddelande, prio=4):
    if nyckel_ in state["larmade"]:
        return False
    notisa(titel, meddelande, prio=prio)
    state["larmade"].append(nyckel_)
    return True


def skriv_debug(svar, sidtext, alla_urler=None, skarmdump=None, meta=None):
    DEBUG_MAPP.mkdir(exist_ok=True)
    try:
        (DEBUG_MAPP / "captured.json").write_text(
            json.dumps(svar, ensure_ascii=False, indent=1)[:400_000], encoding="utf-8")
    except Exception:
        pass
    (DEBUG_MAPP / "page.txt").write_text((sidtext or "")[:100_000], encoding="utf-8")
    if alla_urler is not None:
        (DEBUG_MAPP / "urls.txt").write_text("\n".join(alla_urler)[:60_000], encoding="utf-8")
    if meta is not None:
        try:
            (DEBUG_MAPP / "meta.json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception:
            pass
    if skarmdump:
        try:
            (DEBUG_MAPP / "screenshot.png").write_bytes(skarmdump)
        except Exception:
            pass


# ---------------- Huvudflöde ----------------

def main():
    forsta_korningen = not STATE_FIL.exists()
    state = las_state()

    svar, sidtext, meta, skarmdump, alla_urler = hamta_sidan()
    print("Slutlig URL:", meta["slutlig_url"],
          "| Krävde inloggning:", meta["kravde_inloggning"],
          "| Inloggad:", meta["inloggad"])

    erbjudanden = []
    for s in svar:
        hitta_alla_erbjudanden(s["json"], erbjudanden)
    erbjudanden = unika(erbjudanden)
    utan_datum = [e for e in erbjudanden if not e["start"]]

    print(f"Hittade {len(erbjudanden)} erbjudanden totalt ({len(utan_datum)} utan datum).")
    for e in erbjudanden:
        print("  -", beskriv(e))

    utresor = [e for e in erbjudanden if ar_giltig_utresa(e)]
    returer = [e for e in erbjudanden if ar_giltig_retur(e)]

    par = []
    for u in utresor:
        for h in returer:
            if not datum_gar_ihop(u, h):
                continue
            d = retur_passar(u, h)
            if d is not None:
                par.append((u, h, d))
    par.sort(key=lambda x: x[2])  # närmaste retur först

    nya_par = [(u, h, d) for (u, h, d) in par
               if f"par:{nyckel(u)}||{nyckel(h)}" not in state["larmade"]]

    if OKANDA_ORTER:
        print("Orter som saknas i koordinattabellen:", ", ".join(sorted(OKANDA_ORTER)))

    if nya_par:
        rader = []
        for u, h, d in nya_par[:5]:
            if d < 20:
                avst = "retur från samma ort"
            else:
                avst = f"retur ~{int(round(d, -1))} km från {ortnamn(u['till'])}"
            rader.append(f"UT:  {beskriv(u)}\nHEM: {beskriv(h)}\n({avst})")
            state["larmade"].append(f"par:{nyckel(u)}||{nyckel(h)}")
        extra = f"\n\n... och {len(nya_par) - 5} par till – se sajten!" if len(nya_par) > 5 else ""
        notisa("🚗 Tur & retur hittad på Freerider!",
               "\n\n".join(rader) + extra + "\n\nFörst till kvarn – boka direkt!")

    if NOTIFIERA_ENKELRESA:
        for e in utresor + returer:
            k = f"enkel:{nyckel(e)}"
            if k not in state["larmade"]:
                notisa("🚗 Enkelresa på din sträcka", beskriv(e), prio=4)
                state["larmade"].append(k)

    # ---- Status-/kalibreringsnotiser (max en gång per situation) ----
    ny_debug = False
    if meta["kravde_inloggning"] and not (FREERIDER_EMAIL and FREERIDER_PASSWORD):
        ny_debug = _engangsnotis(
            state, "behover-login",
            "🔐 Freerider kräver nu inloggning",
            "Sajten visar inte erbjudandena utan konto. Använd ditt konto på "
            "hertzfreerider.se och lägg till två secrets i GitHub-repot "
            "(Settings → Secrets and variables → Actions → New repository secret): "
            "FREERIDER_EMAIL och FREERIDER_PASSWORD. Sedan loggar bevakningen in själv.")
    elif meta["kravde_inloggning"] and not meta["inloggad"]:
        ny_debug = _engangsnotis(
            state, "login-misslyckades",
            "⚠️ Kunde inte logga in på Freerider",
            "Kontrollera att secrets FREERIDER_EMAIL och FREERIDER_PASSWORD stämmer. "
            "Om de är rätt: ny debuginfo finns i repots debug-mapp – klistra in den "
            "till Claude så justeras inloggningen.")
    elif not erbjudanden and meta["inloggad"]:
        ny_debug = _engangsnotis(
            state, "kalibrering-efter-login",
            "🔧 Inloggad – men listan kan inte tolkas ännu",
            "Inloggningen funkar! Nu behövs sista kalibreringen: kolla debug-mappen "
            "i repot (med urls.txt, meta.json och screenshot.png) och klistra in "
            "innehållet till Claude. OBS: filerna kan visa din inloggade vy – "
            "radera mappen när kalibreringen är klar.")
    elif erbjudanden and len(utan_datum) > len(erbjudanden) // 2:
        ny_debug = _engangsnotis(
            state, "datum-saknas",
            "🔧 Resorna hittas – men inte datumen",
            "Från/till tolkas rätt, men datumformatet känns inte igen än, därför står "
            "det 'datum okänt'. Färsk debuginfo är sparad i repots debug-mapp – säg "
            "till Claude så hämtas den och datumtolkningen fixas. Tills dess larmas "
            "par utan datumkontroll (hellre en notis för mycket än en missad bil). "
            "OBS: debugfilerna kan visa din inloggade vy.")
    elif not erbjudanden and len(sidtext or "") > 500:
        ny_debug = _engangsnotis(
            state, "kalibrering",
            "⚠️ Freerider-bevakningen hittar inga erbjudanden",
            "Antingen är listan tom just nu, eller så behöver sidtolkningen kalibreras. "
            "Kolla debug-mappen i ditt GitHub-repo och klistra in innehållet till Claude.")

    if ny_debug or (not erbjudanden and not (DEBUG_MAPP / "captured.json").exists()):
        skriv_debug(svar, sidtext, alla_urler=alla_urler, skarmdump=skarmdump, meta=meta)

    # ---- Återhämtning: datumen börjar tolkas ----
    if (erbjudanden and "datum-saknas" in state["larmade"]
            and len(utan_datum) <= len(erbjudanden) // 2):
        state["larmade"].remove("datum-saknas")
        notisa("✅ Datumtolkningen fungerar nu",
               "Datum följer med i notiserna framöver. Samma resor kan larmas en gång "
               "till – nu med datum. Radera gärna debug-mappen i repot.", prio=3)

    # ---- Återhämtning: tolkningen som helhet börjar fungera ----
    flaggor = {"kalibrering", "kalibrering-efter-login", "behover-login", "login-misslyckades"}
    if erbjudanden and flaggor & set(state["larmade"]):
        state["larmade"] = [k for k in state["larmade"] if k not in flaggor]
        notisa("✅ Kalibreringen är klar!",
               f"Bevakningen tolkar nu sajten korrekt: {len(erbjudanden)} erbjudanden, "
               f"varav {len(utresor)} möjliga utresor och {len(returer)} returer just nu. "
               "Radera gärna debug-mappen i repot (den kan innehålla din inloggade vy).",
               prio=3)

    if forsta_korningen:
        if erbjudanden:
            notisa("✅ Freerider-bevakningen är igång",
                   f"Allt funkar! Just nu ser jag {len(erbjudanden)} erbjudanden på sajten, "
                   f"varav {len(utresor)} möjliga utresor från din startgrupp och "
                   f"{len(returer)} returer hem.", prio=3)
        else:
            notisa("✅ Bevakningen är igång",
                   "Pushnotiserna funkar! Följ instruktionerna i eventuella notiser ovan "
                   "om något mer behöver ställas in.", prio=3)

    spara_state(state)
    print("Klart.")


if __name__ == "__main__":
    main()
