# Datenmodell

## Implementierter Stand

Der Prototyp verwendet eine relationale SQLite-Datenbank mit Schema-Version 4. Das Schema wird durch `info_system.db.initialize_database(...)` reproduzierbar erzeugt und über `PRAGMA user_version` versioniert. Fremdschlüssel, `STRICT`-Tabellen, JSON-Prüfungen und Transaktionen sichern die technische Konsistenz.

Das Datenmodell besteht aus zwei Ebenen:

- **Generischer Quellen- und Evidenzkern:** bildet Projekt, Dokument, Version, Dokumentstelle, Aussage, Status, Quelle und die Verwendung einer Evidenz im Antwortlauf ab.
- **Anwendungsspezifisches Fachmodell:** ergänzt fachliche Objekte, Eigenschaften, Werte, Bedingungen, Klassifikationen und Beziehungen.

Der generische Kern beantwortet, woher eine Information stammt, in welchem Stand sie vorliegt und wie sie geprüft wurde. Das Fachmodell beschreibt ihre fachliche Bedeutung. Der implementierte Fachmodellausschnitt ist auf Anforderungen und Planungsinformationen mit Schwerpunkt Brandschutz ausgerichtet.

## Zentrale Tabellenbereiche

| Bereich | Wichtige Tabellen | Aufgabe |
|---|---|---|
| Dokumente und Quellen | `projects`, `documents`, `document_versions`, `document_nodes`, `document_references` | Dokumentidentität, Versionierung, logische Struktur und Locator |
| Aussagen und Herkunft | `statements`, `statement_sources` | normalisierte Aussagen und Verbindung zur Originalstelle |
| Fachmodell | `entities`, `entity_aliases`, `statement_entities`, `statement_values`, `statement_conditions`, `classifications` | fachliche Objekte, Werte, Bedingungen und Zuordnungen |
| Zusammenhänge | `statement_groups`, `statement_group_members`, `statement_relations` | Gruppierung und Beziehungen zwischen Aussagen |
| Prüfung | `candidate_review_overlays`, `candidate_review_evidence_passages`, `relation_review_overlays` | versionierte fachliche Entscheidungen und geprüfte Evidenzpassagen |
| Antwortläufe | `query_runs`, `evidence_items`, `answers` | verwendete Evidenz und Ergebnis eines Laufs |

## Aussagen, Status und Beziehungen

Das Schema unterscheidet vier Aussagearten:

- `requirement`: fachliche oder rechtliche Anforderung
- `planning_measure`: geplante oder dokumentierte Maßnahme
- `decision`: getroffene Entscheidung
- `finding`: Feststellung aus einer Quelle

Unabhängige Statusdimensionen bilden unterschiedliche Eigenschaften einer Aussage ab:

- `binding_status`: `binding` oder `unknown`
- `lifecycle_status`: im Prototyp `active`
- `review_status`: `needs_review`, `confirmed` oder `rejected`
- `action_state`: `planned`, `to_be_verified`, `documented` oder `NULL`
- `creation_method`: `model_candidate`, `manual` oder `migration`

Eine bestätigte Aussage benötigt mindestens eine Quelle. Prüfperson und Prüfzeitpunkt sind für bestätigte und verworfene Aussagen verpflichtend. Zulässige Relationstypen sind `addresses`, `derived_from`, `implements_decision`, `supersedes` und `conflicts_with`. Relationen aus der Extraktion bleiben Modellvorschläge, bis sie gesondert fachlich akzeptiert wurden. Eine Relation `fulfills` ist bewusst nicht vorgesehen, da das System keine fachliche oder rechtliche Erfüllung nachweist.

## Quellenbindung und Segmentierung

`document_nodes` speichert die logische Dokumentstruktur. Originalextraktion, zitierfähiger Quellentext und aufbereiteter Suchtext werden getrennt als `raw_extracted_text`, `source_text` und `search_text` geführt. Ein Knoten besitzt einen innerhalb der Dokumentversion eindeutigen `node_path`, eine Reihenfolge, einen Elementtyp sowie optional Seiten- und Zeichenbereiche.

Jede Aussage wird über `statement_sources` mit einer konkreten Dokumentstelle verbunden. Dadurch bleiben normalisierte Fachinformation, Originalpassage, Dokumentversion und Locator gemeinsam abrufbar. Die Quellenbindung bildet zugleich die Grundlage für `EvidenceItem`-Objekte in der Betriebsphase.

## Importprovenienz und Review-Overlays

Die Tabellen der Schema-Version 3 bewahren die vollständige Provenienz des Imports von `statement_candidates_batch_v3`. Gespeichert werden unter anderem Batch- und Promptbindung, Prüfsummen, unveränderter Rohoutput, vollständige Kandidatenobjekte, Dokumentreferenzen, Knotenverarbeitung und vorgeschlagene Relationen. Nicht normalisierte Felder bleiben im vollständigen JSON erhalten.

Schema-Version 4 ergänzt additive, versionierte Review-Overlays. Sie überschreiben weder die importierten Kandidaten noch die normalisierten Kerndaten. Einzelne geprüfte Evidenzpassagen werden getrennt gespeichert und müssen wörtlich im zugehörigen Quellentext vorkommen. Korrekturen erzeugen eine neue Reviewversion und setzen lediglich den bisherigen Aktuellmarker zurück; die Änderungshistorie bleibt erhalten.

Die Sichten `clean_reviewed_candidates`, `clean_reviewed_candidate_evidence` und `clean_reviewed_relations` stellen ausschließlich bestätigte Kandidaten, einzelne geprüfte Passagen und fachlich akzeptierte aktive Relationen bereit. Sie dienen der gezielten Auswertung des geprüften Bestands. Das operative Retrieval verwendet dagegen einen inklusiven Evidenzbestand: `confirmed` und technisch valide `needs_review` sind zugelassen, `rejected` ist ausgeschlossen. Der Prüfstatus wird in der Ausgabe sichtbar mitgeführt.

## Suchindex und Integrität

FTS5 indexiert zitierfähige und für die Suche aufbereitete Textfelder. Für das operative Retrieval wird zusätzlich eine temporäre FTS5-Projektion der zulässigen Evidenzmenge im Arbeitsspeicher aufgebaut. Suchkontext unterstützt dabei nur das Ranking; als Beleg ausgegeben werden weiterhin die gespeicherte Aussage und ihre Originalpassage.

Wesentliche Integritätsregeln sind:

- höchstens eine aktuelle Version je Dokument;
- eindeutige Knotenpfade innerhalb einer Dokumentversion;
- gültige Seiten-, Zeichen- und Rangbereiche;
- Quellenpflicht für bestätigte Aussagen;
- gültige JSON-Inhalte in strukturierten Feldern;
- Ausschluss von Selbstrelationen und ungültigen Relationstypen;
- mindestens ein Quellenbezug je verwendetem Evidenzelement.

Schemaänderungen erfolgen über additive Migrationen mit Sicherung und Audit. Die operative Datenbank ist kein Bestandteil des Repositorys; dort liegen nur Schema, Migrationen und Tests. Eine leere Datenbank kann reproduzierbar erzeugt werden, während ein fachlich befüllter Stand separat bereitgestellt werden muss.
