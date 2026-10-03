Du extrahierst vorläufige Aussagekandidaten aus einem lokalen, deterministisch
gebildeten Dokumentknoten-Batch.

Gib exakt ein JSON-Objekt zurück, das dem untenstehenden JSON-Schema entspricht.
Gib kein Markdown, keine Kommentare und keine erläuternde Prosa zurück.

Prüfe jeden Knoten mit `node_role = "target"` absatzweise und vollständig.
Erfasse alle fachlich relevanten Anforderungen, Planungsmaßnahmen,
Feststellungen und Entscheidungen, nicht nur eine repräsentative Aussage pro
Seite. Mehrere Kandidaten aus demselben Zielknoten sind ausdrücklich zulässig.
Trenne voneinander unabhängige Aussagen, Werte, Bedingungen und Objekte atomar.
Fasse unterschiedliche Anforderungen oder Maßnahmen nicht zu einer
Sammelaussage zusammen.

Prüfe vor `processed_without_candidate` den gesamten Zielknoten erneut auf
Zahlenwerte, Mindest- und Höchstmaße, Bauteilqualitäten, räumliche Zuordnungen,
Maßnahmen, Nachweise, Abweichungen und dokumenteigene Bewertungen. Erfasse
konkrete Objekte, Werte, Bedingungen, Klassifikationen, Gruppen und explizite
Relationen, wenn die Quelle sie trägt. Erfinde solche Strukturen nicht und lege
sie nicht nur zum Füllen des Schemas an.

Ordne Fortschreibungs- und Änderungsvermerke, die bereits erfolgte
Dokumentänderungen beschreiben, grundsätzlich als `finding` ein. Nur konkrete
zukünftige Planungs- oder Ausführungsaktivitäten sind `planning_measure`.
Dokumenteigene Erfüllungsbewertungen sind ausschließlich `finding` mit
`statement_subtype = "source_assessment"`.

Verwende ausschließlich exakte Passagen aus `source_nodes[*].source_text` als
Beleg. Kandidaten dürfen nur Knoten mit `node_role = "target"` als Quelle
verwenden. Knoten mit `node_role = "context_only"` dienen nur dem Verständnis:
Sie dürfen weder Kandidatenquelle sein noch in `node_processing` erscheinen.
Gib alle Kandidaten ausschließlich mit `review_status = "needs_review"` aus.

Verwende kein externes Wissen, keine Entwicklungs-Goldreferenzen, keine
Entwicklungs- oder Hold-out-Fragen, keine erwarteten Antworten und keine
anderen Textschichten. Erfinde keine Modellbezeichnung und keine Prüfsumme.
Kopiere die separat bereitgestellte `batch_binding` unverändert in die Ausgabe.

## Vertrag

{{CONTRACT_MARKDOWN}}

## JSON-Schema

{{JSON_SCHEMA}}

## Eingabepaket

{{INPUT_PACKAGE}}
