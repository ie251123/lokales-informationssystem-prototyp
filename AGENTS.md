# Arbeitsregeln für Entwicklungsagenten

## Projektziel

Das Repository enthält einen Minimalprototyp für ein lokal betriebenes Informationssystem mit relationaler Wissensbasis und natürlicher Sprachschnittstelle. Änderungen müssen den nachvollziehbaren, quellengebundenen und lokal ausführbaren Systemkern erhalten.

## Verbindliche Architekturregeln

- Konstruktionsphase und Betriebsphase bleiben getrennt.
- Die Betriebsphase arbeitet ausschließlich lesend auf der vorbereiteten SQLite-Datenbank.
- SQLite und FTS5 bilden den zentralen Retrievalmechanismus.
- Das lokale Sprachmodell ist nur Vermittlungsschicht für `SearchIntent` und Evidence Selection.
- Das Modell erzeugt weder freies SQL noch Shell-Befehle oder freien Endantworttext.
- Alle Modell- und Nutzereingaben werden gegen begrenzte Verträge validiert.
- Antworten entstehen ausschließlich aus zurückgegebener Evidenz.
- Jede verwendete Aussage bleibt auf Dokument, Version und Locator zurückführbar.
- Unzureichende oder widersprüchliche Evidenz wird sichtbar ausgewiesen.
- Fachlich verworfene Kandidaten werden nicht operativ verwendet.
- Die Betriebsphase erzeugt keine fachlichen Daten und verändert keinen Prüfstatus.

## Technischer Rahmen

| Bereich | Festlegung |
|---|---|
| Sprache | Python 3.11 |
| Datenbank | SQLite, Schema-Version 4 |
| Volltextsuche | SQLite FTS5 |
| Lokales Modell | OpenAI-kompatible lokale Schnittstelle; Qwen3 als Referenz |
| Tests | `pytest` mit synthetischen Fixtures und Modell-Stubs |
| Versionskontrolle | Git |

Die operative Datenbank, Quelldokumente, Modellartefakte und Laufprotokolle gehören nicht in das Repository. Pfade und Laufzeitparameter müssen konfigurierbar bleiben.

## Konstruktionsphase

Zulässig sind Import, Textextraktion, dokumentlogische Segmentierung, Quellenadressierung, Schema- und Indexaufbau sowie der kontrollierte Import fachlicher Aussagekandidaten. LLM-Ausgaben dürfen erst nach Schema-, Bindungs- und Prüfsummenvalidierung gespeichert werden.

Neue Kandidaten erhalten `needs_review`. Fachliche Bestätigungen und Korrekturen werden ausschließlich über versionierte Review-Overlays ergänzt. Importierte Rohdaten und Provenienz dürfen nicht überschrieben oder stillschweigend umgedeutet werden. Schreibvorgänge müssen transaktional sein und bei einem Fehler vollständig zurückgerollt werden.

## Betriebsphase

Der `SearchIntent` darf nur bekannte Retrievalpfade, Suchbegriffe, Filter und Grenzwerte enthalten. Python erzeugt daraus parametrisierte SQL- und FTS5-Abfragen. Tool Retrieval ist nur als kontrollierte, lesende Ergänzung über eine feste Registry zulässig. Freie Shell-Strings, `shell=True`, unbeschränkte Pfade und vom Modell erfundene Werkzeuge sind verboten.

Der operative Evidenzbestand umfasst bestätigte und technisch valide ungeprüfte Kandidaten; `rejected` bleibt ausgeschlossen. Der Prüfstatus muss in der Ausgabe sichtbar sein und darf das Ranking nicht automatisch verbessern. Relationen sind ohne gesonderte fachliche Prüfung keine belastbaren Tatsachen und dürfen keine Erfüllungs-, Konformitäts- oder Rechtsbehauptungen begründen.

Nach dem Retrieval muss der Hauptankerwächter angewendet werden. Die Evidence Selection darf nur vorhandene Evidence-IDs gültigen Facetten zuordnen. Die Endantwort wird deterministisch formatiert und darf keine Inhalte außerhalb der validierten Evidenzauswahl ergänzen.

## Implementierungsregeln

- Halte Module klein und Verantwortlichkeiten klar getrennt.
- Bevorzuge reine Funktionen für Parsing, Validierung, Retrievalplanung und Formatierung.
- Bündele Datei-I/O, Datenbankzugriffe, Modellaufrufe und externe Programme an klaren Schnittstellen.
- Verwende Typannotationen für öffentliche Funktionen.
- Führe Fehler kontrolliert zurück und protokolliere keine unnötigen Volltexte.
- Führe keine neuen Frameworks, Netzwerkdienste oder Infrastrukturkomponenten ohne begründete Architekturentscheidung ein.
- Bewahre bestehende IDs, JSON-Verträge, Datenbankmigrationen und Protokollversionen kompatibel oder versioniere Änderungen ausdrücklich.
- Verändere keine fachlichen Daten, Fixtures oder Verträge nur, um einen Test bestehen zu lassen.

## Tests

Jede funktionale Änderung benötigt passende automatisierte Tests. Verwende synthetische Fixtures, temporäre Datenbanken und Modell-Stubs; ein echtes lokales Modell und die operative Datenbank sind für die Tests nicht erforderlich.

Vor Abschluss einer Änderung sind mindestens auszuführen:

```bash
python -m compileall src
python -m pytest
git diff --check
```

## Dokumentationspflichten

Aktualisiere bei Änderungen die unmittelbar betroffenen Dateien:

- Ziel und Umfang: `docs/project_brief.md`
- Anforderungen: `docs/requirements.md`
- Architektur oder Retrieval: `docs/architecture.md` und passende ADR
- KI-Einsatz und Modellverträge: `docs/ai_contract.md`
- Datenbankschema: `docs/data_model.md`
- Tests: `docs/testing_strategy.md`
- protokollierte Laufwerte: `docs/evaluation.md`

## Abschlusskriterien

Eine Änderung ist abgeschlossen, wenn Tests und Diff-Prüfung erfolgreich sind, Phasentrennung und Quellenbindung erhalten bleiben, keine unkontrollierten LLM-, Datenbank- oder Toolpfade entstehen und die technische Dokumentation dem implementierten Stand entspricht.
