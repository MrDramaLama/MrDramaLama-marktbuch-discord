# -*- coding: utf-8 -*-
"""
Baut das Datenpaket fürs Marktbuch-Artefakt: holt alle Preise (wie marktbuch_abruf.py) und schreibt
EINE Datei artefakt.json, die eine Cloud-Routine von GitHub lädt und per ArtifactData ins Artefakt schreibt.

    python artefakt_bundle.py <ausgabe-ordner>

Inhalt von artefakt.json:
  ts     Zeit des Abrufs (ISO, Berliner Zeit)
  docs   {"markt/ly": {...}, ..., "meta/abruf": {...}}   56 Dokumente, so wie sie ins Artefakt gehören
  verlauf {"CLOTH~20261027": {...}, ...}                 Verlauf der letzten 3 Stunden je Warengruppe und Tag (UTC),
                                                          Datenschema 2; die Routine ergänzt bestehende Docs per update
                                                          und legt neue per set an (derselbe Punkt darf doppelt kommen)
  hist   []                                               leer, nur damit eine Routine mit altem Auftrag nicht abbricht
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import time
if hasattr(time, "tzset"):  # Linux-Runner läuft in UTC: Zeitstempel wie am Windows-PC in Berliner Zeit
    os.environ.setdefault("TZ", "Europe/Berlin")
    time.tzset()

sys.path.insert(0, str(Path(__file__).resolve().parent))
import marktbuch_abruf as ab  # noqa: E402


def main(ziel):
    ziel = Path(ziel); ziel.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        dump, out = Path(tmp, "dump"), Path(tmp, "out")
        (dump / "obs").mkdir(parents=True)
        alt_stdout = sys.stdout
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # die Paketlisten von main() braucht hier niemand
        try:
            ab.main(str(dump), str(out), "", "")
        finally:
            sys.stdout.close(); sys.stdout = alt_stdout
        docs, verlauf = {}, {}
        for f in sorted(out.glob("*.json")):
            coll, _, doc_id = f.stem.partition("__")
            data = json.loads(f.read_text("utf-8"))
            if coll == "verlauf":
                verlauf[f"{data['gruppe']}~{data['tag']}"] = data  # Dateiname trägt "_" statt "~"
            else:
                docs[f"{coll}/{doc_id}"] = data
    meta = docs["meta/abruf"]
    meta["via"] = "GitHub"
    paket = {"ts": meta["ts"], "docs": docs, "verlauf": verlauf, "hist": []}
    (ziel / "artefakt.json").write_text(json.dumps(paket, ensure_ascii=False, separators=(",", ":")), "utf-8")
    print(f"artefakt.json: {len(docs)} Dokumente, {meta['items']} Waren, {meta['neu']} Verlaufspunkte in {len(verlauf)} Docs, "
          f"{(ziel / 'artefakt.json').stat().st_size // 1024} KB, Stand {meta['ts']}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Aufruf: python artefakt_bundle.py <ausgabe-ordner>", file=sys.stderr); sys.exit(2)
    try:
        main(sys.argv[1])
    except RuntimeError as err:
        print(f"Abbruch: {err}", file=sys.stderr); sys.exit(1)
