# ADR 15: Prüfentscheidungen nachvollziehbar korrigieren

## Status

accepted

## Kontext

Eine fachlich fehlerhafte Prüfentscheidung muss korrigierbar sein, ohne die frühere Entscheidung und ihre Herkunft zu überschreiben. Für einen reinen Statuswechsel ist keine weitere Schemamigration erforderlich.

## Entscheidung

Eine Korrektur verwendet einen versionierten, mit SHA-256 gekennzeichneten Plan. Vor dem Schreiben werden Schema, Kandidat, aktueller Prüfzustand, Evidenzpassagen und geschützte Ausgangsdaten geprüft. Außerdem wird eine Sicherung der Datenbank erstellt.

In einer Transaktion wird die bisherige Prüfung als nicht mehr aktuell markiert und eine neue Version mit `needs_review` angelegt. Fachliche Werte und Quellenbezüge werden unverändert übernommen; Korrekturgrund und Planhash bleiben als Provenienz erhalten. Jede Abweichung von den erwarteten Vorbedingungen führt zum vollständigen Zurückrollen.

## Konsequenzen

- Die vollständige Prüfungshistorie bleibt erhalten.
- Rohkandidaten, Aussagen, Relationen und Suchlogik bleiben unverändert.
- Der korrigierte Kandidat bleibt nach ADR 13 mit sichtbarem ungeprüftem Status auffindbar.
- Das Verfahren dient nur der kontrollierten Korrektur bestehender Prüfungen und ist kein allgemeines System für Prüfabläufe.
