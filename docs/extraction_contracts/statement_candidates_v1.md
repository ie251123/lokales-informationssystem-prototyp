# Statement Candidates v1

Dieser Vertrag definiert modellgestützte Aussagekandidaten für die
Konstruktionsphase. Er ist der ausführbare Vertrag zum JSON-Schema
`statement_candidates_v1.schema.json`.

Das Modell erhält ausschließlich zitierfähige Passagen aus
`document_nodes.source_text`. Es darf `raw_extracted_text`, `search_text`,
Entwicklungs-Goldreferenzen, Hold-out-Fragen, erwartete Antworten und externes
Wissen nicht verwenden.

Jeder Kandidat ist vorläufig:

- `review_status` ist immer `needs_review`.
- Das Modell darf nicht `confirmed` oder `rejected` ausgeben.
- `creation_method` wird vom Importer als `model_candidate` gesetzt.
- `reviewed_by` und `reviewed_at` bleiben bis zur menschlichen Prüfung leer.

Zulässige Werte für `statement_type` sind:

- `requirement`
- `planning_measure`
- `finding`
- `decision`

Quellenseitige Bewertungen aus dem Dokument selbst sind Aussagen vom Typ
`finding` mit `statement_subtype = source_assessment`. Sie sind keine
Systembewertungen und keine behördlichen Bestätigungen.

Relationen sind nur zulässig, wenn sie durch den lokalen Kontext ausdrücklich
belegt sind:

- `addresses`
- `derived_from`
- `implements_decision`
- `supersedes`
- `conflicts_with`

Der Relationstyp `fulfills` ist verboten. Es dürfen keine automatischen
Konformitäts-, Rechts- oder Erfüllungsbewertungen erzeugt werden.

Evidenz muss exakt aus einer zitierfähigen `source_text`-Passage kopiert
werden. Leere Seiten, nicht zitierfähige Knoten, nur grafisch vorhandene
überholte Beschriftungen, Kopfzeilen, Inhaltsverzeichnisse und rein
redaktionelle Wiederholungen dürfen nicht zu Evidenz werden. Die überholte
Beschriftung auf Seite 23 `0,30 m ueber Dach gefuehrt` ist nicht zulässig. Der
zitierfähige Text auf Seite 23 zur beidseitig `0,5 m` auskragenden Platte
bleibt zulässig, wenn er aus `source_text` extrahiert wird.

Für jeden zitierfähigen Eingabeknoten ist genau ein Verarbeitungsstatus
anzugeben. Verwende `processed_without_candidate`, wenn keine atomare Aussage
vorliegt. Verwende `failed` nur mit einer knappen Fehlerangabe. Knoten dürfen
nicht wegen Kontextgrenzen des Modells ausgelassen werden.

Atomare Aussagen sind von ihren Bedingungen, Werten, Objekten,
Klassifikationen, Gruppen, Dokumentverweisen und Relationen getrennt zu halten.
Ein Rechtszitat innerhalb eines Brandschutzkonzepts belegt für sich allein
nicht `binding_status = binding`; verwende `unknown`, solange die Quelle nicht
selbst die verbindliche Norm ist.
