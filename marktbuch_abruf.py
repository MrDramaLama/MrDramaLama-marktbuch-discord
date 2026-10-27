"""Holt Preise vom Albion Online Data Project (Europa) und baut Schreibpakete fürs Marktbuch.

Aufruf (im Projektordner):
  python -I marktbuch_abruf.py <dump-ordner> <ausgabe-ordner> [versionen] [seit]

<dump-ordner> wird seit Datenschema 2 nicht mehr gebraucht (bleibt für alte Aufrufe erhalten, darf leer sein).
[versionen]   z. B. "markt/ly=2,meta/abruf=5,verlauf/CLOTH~20261027=3" (aktuelle Versionen bestehender Docs;
              die verlauf-Versionen per ArtifactData query collection "verlauf", where tag >= erster Tag).
[seit]        ISO-Zeit des letzten Abrufs (meta/abruf.ts); Verlauf ab 3 Stunden davor (Überlappung fängt
              verspätet gemeldete Preise ein), ohne Angabe die letzten 3 Stunden.

Schreibt (Datenschema 2, steht in meta/abruf.schema):
  markt/<stadt>          jüngster Preis je Ware einer Stadt, als Map items[ware] = {p, pt, b, bt}
  meta/abruf             Zeitpunkt und Zählung des Abrufs
  verlauf/<GRUPPE>~<JJJJMMTT>  Verlauf je Warengruppe und Tag (UTC): {v: 2, gruppe, tag, s: {ware: {stadt:
                         {"HHMM" (UTC): verkauf}}}, b: {… kauf}}, nur vorhandene Werte. Bestehende Docs per "update"
                         (fügt verschachtelt hinzu), neue per "set". Derselbe Punkt darf mehrfach geschrieben werden.
                         (Die ersten Docs vom 24.–27.10.2026 tragen zusätzlich p: {…: [verkauf, kauf]}, Schema v 1.)
Gibt ArtifactData-batch-Listen (je höchstens 50 Schreibvorgänge) mit file_path aus.
Die Warenliste ist dieselbe wie im Marktbuch: 10 Rohstoff- und Veredelungsgruppen T2-T8, Verzauberung bis .3,
dazu alle Kategorien aus marktbuch_waren.json (neben diesem Skript), seit 2026-10-20 auch die 21 Luxuswaren (lux; ihr siebtes Feld {reise, stadt, fest} ist nur für die Seite). Ressourcen liegen in markt/<stadt>,
jede andere Hauptkategorie in markt/<stadt>-<kategorie>. Verlauf nur für die 10 Gruppen und die Avalonischen Splitter (SHARD).
"""
import gzip, json, sys, time, urllib.error, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

CITIES = {"Lymhurst": "ly", "Fort Sterling": "fs", "Martlock": "ma", "Bridgewatch": "bw", "Thetford": "th"}
RAW = ["FIBER", "HIDE", "WOOD", "ORE", "ROCK"]
REFINED = ["CLOTH", "LEATHER", "PLANKS", "METALBAR", "STONEBLOCK"]
MAX_AGE = timedelta(days=7)
API = "https://europe.albion-online-data.com/api/v2/stats/prices/"
MAX_URL = 4000  # api-docs des Datenprojekts: Adresse unter 4.096 Zeichen halten
SCHEMA = 2      # 2 = Verlauf gebündelt in verlauf/<GRUPPE>~<JJJJMMTT> (vorher obs, ein Doc je Punkt)


MAINS = {  # Unterkategorie -> Hauptkategorie, wie MAINS im Marktbuch
    "res": "alchemy fish",
    "art": "a_weapons a_offhands a_head a_armors a_shoes a_capes a_fragments a_favor a_faction",
    "verb": "food potions",
    "farm": "farm herbgarden pasture kennel farmingproducts",
    "waf": "sword axe mace hammer knuckles crossbow bow spear dagger quarterstaff shapeshifterstaff firestaff holystaff "
           "arcanestaff froststaff cursestaff naturestaff",
    "neb": "shieldtype booktype torchtype",
    "rue": "cloth_helmet cloth_armor cloth_shoes leather_helmet leather_armor leather_shoes plate_helmet plate_armor plate_shoes",
    "zub": "bags satchels capes",
    "reit": "basemounts battle_mount",
    "sam": "g_fiber g_hide g_ore g_rock g_wood g_fish g_tracking",
    "lux": "l_knowledge l_silverware l_decorative l_ceremonial l_tribal l_ritual l_avalon",
}
SUBMAIN = {s: m for m, subs in MAINS.items() for s in subs.split()}


