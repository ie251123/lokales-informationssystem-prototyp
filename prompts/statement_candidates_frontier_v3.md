Du extrahierst vorläufige Aussagekandidaten aus einem lokalen, deterministisch
gebildeten Dokumentknoten-Batch.

Gib exakt ein JSON-Objekt zurück. Gib kein Markdown, keine Kommentare und keine
erläuternde Prosa zurück.

Der nachfolgende Auftrag ist vollständig eigenständig. Verwende ausschließlich
die darin enthaltenen Definitionen und Eingabedaten. Setze keine zusätzlichen
Schemata, Dateien oder Repositoryinhalte voraus.

Kein Pflichtfeld darf ausgelassen, umbenannt oder durch eine Kurzform ersetzt
werden. Verwende insbesondere niemals:

- `processing_status` statt `status`;
- `source_node_id` statt `document_node_id`;
- `evidence_quote` statt der vorgeschriebenen `evidence`-Struktur;
- `statement_text` statt der definierten v3-Felder, insbesondere
  `normalized_text`.

Prüfe jeden Knoten mit `node_role = "target"` absatzweise und vollständig.
Erfasse alle fachlich relevanten Anforderungen, Planungsmaßnahmen,
Feststellungen und Entscheidungen. Trenne unabhängige Aussagen atomar.

Prüfe vor `processed_without_candidate` den gesamten Zielknoten erneut auf
Zahlenwerte, Maße, Bauteilqualitäten, räumliche Zuordnungen, Maßnahmen,
Nachweise, Abweichungen und dokumenteigene Bewertungen. Erfinde keine Inhalte.

Verwende ausschließlich exakte Passagen aus `source_nodes[*].source_text` als
Evidenz. Kandidaten dürfen nur Zielknoten als Quelle verwenden. Kontextknoten
dienen ausschließlich dem Verständnis. Alle Kandidaten verwenden
`review_status = "needs_review"`.

Kopiere die separat in der letzten Nachricht bereitgestellte `batch_binding`
unverändert und vollständig in die Ausgabe.

## Vollständiger Vertrag

{{CONTRACT_MARKDOWN}}

## Vollständig eingebettetes Ausgabeschema

Das folgende Schema ist die vollständige und für die Validierung maßgebliche
Definition. Es enthält alle benötigten Strukturen direkt im Auftrag.

{{JSON_SCHEMA}}

## Schematische JSON-Grundstruktur

Die folgende Struktur zeigt sämtliche Pflichtfelder. Platzhalter sind durch
Werte aus Eingabepaket, Evidenz und verbindlicher Batchbindung zu ersetzen.
Leere Arrays und `null` sind nur dort zu verwenden, wo das Schema sie erlaubt.
Der einzelne Kandidat und die Einträge seiner Strukturarrays sind neutrale
Strukturbeispiele. Entferne jeden nicht durch die Quelle getragenen Eintrag und
lasse das jeweilige Array leer. Wenn ein Zielknoten keinen Kandidaten trägt, ist
auch der Kandidat wegzulassen; die Arrays selbst bleiben stets erhalten.

{{OUTPUT_SKELETON}}

## Vollständiges Eingabepaket

{{INPUT_PACKAGE}}
