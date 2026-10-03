# ADR 09: Aussagekandidaten verlustfrei importieren

## Status

accepted

## Kontext

Validierte Ausgabepakete des Vertrags `statement_candidates_batch_v3` enthalten zusätzliche Herkunfts- und Prüffelder, die das normalisierte Kernschema nicht vollständig abbildet. Eine Feldreduktion beim Import würde die Nachvollziehbarkeit der Extraktion beeinträchtigen.

## Entscheidung

Für den Import werden ergänzende Archivtabellen verwendet. Der unveränderte Rohoutput sowie vollständige Kandidaten- und Relationsobjekte werden gespeichert und mit den normalisierten Kerntabellen verknüpft.

Alle Pakete werden vor dem ersten Schreibzugriff validiert und anschließend in einer gemeinsamen Transaktion importiert. Stabile Kennungen erlauben identische Wiederholungen; abweichende Inhalte unter derselben Kennung führen zum Abbruch. Ein Probelauf wird vollständig zurückgerollt.

Importierte Aussagen bleiben Modellkandidaten mit dem Prüfstatus `needs_review`. Der Import bestätigt keine Aussage und verändert weder Quellen- noch Normalisierungstexte.

## Konsequenzen

- Extraktionsergebnisse bleiben bis zum Rohoutput nachvollziehbar.
- Normalisierte Tabellen können weiterhin für Suche und fachliche Prüfung verwendet werden.
- Der Import ist atomar und wiederholbar, verursacht aber zusätzlichen Speicher- und Schemaaufwand.
- Technisch gültige Kandidaten gelten erst nach gesonderter Prüfung als fachlich bestätigt.
