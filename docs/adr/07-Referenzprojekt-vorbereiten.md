# ADR 07: Referenzprojekt vorbereiten

## Status

nicht vollständig umgesetzt

## Kontext

Quelldokumente des Referenzprojekts liegen außerhalb des Repositorys. Für ihre kontrollierte Verarbeitung war ein technisches Inventar vorgesehen, ohne Dokumentinhalte oder absolute lokale Pfade zu veröffentlichen.

## Entscheidung

Die Vorbereitung soll lokal und deterministisch erfolgen. Für jedes Dokument werden ein kryptografischer Hash, technische PDF-Metadaten, ein relativer Bezeichner und eine Datenschutzklasse erfasst. Private und grundsätzlich teilbare Einträge werden in getrennten Manifesten dokumentiert.

Die Manifeste enthalten weder extrahierte Texte noch absolute lokale Pfade. Die Verarbeitung nutzt kein Sprachmodell und keine externen Dienste. Nicht lesbare Dateien werden als Fehler dokumentiert, ohne den gesamten Inventarlauf abzubrechen.

Der allgemeine Inventarisierungsweg wurde nicht vollständig zum finalen Standardpfad ausgebaut. Der tatsächlich verwendete Referenzbestand wurde enger abgegrenzt und über kontrolliert vorbereitete Importpakete verarbeitet.

## Konsequenzen

- Dokumentbestände können vor dem Import nachvollziehbar beschrieben und auf Veränderungen geprüft werden.
- Manifeste mit Dateinamen und Hashes bleiben außerhalb des öffentlichen Repositorys.
- Das Inventar ersetzt weder die Dokumentregistrierung in SQLite noch die fachliche Prüfung.
- Für den veröffentlichten Prototyp ist keine allgemeine Inventarisierung beliebiger Projektbestände nachgewiesen.
