# Statement Candidates Batch v2

Dieser versionierte Vertrag ergänzt `statement_candidates_v1`, ohne dessen
Prompt, Eingabeformat oder Ausgabeschema rückwirkend zu verändern. Er gilt nur
für deterministische Abschnitts-Batches der Konstruktionsphase.

`batch_binding` wird vom Paketgenerator vorgegeben und muss unverändert
zurückgegeben werden. Das Modell errät weder die Modellbezeichnung noch
Prüfsummen. `node_processing` enthält genau einmal jeden `target`-Knoten.
`context_only`-Knoten erhalten keinen Status und dürfen keine Kandidatenquelle
sein. Kandidaten-IDs beginnen mit dem jeweiligen `batch_id` und Relationen
bleiben innerhalb desselben Batches.

Alle Kandidaten sind vorläufig und haben `review_status = needs_review`.
Evidenz ist eine exakte Passage aus `source_text` eines Zielknotens. Leere oder
nicht zitierfähige Seiten und rein grafische, überholte Beschriftungen sind
keine Quellen. Insbesondere ist die überholte grafische Beschriftung auf Seite
23 nicht als Evidenz zulässig.

Dokumenteigene Erfüllungsbewertungen werden ausschließlich als `finding` mit
`statement_subtype = source_assessment` erfasst. Fortschreibungs- und
Änderungsvermerke über bereits erfolgte Änderungen sind `finding`; nur
konkrete zukünftige Planungs- oder Ausführungsaktivitäten sind
`planning_measure`. Der Relationstyp `fulfills` und automatische
Konformitätsbewertungen bleiben verboten.
