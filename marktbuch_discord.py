# -*- coding: utf-8 -*-
"""
Marktbuch für Discord: holt Preise vom Albion Online Data Project (Europa) und pflegt eine
Live-Nachricht per Webhook in #markt-und-handel. Zur Minute 7 jeder Stunde kommt ein neuer Post
(der vorherige wird gelöscht, damit immer nur der aktuelle unten im Kanal steht), zur Minute 37
wird dieser Post mit frischen Preisen bearbeitet.

    python marktbuch_discord.py            Rohstoffe T2-T8 (mit Verzauberung) und Luxuswaren
    python marktbuch_discord.py --alle     zusätzlich alle Kategorien aus marktbuch_waren.json (langsam)
    python marktbuch_discord.py --neu      neuen Post absetzen und den vorherigen löschen (stündlich)

Inhalt der Nachricht:
  🔁 Top-Flips:              günstigste Verkaufsorder in Stadt A kaufen, in Stadt B per Verkaufsorder
                             verkaufen, abzüglich 6,5 % Steuer (Premium). Nur frische Preise (< 8 h).
  🏷️ Günstigste Stadt T4-T6: je Rohstoffgruppe, wo man am billigsten einkauft.
  💎 Luxuswaren-Alarm:       Marktpreis deutlich unter dem festen Händlerpreis der Zielstadt.

Benötigt in .env: MARKTBUCH_WEBHOOK (Webhook-URL des Kanals). Nutzt marktbuch_abruf.py aus dem
Projektordner (Warenliste, Anfragen, Städte).
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    BERLIN = ZoneInfo("Europe/Berlin")  # der Cloud-Rechner läuft in UTC, angezeigt wird deutsche Zeit
except Exception:  # Windows ohne Paket tzdata: dann die Zeitzone des Rechners nehmen (hier ohnehin Berlin)
    BERLIN = datetime.now().astimezone().tzinfo

try:
    from dotenv import load_dotenv
except ImportError:  # in der Cloud kommen die Werte aus der Umgebung, python-dotenv ist dort nicht nötig
    def load_dotenv(*_a, **_k):
        return False

sys.stdout.reconfigure(encoding="utf-8")
HIER = Path(__file__).resolve().parent
# Lokal liegt marktbuch_abruf.py im Projektordner darüber, im Cloud-Repo direkt daneben.
PROJEKT = HIER if (HIER / "marktbuch_abruf.py").exists() else HIER.parent
sys.path.insert(0, str(PROJEKT))
import marktbuch_abruf as ab  # noqa: E402

load_dotenv(HIER / ".env")
WEBHOOK = os.getenv("MARKTBUCH_WEBHOOK", "")
STATE = HIER / "marktbuch_discord_state.json"
LOG = HIER / "marktbuch_discord.log"

STEUER = 0.065          # Verkaufsorder mit Premium: 4 % Steuer + 2,5 % Einstellgebühr
FRISCH = timedelta(hours=8)
MIN_GEWINN = 500        # Silber je Stück
MIN_MARGE = 0.15
MAX_MARGE = 2.00        # darüber ist es fast immer eine einzelne Ausreißer-Order, nicht handelbar
LUX_MARGE = 0.20
FLIP_SPERRE = {"ROCK", "STONEBLOCK"}   # Stein hat kaum Umsatz, die Preise sind fast immer Ausreißer

STADT_NAME = {v: k for k, v in ab.CITIES.items()}
STADT_KURZ = {"Lymhurst": "Lymhurst", "Fort Sterling": "Fort Sterling", "Martlock": "Martlock",
              "Bridgewatch": "Bridgewatch", "Thetford": "Thetford",
              "caerleon": "Caerleon", "any": "jede Stadt", "?": "?"}
STADT_NAME.update({"caerleon": "caerleon", "any": "any"})  # Luxuswaren-Zielorte außerhalb der fünf Städte
ROHSTOFF = {"FIBER": "Fasern", "HIDE": "Häute", "WOOD": "Holz", "ORE": "Erz", "ROCK": "Stein",
            "CLOTH": "Stoff", "LEATHER": "Leder", "PLANKS": "Bretter", "METALBAR": "Barren",
            "STONEBLOCK": "Steinblöcke"}


def log(text):
    zeile = f"{datetime.now(BERLIN):%Y-%m-%d %H:%M:%S}  {text}"
    print(zeile)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(zeile + "\n")


def silber(n):
    n = int(round(n))
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M".replace(".", ",")
    if n >= 10_000:
        return f"{n/1000:.1f}k".replace(".", ",")
    return f"{n:,}".replace(",", ".")


def name_res(item_id):
    """T4_ORE_LEVEL2@2 -> Erz T4.2"""
    base, _, ench = item_id.partition("@")
    teile = base.split("_")
    tier, gruppe = teile[0], teile[1]
    n = f"{ROHSTOFF.get(gruppe, gruppe)} {tier}"
    return n + (f".{ench}" if ench else "")


def waren(alle):
    """item_id -> (Anzeigename, Art, Zusatz). Art: res, lux oder sonst."""
    out = {i: (name_res(i), "res", None) for i in ab.base_items()}
    daten = json.loads((PROJEKT / "marktbuch_waren.json").read_text("utf-8"))
    for eintrag in daten:
        base, name, sub, _tier, _w, max_e, *extra = eintrag
        art = "lux" if sub.startswith("l_") else "sonst"
        if art == "sonst" and not alle:
            continue
        zusatz = extra[0] if extra else None
        for e in range(max_e + 1):
            iid = f"{base}@{e}" if e else base
            out[iid] = (name + (f" .{e}" if e else ""), art, zusatz)
    return out


def preise_holen(ids):
    """item_id -> stadt -> {sell, sell_t, buy, buy_t}"""
    urls = list(ab.chunks(list(ids)))
    rows = []
    for i, url in enumerate(urls, 1):
        print(f"  Anfrage {i} von {len(urls)}", file=sys.stderr)
        rows += ab.fetch(url)
    jetzt = datetime.now(timezone.utc)
    out = {}
    for r in rows:
        if r["city"] not in ab.CITIES:
            continue
        e = {}
        sd, bd = ab.parse(r["sell_price_min_date"]), ab.parse(r["buy_price_max_date"])
        if r["sell_price_min"] and sd and jetzt - sd < FRISCH:
            e["sell"], e["sell_t"] = r["sell_price_min"], sd
        if r["buy_price_max"] and bd and jetzt - bd < FRISCH:
            e["buy"], e["buy_t"] = r["buy_price_max"], bd
        if e:
            out.setdefault(r["item_id"], {})[r["city"]] = e
    return out


def gruppe_von(item_id):
    """T4_ORE_LEVEL2@2 -> ORE, T4_MAIN_SWORD -> MAIN_SWORD"""
    teile = item_id.partition("@")[0].split("_")
    return "_".join(teile[1:]).replace("_LEVEL", "").rstrip("0123456789") or teile[0]


def flips(preise, info, n=5):
    kandidaten = []
    for iid, staedte in preise.items():
        if iid not in info or info[iid][1] == "lux":
            continue
        if info[iid][1] == "res" and gruppe_von(iid) in FLIP_SPERRE:
            continue
        verkauf = {s: e["sell"] for s, e in staedte.items() if "sell" in e}
        if len(verkauf) < 2:
            continue
        a, pa = min(verkauf.items(), key=lambda kv: kv[1])
        b, pb = max(verkauf.items(), key=lambda kv: kv[1])
        gewinn = pb * (1 - STEUER) - pa
        marge = gewinn / pa if pa else 0
        if gewinn >= MIN_GEWINN and MIN_MARGE <= marge <= MAX_MARGE:
            kandidaten.append((gewinn, marge, info[iid][0], a, pa, b, pb, gruppe_von(iid)))
    # Nach Silber-Gewinn je Stück sortieren (nicht nach Marge, sonst gewinnen Nischenwaren mit
    # einzelnen Ausreißer-Orders) und höchstens ein Eintrag je Rohstoffgruppe, damit die Liste
    # nicht fünfmal dieselbe Ware in verschiedenen Stufen zeigt.
    kandidaten.sort(reverse=True)
    zeilen, gesehen = [], set()
    for gewinn, marge, name, a, pa, b, pb, gruppe in kandidaten:
        if gruppe in gesehen:
            continue
        gesehen.add(gruppe)
        zeilen.append(f"**{name}** · {STADT_KURZ[a]} {silber(pa)} → {STADT_KURZ[b]} {silber(pb)} "
                      f"· **+{silber(gewinn)}** ({marge*100:.0f} %)")
        if len(zeilen) >= n:
            break
    return zeilen or ["Keine Flips mit mindestens 15 % Marge und frischen Preisen."]


def guenstigste(preise, info):
    zeilen = []
    for gruppe in ab.RAW + ab.REFINED:
        teile = []
        for t in (4, 5, 6):
            iid = f"T{t}_{gruppe}"
            verkauf = {s: e["sell"] for s, e in preise.get(iid, {}).items() if "sell" in e}
            if verkauf:
                s, p = min(verkauf.items(), key=lambda kv: kv[1])
                teile.append(f"T{t} {STADT_KURZ[s][:4]} {silber(p)}")
            else:
                teile.append(f"T{t} –")
        zeilen.append(f"**{ROHSTOFF[gruppe]}**: " + " · ".join(teile))
    return zeilen


def luxus(preise, info, n=5):
    kandidaten = []
    for iid, (name, art, zusatz) in info.items():
        if art != "lux" or not zusatz or iid not in preise:
            continue
        fest, ziel = zusatz.get("fest"), STADT_NAME.get(zusatz.get("stadt"), "?")
        verkauf = {s: e["sell"] for s, e in preise[iid].items() if "sell" in e}
        if not fest or not verkauf:
            continue
        s, p = min(verkauf.items(), key=lambda kv: kv[1])
        gewinn = fest - p
        if gewinn / fest >= LUX_MARGE:
            kandidaten.append((gewinn / fest, gewinn, name, s, p, ziel, fest))
    kandidaten.sort(reverse=True)
    zeilen = [f"**{name}** · {STADT_KURZ[s]} {silber(p)} → Händler {STADT_KURZ[ziel]} {silber(fest)} "
              f"· **+{silber(gewinn)}** ({m*100:.0f} %)" for m, gewinn, name, s, p, ziel, fest in kandidaten[:n]]
    return zeilen or ["Kein Luxusgut aktuell 20 % unter Händlerpreis."]


def kuerzen(zeilen, grenze=1000):
    text = ""
    for z in zeilen:
        if len(text) + len(z) + 1 > grenze:
            break
        text += z + "\n"
    return text.strip() or "–"


def embed_bauen(preise, info, alle):
    jetzt = datetime.now(BERLIN)
    anz_frisch = sum(1 for st in preise.values() for e in st.values() if "sell" in e)
    return {
        "title": "📊 Marktbuch · Europe",
        "description": f"Stand **{jetzt:%d.%m. %H:%M} Uhr** · günstigste Verkaufsorder je Stadt · "
                       f"{anz_frisch} frische Preise (< 8 h)" + (" · alle Kategorien" if alle else ""),
        "color": 0xE8B923,
        "fields": [
            {"name": "🔁 Top-Flips je Stück (Kaufen → Verkaufen, nach 6,5 % Steuer, sortiert nach Gewinn)", "value": kuerzen(flips(preise, info))},
            {"name": "🏷️ Günstigste Stadt je Rohstoff", "value": kuerzen(guenstigste(preise, info))},
            {"name": "💎 Luxuswaren-Alarm (Markt → fester Händlerpreis)", "value": kuerzen(luxus(preise, info))},
        ],
        "footer": {"text": "Quelle: Albion Online Data Project · neuer Post jede Stunde, Zwischenstand zur "
                           "halben Stunde · Preise sind Momentaufnahmen, vor dem Kauf im Spiel prüfen"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def senden(embed, neu):
    if not WEBHOOK:
        raise SystemExit("MARKTBUCH_WEBHOOK fehlt in .env")
    state = json.loads(STATE.read_text("utf-8")) if STATE.exists() else {}
    body = json.dumps({"embeds": [embed], "username": "Marktbuch"}).encode()
    H = {"Content-Type": "application/json", "User-Agent": "DLU-Marktbuch/1.0"}
    mid = state.get("message_id")
    if mid and not neu:
        req = urllib.request.Request(f"{WEBHOOK}/messages/{mid}", method="PATCH", headers=H, data=body)
        try:
            with urllib.request.urlopen(req, timeout=30):
                return "bearbeitet", mid
        except urllib.error.HTTPError as err:
            if err.code != 404:
                raise
            log("Alte Nachricht nicht mehr da, poste neu.")
    req = urllib.request.Request(WEBHOOK + "?wait=true", method="POST", headers=H, data=body)
    with urllib.request.urlopen(req, timeout=30) as r:
        neue_id = json.load(r)["id"]
    STATE.write_text(json.dumps({"message_id": neue_id}), "utf-8")
    if mid and mid != neue_id:
        # Erst posten, dann den alten Post löschen: so steht nie gar keine Nachricht im Kanal.
        req = urllib.request.Request(f"{WEBHOOK}/messages/{mid}", method="DELETE", headers=H)
        try:
            with urllib.request.urlopen(req, timeout=30):
                pass
        except urllib.error.HTTPError as err:
            if err.code != 404:
                log(f"Alten Post {mid} nicht gelöscht: HTTP {err.code}")
    return "neu gepostet", neue_id


def main():
    alle, neu = "--alle" in sys.argv, "--neu" in sys.argv
    info = waren(alle)
    log(f"Start: {len(info)} Waren" + (" (alle Kategorien)" if alle else ""))
    try:
        preise = preise_holen(info.keys())
    except RuntimeError as err:
        log(f"Abbruch: {err}")
        sys.exit(1)
    embed = embed_bauen(preise, info, alle)
    was, mid = senden(embed, neu)
    log(f"Nachricht {was} (ID {mid}), {len(preise)} Waren mit Preisen")


if __name__ == "__main__":
    main()
