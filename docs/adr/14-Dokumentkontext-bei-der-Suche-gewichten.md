# ADR 14: Dokumentkontext bei der Suche gewichten

## Status

accepted

## Kontext

Eine Suche nur in normalisierter Aussage und Originalpassage nutzt vorhandene Dokumentüberschriften, Objektbezüge und Werte nicht. Diese Felder können die Auffindbarkeit verbessern, dürfen aber nicht selbst als Antwortbeleg gelten.

## Entscheidung

`find_evidence` erstellt für jeden Aufruf eine abgeleitete FTS5-Projektion im Arbeitsspeicher. Die BM25-Gewichte betragen: normalisierte Aussage 5,0; Originalpassage 4,0; Abschnittsüberschrift 3,0; übergeordnete Überschrift 2,0; Dokumenttitel 1,0; Dokumenttyp 0,5; Objekt- und Wertekontext jeweils 2,0.

Kontextfelder dienen nur als Suchsignal. Als Evidenz dienen weiterhin die normalisierte Aussage und die Originalpassage. Der Prüfstatus beeinflusst die Rangfolge nicht.

Die Kontextliste wird nur dann über eine rangbasierte Fusion mit der bestehenden Trefferliste verbunden, wenn sie zusätzliche zulässige Treffer liefert. Andernfalls bleibt die Ausgangsrangfolge unverändert. Aktivierung, Komponentenränge und Fusionsbeiträge werden protokolliert.

## Konsequenzen

- Die Suche nutzt vorhandenen Kontext ohne Schemaänderung oder neue Abhängigkeit.
- Die Quelldatenbank bleibt unverändert und wird nur lesend verwendet.
- Zusätzliche Treffer werden nachvollziehbar ergänzt, ohne die bewährte Rangfolge unnötig zu verändern.
- Die Gewichtung ist an den implementierten Dokument- und Fachkontext gebunden und muss bei Erweiterungen erneut geprüft werden.