def base_items():
    out = []
    for cat in RAW + REFINED:
        for t in range(2, 9):
            out.append(f"T{t}_{cat}")
            if t >= 4 and cat != "STONEBLOCK":
                out += [f"T{t}_{cat}_LEVEL{e}@{e}" for e in (1, 2, 3)]
    return out


def items():
    """Ware -> Hauptkategorie, mit Verlauf-Kennzeichen für die 10 Gruppen und die Avalonischen Splitter."""
    out = {i: ("res", True) for i in base_items()}
    for base, _name, sub, _tier, _w, max_e, *_extra in json.loads(Path(__file__).with_name("marktbuch_waren.json").read_text("utf-8")):
        for e in range(max_e + 1):
            out[f"{base}@{e}" if e else base] = (SUBMAIN[sub], base.endswith("_SHARD_AVALONIAN"))
    return out


def chunks(ids):
    tail = ".json?" + urllib.parse.urlencode({"locations": ",".join(CITIES), "qualities": "1"})
    part = []
    for i in ids:
        if part and len(API + ",".join(part + [i]) + tail) > MAX_URL:
            yield API + ",".join(part) + tail
            part = []
        part.append(i)
    if part:
        yield API + ",".join(part) + tail


def fetch(url):
    """Eine Anfrage mit kurzer Pause davor. Bei 429 (zu viele Anfragen), Serverfehlern (5xx),
    Netzfehlern oder unlesbarer Antwort warten und wiederholen, höchstens 6 Versuche.
    Laut api-docs erlaubt das Datenprojekt mit gzip 100 Anfragen je Minute und 300 je 5 Minuten,
    ohne Kompression nur 1 je 5 Minuten; 0,7 s Pause hält uns sicher unter 100 je Minute."""
    for attempt in range(6):
        time.sleep(0.7)
        req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip", "User-Agent": "Marktbuch"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                rows = json.loads(body)
                if not isinstance(rows, list):
                    raise ValueError("Antwort ist keine Liste")
                return rows
        except urllib.error.HTTPError as err:
            if err.code == 429:
                wait = 15 * (attempt + 1)
            elif err.code >= 500:
                wait = 5 * (attempt + 1)
            else:
                raise
            grund = f"HTTP {err.code}"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as err:
            wait = 5 * (attempt + 1)
            grund = str(err)
        if attempt == 5:
            raise RuntimeError(f"Datenprojekt antwortet nicht ({grund}). Später noch einmal versuchen.")
        print(f"  Versuch {attempt + 1} fehlgeschlagen ({grund}), warte {wait} s", file=sys.stderr)
        time.sleep(wait)


def parse(s):
    if not s or s.startswith("0001"):
        return None
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def iso(d):
    return d.astimezone().isoformat(timespec="seconds")


