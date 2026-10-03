# ADR 16: Lokales Sprachmodell kontrolliert einsetzen

## Status

accepted

## Kontext

Das lokale Sprachmodell eignet sich für die Interpretation natürlicher Fragen und die semantische Auswahl bereitgestellter Evidenz. Eine freie Antwortformulierung gab Pflichtangaben, Quellenbezüge und unvollständige Evidenz jedoch nicht zuverlässig wieder.

## Entscheidung

Das Modell übernimmt ausschließlich zwei strukturierte Aufgaben: Es erzeugt einen validierbaren `SearchIntent` und wählt aus dem Evidenzpaket eindeutige Evidenzkennungen mit dem Abdeckungsstatus `covered`, `partial` oder `missing`.

Python validiert beide Ausgaben. Ein deterministischer Hauptankerwächter beendet den Lauf vor der Evidenzauswahl, wenn kein hinreichender Bezug zur Frage vorhanden ist. Das Modell erzeugt keinen freien Antworttext. Die Anwendung formatiert die validierte Auswahl deterministisch und ergänzt Prüfstatus, Dokument, Version und Locator. Fehlende oder teilweise Evidenz wird als `limited` sichtbar ausgewiesen.

Rohe Modellausgaben und Verarbeitungsschritte werden außerhalb des Repositorys protokolliert. Die Entscheidung konkretisiert ADR 03 für den operativen Antwortpfad.

## Konsequenzen

- Frageinterpretation und Evidenzauswahl bleiben lokale Modellaufgaben.
- Antwortstruktur, Begrenzung und Quellenangaben sind deterministisch und besser rekonstruierbar.
- Fehlende Evidenz kann nicht durch freie Modellformulierungen verdeckt werden.
- Es werden keine Embeddings, freie Antwortgenerierung oder automatische Konformitätsbewertung eingeführt.
