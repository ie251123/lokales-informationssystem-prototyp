# ADR 11: Geprüfte Korrekturen getrennt speichern

## Status

ungültig, wird von ADR 13 überschrieben

## Kontext

Fachliche Prüfungen können Aussagen, Statuswerte, Evidenzpassagen und Relationen korrigieren. Die ursprünglichen Modellausgaben müssen dabei unverändert erhalten bleiben, damit Herkunft und Bearbeitung nachvollziehbar bleiben.

## Entscheidung

Korrekturen wurden als versionierte Zusatzdaten gespeichert. Sie überschrieben die importierten Rohdaten nicht. Ergänzende Tabellen erfassten geprüfte Aussagewerte, einzelne Evidenzpassagen sowie fachliche und technische Relationsentscheidungen.

Bereinigte Datenbanksichten fassten ausschließlich fachlich bestätigte Kandidaten und akzeptierte Relationen als sogenannten Clean Set zusammen. Migration und Rückschreibung erfolgten transaktional und nach einer Sicherung der Ausgangsdaten.

## Konsequenzen

- Rohdaten und fachliche Korrekturen bleiben getrennt nachvollziehbar.
- Die Zusatzdaten und bereinigten Sichten bleiben technisch nutzbar.
- Die Festlegung des Clean Sets als alleiniger operativer Bestand ist ungültig.
- ADR 13 erlaubt zusätzlich technisch valide ungeprüfte Kandidaten und schließt nur fachlich verworfene Kandidaten aus.
