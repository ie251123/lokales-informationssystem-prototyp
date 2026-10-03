# Teststrategie

## Zweck

Die Tests prüfen die technische Funktionsfähigkeit, Sicherheit und Reproduzierbarkeit des Prototyps. 

Alle veröffentlichten Tests verwenden kleine synthetische Fixtures, temporäre Datenbanken und feste oder gemockte Modellantworten. Weder die operative Datenbank noch reale Projektdokumente, LM Studio oder Qwen sind für die Testausführung erforderlich.

## Testprinzipien

- Tests werden mit `pytest` ausgeführt und müssen deterministisch wiederholbar sein.
- Externe Dienste und Netzwerkzugriffe sind nicht erforderlich.
- Datei- und Datenbankoperationen verwenden ausschließlich temporäre Testverzeichnisse.
- Identische Eingaben erzeugen reproduzierbare IDs, Locator, Reihenfolgen und Datenbankzustände.
- Schreibende Abläufe werden auf Transaktionssicherheit und Rollback geprüft.
- LLM-Ausgaben und Nutzerparameter werden als ungesicherte Eingaben behandelt.

## Testbereiche

### Datenbank und Datenmodell

Die Tests prüfen den Neuaufbau einer leeren Schema-4-Datenbank, additive Migrationen, Fremdschlüssel und zentrale Constraints. Abgedeckt sind insbesondere Dokumentversionen, Quellenbindung, Aussagearten, Statuswerte, Relationstypen, JSON-Felder, Locator sowie der Ausschluss von Selbstrelationen und `fulfills`.

Für bestätigte Aussagen wird die Quellenpflicht geprüft. Review-Overlays, getrennte Evidenzpassagen, versionierte Korrekturen und die drei Clean-Set-Sichten werden einschließlich ihrer Filter- und Rollbackregeln getestet.

### Konstruktionsphase

Geprüft werden Textnormalisierung, dokumentlogische Segmentierung, Locator-Erzeugung und der verlustfreie Import von `statement_candidates_batch_v3`. Dazu gehören Vertrags-, Manifest-, Batch-, Dokument- und Prüfsummenvalidierung, idempotente Wiederholung sowie der vollständige Transaktionsabbruch bei widersprüchlichen stabilen IDs.

Der Import muss Rohoutput, Kandidatenobjekte, Evidenz und Relationsvorschläge bewahren und ausschließlich Kandidaten mit `needs_review` erzeugen. Fachliche Bestätigungen dürfen nicht automatisch entstehen.

### Retrieval

Die Retrievaltests verwenden synthetische Schema-4-Datenbanken und prüfen:

- strikte Validierung des `SearchIntent`;
- parametrisierte SQL- und FTS5-Abfragen;
- Filter nach Aussageart, Status, Dokument und Objektmerkmalen;
- Retrieval von `confirmed` und technisch validem `needs_review` bei Ausschluss von `rejected`;
- Vorrang bestätigter Overlaywerte und Fallback auf Rohwerte ungeprüfter Kandidaten;
- Exact-/Präfixsuche, gewichtete Kontextprojektion und konditionale Reciprocal Rank Fusion;
- stabile Deduplizierung, Rangfolge und vollständigen Retrievaltrace;
- read-only-Zugriff und unveränderte Quelldatenbank;
- begrenzte, kontrollierte Tool-Suche ohne freie Shellausführung.

### Lokales Sprachmodell und Antwortpfad

Modellschnittstellen werden durch feste Stubs oder Mocks ersetzt. Getestet werden gültige und ungültige SearchIntents, der Hauptankerwächter, die kontrollierte Evidence Selection sowie die Ablehnung unbekannter IDs, ungültiger Facettenzuordnungen und verworfener Evidenz.

Ein synthetischer End-to-End-Test deckt die vollständige Betriebsstrecke ab:

```text
Nutzerfrage → SearchIntent → Retrieval → Evidenzpaket
→ Hauptankerwächter → Evidence Selection
→ deterministische Antwort → Laufprotokoll
```

Der Test prüft außerdem `limited` bei unvollständiger Abdeckung, sichtbare Prüfstatus, kanonische Quellenangaben und den Ausschluss frei erzeugter Antwortinhalte.

### Sicherheit und Protokollierung

Negativtests prüfen Prompt-Injection in Dokumenttexten, SQL- und Shellfragmente, unbekannte Werkzeuge, nicht erlaubte Filter, unzulässige Pfade und manipulierte Quellen-IDs. Erwartet werden kontrollierte Ablehnung, unveränderte Fachdaten und ein nachvollziehbarer Fehlerstatus.

Das Evaluationsprotokoll wird auf stabile Lauf-IDs, System- und Datenbankfingerprints, rohe und validierte Modellübergaben, Retrievaltrace, Evidenzauswahl, Laufzeiten und Tokenfelder geprüft. Die Tests erzeugen keine dauerhaften Laufprotokolle im Repository.

## Ausführung

Vollständige Testsuite:

```bash
python -m pytest
```

Wenn das globale temporäre Windows-Verzeichnis nicht beschreibbar ist, kann ein lokales Testverzeichnis verwendet werden:

```bash
python -m pytest --basetemp=.pytest-public-temp
```

Zusätzlich wird der Quellcode kompiliert:

```bash
python -m compileall src
```

## Abschlusskriterien

Eine Änderung ist technisch abgeschlossen, wenn die zugehörigen Tests vorhanden sind, die vollständige Testsuite besteht, `git diff --check` keine Fehler meldet und Änderungen an Schema, Verträgen oder Schnittstellen in den technischen Dokumenten nachvollzogen sind. Temporäre Testartefakte dürfen nicht im Repository verbleiben.

