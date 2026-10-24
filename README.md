# Marktbuch Discord (Cloud)

Postet jede Stunde (Minute 7 UTC) einen neuen Post mit Albion-Marktdaten (Europa) in den Discord-Kanal
`#markt-und-handel` der Gilde Drama Lama United und löscht den vorherigen; zur Minute 37 wird der Post mit
frischen Preisen bearbeitet. Läuft als GitHub-Actions-Zeitplan, unabhängig vom eigenen PC.

Inhalt: Top-5-Flips zwischen den fünf Königsstädten (nach 6,5 % Steuer), günstigste Stadt je Rohstoff
T4-T6, Luxuswaren-Alarm (Markt unter festem Händlerpreis). Quelle: Albion Online Data Project.

## Einrichtung
1. Repository-Secret `MARKTBUCH_WEBHOOK` = Webhook-URL des Kanals (Settings → Secrets and variables → Actions).
2. `marktbuch_discord_state.json` enthält die ID der Live-Nachricht; der Workflow aktualisiert sie selbst.
3. Erster Lauf: Actions → „Marktbuch Discord“ → „Run workflow“.

Dateien: `marktbuch_discord.py` (Poster), `marktbuch_abruf.py` (Warenliste, Anfragen),
`marktbuch_waren.json` (Waren), `.github/workflows/marktbuch.yml` (Zeitplan).
