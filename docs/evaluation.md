# Evaluationsprotokoll

## Zweck

Dieses Dokument beschreibt ausschließlich die technischen Werte und Zwischenprodukte, die der finale `ask-local`-Pfad für die Evaluation bereitstellt. Wissenschaftliches Evaluationsdesign, Referenzfragen, Goldreferenzen, fachliche Bewertung und Interpretation sind nicht Bestandteil des Repositorys.

Jeder erfolgreiche Lauf erzeugt außerhalb des Repositorys genau ein vollständiges JSON-Protokoll nach dem Vertrag `local_e2e_run_v3`. Das Zielverzeichnis ist verpflichtend; vorhandene Protokolle werden nicht überschrieben. Die Betriebsdatenbank, Quelldokumente und Modellartefakte bleiben ebenfalls außerhalb des Repositorys.

## Laufkontext

Das Protokoll identifiziert den ausgeführten Systemstand und enthält:

| Bereich | Bereitgestellte Werte |
|---|---|
| Lauf | `run_id`, Protokollversion, Start- und Endzeit, Laufstatus |
| Eingabe | `project_id`, `question_id`, vollständige Nutzerfrage |
| Repository | `git_commit`, `tracked_worktree_clean` |
| Datenbank | `database_sha256`, `database_user_version` |
| Modell | `model_id`, `model_artifact`, `model_quantization` |
| Komponenten | Versionen der verwendeten Prompts, Adapter, Validatoren und Formatter |

Git-Commit, Arbeitsbaumstatus, Datenbankhash und Schema-Version werden beim Lauf technisch ermittelt. Ein Lauf wird vor dem ersten Modellaufruf abgebrochen, wenn der Git-Stand nicht zuverlässig bestimmt werden kann, getrackte Änderungen vorhanden sind, die Datenbank nicht lesbar ist oder das externe Ausgabeverzeichnis nicht verwendet werden kann.

## SearchIntent

Für die Übersetzung der Nutzerfrage in einen strukturierten Suchauftrag werden protokolliert:

- tatsächlicher Request an das lokale Sprachmodell;
- unveränderte Modellausgabe;
- geparster und validierter `SearchIntent`;
- Suchfacetten, Suchbegriffe, Filter und Treffergrenze;
- Validierungswarnungen und Fehler;
- Modelllaufzeit und Tokenverbrauch.

Rohe Modellausgabe und validiertes Ergebnis bleiben getrennt. Nur der validierte `SearchIntent` wird an das Retrieval übergeben.

## Retrieval

Das Retrievalprotokoll dokumentiert den deterministischen Suchweg einschließlich:

- verwendeter Retrievalkomponenten und Iterationen;
- normalisierter Suchbegriffe und technischer Schreibvarianten;
- angewendeter Aussage-, Status-, Dokument-, Objekt- und Wertefilter;
- gebundener Abfrageparameter und Treffergrenze;
- Rang und Score der Treffer je Suchkomponente;
- Beiträge der konditionalen Reciprocal Rank Fusion;
- Deduplizierung und finaler Rang;
- Retrievallaufzeit.

Für jedes zurückgegebene EvidenceItem werden mindestens gespeichert:

- `evidence_id`, `candidate_id`, `statement_id` und `passage_order`;
- normalisierte Aussage und exakte Originalpassage;
- Dokument, Dokumentversion und Locator;
- Aussageart, Verbindlichkeitsstatus und Prüfstatus;
- vorhandener Objekt-, Werte- und Abschnittskontext.

Verworfene Kandidaten werden nicht als operative Evidenz ausgegeben. Suchkontext wird als Rankinghilfe protokolliert, aber nicht als Quellenbeleg behandelt.

## Hauptankerwächter und Evidenzauswahl

Zwischen Retrieval und Evidenzauswahl protokolliert das System:

- Ergebnis und Begründung des Hauptankerwächters;
- erkannte fragebezogene Anker und zugeordnete Evidence-IDs;
- Entscheidung, ob der zweite Modellaufruf ausgeführt oder übersprungen wird.

Für die anschließende Evidence Selection werden gespeichert:

- tatsächlicher Modellrequest und unveränderte Modellausgabe;
- validierte ausgewählte Evidence-IDs;
- Zuordnung der Evidenzen zu den Suchfacetten;
- Abdeckungsstatus `covered`, `partial` oder `missing` je Facette;
- Validierungsfehler und diagnostischer Facettenwortlautvergleich;
- Modelllaufzeit und Tokenverbrauch.

Das Sprachmodell kann ausschließlich Evidenzen auswählen, die im bereitgestellten Evidenzpaket enthalten sind. Ungültige IDs, unzulässige Statuswerte und fehlerhafte Facettenzuordnungen werden durch die Anwendung abgewiesen.

## Antwort und Laufwerte

Die deterministisch erzeugte Ausgabe dokumentiert:

- vollständigen Antworttext und Antwortstatus;
- verwendete Evidence-IDs in Ausgabereihenfolge;
- Quellenangaben mit Dokument, Version und Locator;
- sichtbaren Prüfstatus jeder Aussage;
- teilweise oder nicht abgedeckte Facetten;
- Gesamtzeit sowie Teilzeiten von SearchIntent, Retrieval, Hauptankerprüfung, Evidence Selection und Formatierung;
- Anzahl der Modellaufrufe und Tokenverbrauch je Modellinteraktion.

