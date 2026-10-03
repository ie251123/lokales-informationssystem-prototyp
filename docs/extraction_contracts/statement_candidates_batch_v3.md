# Statement Candidates Batch v3

Dieser Vertrag gilt ausschließlich für eigenständige Frontier-Batch-Pakete der
Konstruktionsphase. Er ersetzt oder verändert keine v1- oder v2-Artefakte.

Der ausführbare Modellauftrag enthält den vollständigen Vertrag, das vollständige
Ausgabeschema, eine schematische Ausgabegrundstruktur, das Eingabepaket und die
verbindliche Batchbindung. Das Modell benötigt kein Repositorywissen und darf
keine außerhalb des Auftrags liegenden Definitionen voraussetzen.

Die Ausgabe ist exakt ein JSON-Objekt ohne Markdown, Kommentare oder Begleittext.
Jedes im Schema vorgeschriebene Feld muss unter seinem exakten Namen und mit der
vorgeschriebenen Verschachtelung ausgegeben werden. Pflichtfelder dürfen auch bei
leeren Arrays oder `null`-Werten nicht ausgelassen, umbenannt oder verkürzt
werden.

Insbesondere sind folgende Kurzformen verboten:

- `processing_status` anstelle von `status`;
- `source_node_id` anstelle von `document_node_id`;
- `evidence_quote` anstelle des strukturierten Objekts `evidence`;
- `statement_text` anstelle von `normalized_text` und den weiteren definierten
  Kandidatenfeldern.

`batch_binding` wird vom Paketgenerator vorgegeben und muss aus der letzten
Nachricht des Modellauftrags exakt und vollständig kopiert werden. Modellname,
Versionen, Knotenlisten und Prüfsummen dürfen weder erraten noch verändert
werden.

`node_processing` enthält genau einmal jeden Zielknoten in Manifestreihenfolge.
Jeder Eintrag enthält `document_node_id`, `status` und `candidate_ids`.
Kontextknoten erscheinen nicht in `node_processing` und dürfen keine
Kandidatenquelle sein. Jeder Kandidat wird genau einem Zielknoten und genau einem
`node_processing`-Eintrag zugeordnet.

Evidenz ist eine exakte Passage aus `source_text` eines Zielknotens. Optionale
Zeichenpositionen müssen, falls beide angegeben werden, exakt auf diese Passage
zeigen. Rein grafische oder als überholt gekennzeichnete Beschriftungen sind
keine Evidenz.

Alle Kandidaten sind vorläufig und verwenden `review_status = needs_review` und
`lifecycle_status = active`. Fortschreibungs- und Änderungsvermerke über bereits
erfolgte Änderungen sind `finding`. Nur konkrete zukünftige Planungs- oder
Ausführungsaktivitäten sind `planning_measure`. Dokumenteigene
Erfüllungsbewertungen sind `finding` mit `statement_subtype = source_assessment`.
Automatische Konformitätsbewertungen und der Relationstyp `fulfills` sind nicht
zulässig.

Objekte, Werte, Bedingungen, Klassifikationen, Gruppen, lokale Relationsnotizen,
Dokumentreferenzen, Reviewhinweise und Validierungswarnungen sind immer als die
im Schema verlangten Arrays vorhanden. Wenn die Quelle keine entsprechenden
Strukturen trägt, werden leere Arrays ausgegeben; es werden keine Fülldaten
erfunden.
