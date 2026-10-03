# Architektur

## Systemüberblick

Der Prototyp ist als lokal betriebener, modularer Monolith in Python umgesetzt. Eine SQLite-Datenbank bildet die zentrale Wissens- und Evidenzbasis; FTS5 ergänzt sie um lexikalische Volltextsuche. Ein lokales Sprachmodell wird über eine OpenAI-kompatible HTTP-Schnittstelle auf dem eigenen Rechner angebunden. Das konkrete Modell ist konfigurierbar.

Die Architektur trennt zwei Verarbeitungspipelines:

- **Konstruktionsphase:** Dokumente importieren, logisch segmentieren, Aussagekandidaten extrahieren und validieren, fachliche Prüfentscheidungen ergänzen und alles quellengebunden speichern.
- **Betriebsphase:** Natürlichsprachliche Fragen interpretieren, den vorbereiteten Datenbestand kontrolliert und ausschließlich lesend durchsuchen, Evidenz auswählen und eine belegte Antwort ausgeben.

Die operative SQLite-Datenbank, Quelldokumente, Modellartefakte und Laufprotokolle werden außerhalb des Repositorys gespeichert. Der Datenbankpfad und die Verbindung zum lokalen Modell werden über die Laufzeitkonfiguration festgelegt.

## Konstruktionsphase

```text
Dokumente
→ Text- und Tabellenextraktion
→ dokumentlogische Segmentierung und Locator
→ fachliche Aussagekandidaten
→ technische Validierung und Import
→ fachliche Prüfung und versionierte Korrekturen
→ SQLite-Wissensbasis
```

Dokumente werden in logisch abgegrenzte Einheiten wie Abschnitte, Absätze oder Tabellenzeilen zerlegt. Jede Einheit erhält eine stabile Quellenadressierung. Originaltext, Suchtext und normalisierte Aussage bleiben getrennt, damit fachliche Strukturierung und Quelle nachvollziehbar verbunden sind.

Extrahierte Aussagekandidaten werden vor dem Schreiben gegen definierte JSON-Schemata, Dokument- und Knotenbindungen sowie Prüfsummen validiert. Der Import bewahrt neben den normalisierten Daten auch die vollständigen Kandidaten- und Relationsobjekte. Wiederholte identische Importe sind idempotent; widersprüchliche Inhalte mit gleicher ID führen zum Abbruch.

Fachliche Prüfungen verändern die Importdaten nicht. Bestätigungen, Zurückweisungen und Korrekturen werden als versionierte Review-Overlays ergänzt. Schreibvorgänge erfolgen transaktional und werden durch Integritäts- und Fremdschlüsselprüfungen abgesichert.

## Betriebsphase

```text
Nutzerfrage
→ SearchIntent durch lokales Sprachmodell
→ Schema- und Parameterprüfung
→ SQL-/FTS5-Retrieval
→ Evidenzpaket
→ Hauptankerwächter
→ Evidence Selection durch lokales Sprachmodell
→ Auswahlvalidierung
→ deterministische Antwortformatierung
→ Laufprotokoll
```

Das Sprachmodell übersetzt die Frage zunächst in einen strukturierten `SearchIntent`. Die Anwendung akzeptiert nur vorgegebene Felder, Filter und Grenzwerte. Freies Text-to-SQL, freie Shell-Kommandos und Schreibzugriffe des Modells auf die Datenbank sind ausgeschlossen.

`find_evidence()` kombiniert strukturierte Filter mit lexikalischem Retrieval. Der operative Evidenzbestand enthält fachlich bestätigte und technisch valide ungeprüfte Kandidaten; verworfene Kandidaten sind ausgeschlossen. Für die gewichtete Kontextsuche wird im Arbeitsspeicher eine FTS5-Projektion aus Aussage, Originalpassage, Überschriften, Dokumentmerkmalen sowie Objekt- und Wertekontext erzeugt. Die Ergebnisse können kontrolliert mit der bestehenden Exact-/Präfixsuche zusammengeführt werden. Zusätzliche Suchwerkzeuge dürfen nur über eine kleine Registry freigegebener, lesender Funktionen aufgerufen werden.

Vor der zweiten Modellinteraktion prüft ein deterministischer Hauptankerwächter, ob mindestens eine Evidenz einen nichtgenerischen Bezug zur Frage besitzt. Das Sprachmodell darf anschließend nur IDs aus dem bereitgestellten Evidenzpaket auswählen und die Abdeckung der Suchfacetten kennzeichnen. Python validiert die Auswahl, den Evidenzstatus und die Zuordnung zu den Facetten.

Die Endantwort wird nicht frei durch das Modell erzeugt. Ein deterministischer Formatter übernimmt ausschließlich validierte Aussagen und Quellenangaben aus den ausgewählten Evidenzen. Teilweise oder fehlende Abdeckung wird sichtbar ausgewiesen. Jede verwendete Evidenz enthält Dokument, Version, Locator, Evidenz-ID und Prüfstatus.

## Modulgrenzen

- `construction/` enthält Import, Strukturierung, Validierung und Review-Rückschreibung.
- `db/` kapselt Schema, Migrationen und Datenbankzugriff.
- `operation/` enthält SearchIntent-Verarbeitung, Retrieval, Evidenzauswahl und Antwortformatierung.
- `config/` enthält Laufzeitparameter für Pfade, Modelle und Suchverhalten.
- `prompts/` und `schemas/` definieren die Verträge für modellgestützte Übergaben.

Die Module werden gemeinsam installiert und versioniert, bleiben aber getrennt testbar. Die lokale Modelllaufzeit ist über ihre Schnittstelle austauschbar. Die Betriebsphase verändert keine fachlichen Inhalte der Wissensbasis.

## Technische Grenzen

Der Prototyp umfasst keinen Mehrbenutzerbetrieb, keine differenzierte Rechteverwaltung, keine grafische Bedienoberfläche und keinen automatisierten Produktivbetrieb. Backup, Zugriffsschutz und Bereitstellung der externen Datenbank liegen außerhalb der Anwendung. Die Qualität der Antworten bleibt von der Abdeckung der Dokumentenextraktion und des Fachmodells abhängig.