def main(dump_dir, out_dir, versions_arg="", seit=""):
    versions = dict(v.split("=", 1) for v in versions_arg.split(",") if "=" in v)
    wares = items()
    urls = list(chunks(list(wares)))
    rows = []
    for i, url in enumerate(urls, 1):
        print(f"  Anfrage {i} von {len(urls)}", file=sys.stderr)
        rows += fetch(url)

    now = datetime.now(timezone.utc)
    if seit:
        since = datetime.fromisoformat(seit)
        if since.tzinfo is None:
            since = since.astimezone()
        since -= timedelta(hours=3)
    else:
        # Ohne Angabe (GitHub-Datenpaket) 12 Stunden zurück: fällt ein Lauf aus, holt der nächste die Punkte nach;
        # doppelt geschriebene Punkte sind unschädlich
        since = now - timedelta(hours=12)
    docs = {key if m == "res" else f"{key}-{m}": (city, {}) for city, key in CITIES.items() for m in MAINS}
    verlauf, neu = {}, 0
    for row in rows:
        city = row["city"]
        if city not in CITIES or row["item_id"] not in wares:
            continue
        main, with_hist = wares[row["item_id"]]
        sd, bd = parse(row["sell_price_min_date"]), parse(row["buy_price_max_date"])
        e, doc, stamps = {}, {}, []
        if row["sell_price_min"] and sd and now - sd < MAX_AGE:
            e["p"], e["pt"] = row["sell_price_min"], iso(sd)
            doc["price"] = row["sell_price_min"]; stamps.append(sd)
        if row["buy_price_max"] and bd and now - bd < MAX_AGE:
            e["b"], e["bt"] = row["buy_price_max"], iso(bd)
            doc["buy"] = row["buy_price_max"]; stamps.append(bd)
        if not stamps:
            continue
        docs[CITIES[city] if main == "res" else f"{CITIES[city]}-{main}"][1][row["item_id"]] = e
        if not with_hist:
            continue
        gruppe = "SHARD" if row["item_id"].endswith("_SHARD_AVALONIAN") else row["item_id"].split("_")[1]
        # Verkauf und Kauf je mit ihrem eigenen Zeitpunkt (UTC); nur vorhandene Werte, so überschreibt ein
        # späterer Lauf ohne Kauforder keinen alten Wert
        for feld, wert, ts in (("s", doc.get("price"), sd), ("b", doc.get("buy"), bd)):
            if not wert or ts <= since:
                continue
            v = verlauf.setdefault(f"{gruppe}~{ts:%Y%m%d}", {"v": 2, "gruppe": gruppe, "tag": f"{ts:%Y%m%d}", "s": {}, "b": {}})
            v[feld].setdefault(row["item_id"], {}).setdefault(CITIES[city], {})[f"{ts:%H%M}"] = wert
            neu += 1

    # Reihenfolge: Preise, Verlauf, meta/abruf zuletzt (bricht ein Lauf ab, bleibt meta alt und der nächste holt nach)
    writes = []
    for doc_id, (city, its) in docs.items():
        if not its:  # leerer Teil (z. B. keine Antwort für eine Kategorie): gespeicherte Preise nicht löschen
            continue
        writes.append(("markt", doc_id, {"city": city, "updated": iso(now), "items": its}))
    writes += [("verlauf", doc_id, data) for doc_id, data in sorted(verlauf.items())]
    writes.append(("meta", "abruf", {"ts": iso(now), "via": "Claude", "items": sum(len(d[1]) for d in docs.values()),
                                       "neu": neu, "schema": SCHEMA}))

    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.json"):
        old.unlink()
    refs = []
    for coll, doc_id, data in writes:
        # Kein "~" im Dateinamen: unbeaufsichtigte Läufe (Cloud-Routine) lehnen solche Quelldateien ab
        f = out / f"{coll}__{doc_id.replace('~', '_')}.json"
        f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        known = f"{coll}/{doc_id}" in versions
        # markt und verlauf werden ergänzt (update führt verschachtelt zusammen): eine Ware ohne neue Meldung behält
        # ihren letzten Preis samt Zeitstempel; meta wird ersetzt
        ref = {"op": "update" if coll in ("markt", "verlauf") and known else "set", "collection": coll, "doc_id": doc_id,
               "file_path": str(f).replace("\\", "/")}
        if known:
            ref["if_version"] = int(versions[f"{coll}/{doc_id}"])
        refs.append(ref)
    for i in range(0, len(refs), 50):
        print(json.dumps(refs[i:i + 50], separators=(",", ":")))
        print("=====")
    print(f"{len(rows)} Zeilen vom Datenprojekt, {neu} Verlaufspunkte in {len(verlauf)} Verlaufs-Docs "
          f"(Tage {', '.join(sorted({d['tag'] for d in verlauf.values()})) or '-'}), {-(-len(refs) // 50)} Pakete")
    for city in CITIES:
        vals = [v for c, its in docs.values() if c == city for v in its.values()]
        ts = [v.get("pt") or v.get("bt") for v in vals]
        print(f"  {city}: {len(vals)} Waren, neuester Preis {max(ts) if ts else '-'}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Aufruf: python -I marktbuch_abruf.py <dump-ordner> <ausgabe-ordner> [versionen] [seit]", file=sys.stderr)
        sys.exit(2)
    try:
        main(*sys.argv[1:5])
    except RuntimeError as err:
        print(f"Abbruch: {err}", file=sys.stderr)
        sys.exit(1)
