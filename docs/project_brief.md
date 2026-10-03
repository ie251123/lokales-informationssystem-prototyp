# Project Brief

## Zweck

Der Prototyp erschließt ausgewählte, fachlich relevante Dokumente in einer lokalen relationalen Wissensbasis und macht die gespeicherten Inhalte über natürliche Sprache zugänglich. Im Mittelpunkt stehen drei technische Ziele:

- Quellen, Dokumentversionen, Fundstellen und Prüfstatus dauerhaft nachvollziehbar speichern,
- natürlichsprachliche Fragen in kontrollierte Suchaufträge überführen,
- Antworten ausschließlich aus ausgewählten, quellengebundenen Evidenzen erzeugen.

Die Anwendung kombiniert Python, SQLite und FTS5 mit einem lokal angebundenen Sprachmodell. Deterministische Anwendungskomponenten validieren Modellausgaben, erzeugen parametrisierte Datenbankabfragen und begrenzen den Zugriff auf freigegebene, lesende Suchwege.

## Funktionsumfang

Der implementierte Prototyp bildet einen abgegrenzten Brandschutzfall mit Schwerpunkt auf Türen in notwendigen Treppenräumen ab. Der Referenzbestand umfasst:

- ein vollständiges, strukturiert erfasstes Brandschutzkonzept,
- § 35 Abs. 6 der Sächsischen Bauordnung als öffentliche Rechtsquelle,
- einen anonymisierten Tabellenkopf und die Zeile T 010 einer Türliste.

Die Dokumente wurden über dokumenttypspezifische beziehungsweise kontrolliert vorbereitete Importwege aufgenommen. Eine allgemeine Importpipeline für beliebige Dokumente ist nicht Bestandteil des Systems.

Die Verarbeitung ist in zwei Bereiche getrennt:

### Konstruktion der Wissensbasis

Dokumente werden strukturiert, nach ihrer logischen Gliederung segmentiert und mit stabilen Fundstellen versehen. LLM-gestützt erzeugte Aussagekandidaten werden gegen feste Schemata validiert und mit Originaltext, normalisiertem Suchtext, Dokumentversion und Statusinformationen gespeichert. Fachliche Prüfung und technische Validierung bleiben voneinander unterscheidbar.

### Lokale Abfrage

Ein lokales Sprachmodell übersetzt eine Nutzerfrage in einen strukturierten `SearchIntent`. Die Anwendung prüft diesen gegen ein festes Schema und führt ausschließlich freigegebene, parametrisierte SQL- und FTS5-Abfragen aus. Aus den Treffern wird ein Evidenzpaket gebildet. Die Antwort darf nur Aussagen verwenden, die durch dieses Paket belegt sind. Reicht die Evidenz nicht aus, wird die Antwort begrenzt oder verweigert.

Für den Betriebsweg stehen insbesondere die Befehle `ask-local` zur natürlichsprachlichen Fragebeantwortung und `find-evidence` zur kontrollierten Evidenzsuche bereit. Suchabsicht, Retrievalschritte, Evidenzauswahl und Quellenbezüge werden protokolliert.

## Datenmodell

Das relationale Modell verbindet einen generischen Quellen- und Evidenzkern mit einem kleinen anwendungsspezifischen Fachmodell. Es bildet insbesondere Projekte, Dokumente, Versionen, Dokumentstellen, Aussagen, Quellenbezüge, Klassifikationen und ausgewählte Relationen ab. Aussagen werden nach Aussageart sowie nach Verbindlichkeits-, Lebenszyklus- und Prüfstatus unterschieden.

Originalpassage und normalisierte Aussage bleiben getrennt gespeichert. Dadurch kann eine strukturierte Aussage jederzeit auf Dokument, Version und Fundstelle zurückgeführt und mit dem Quelltext verglichen werden.

## Rolle der Sprachmodelle

Sprachmodelle werden nur an sprachlich mehrdeutigen Übergängen eingesetzt. In der Konstruktionsphase unterstützen sie die Strukturanalyse und Aussageextraktion. In der Betriebsphase unterstützen sie die Interpretation der Frage und die Auswahl geeigneter Evidenz innerhalb der vorgegebenen Verträge.

Das Sprachmodell erzeugt weder freies SQL noch erhält es Schreibzugriff auf die Wissensbasis. Zulässige Felder, Werte, Suchwege und Ausgabeformate werden durch Python validiert. Allgemeines Modellwissen gilt nicht als Quellenbeleg.

## Nicht-Ziele

Der Prototyp ist kein produktionsreifes Dokumentenmanagement-, Anforderungs- oder Planungssystem. Nicht umgesetzt sind insbesondere:

- eine vollständige oder fehlerfreie automatische Erschließung beliebiger Dokumentbestände,
- eine allgemeine Importunterstützung für PDF-, Office-, E-Mail-, OCR-, BIM- oder IFC-Daten,
- ein universelles Fachschema für alle Projektarten, Fachbereiche oder Leistungsphasen,
- automatische Erfüllungs-, Konformitäts-, Rechts- oder Planungsbewertungen,
- freies Text-to-SQL, schreibende Sprachbefehle oder autonome Agentenaktionen,
- semantische Vektorsuche, Embeddings oder eine RAG-Referenzimplementierung,
- operative Relationsnavigation und ein vollständiger Vergleich von Dokumentversionen,
- Weboberfläche, Mehrbenutzerbetrieb, Rollen- und Berechtigungssystem, Skalierung oder Produktionshärtung.

Die Qualität der Ergebnisse bleibt von Dokumentauswahl, Extraktionsabdeckung, Schemaqualität und fachlicher Prüfung abhängig.

## Erfolgskriterien

Der Prototyp erfüllt seinen technischen Zweck, wenn:

1. die ausgewählten Quellen mit Originaltext, Version, Locator und Statusinformationen in der relationalen Wissensbasis vorliegen,
2. natürlichsprachliche Fragen in schema-konforme `SearchIntents` überführt werden,
3. das Retrieval ausschließlich freigegebene, lesende und parametrisierte Suchwege verwendet,
4. Antworten nur aus ausgewählten Evidenzen entstehen und jede inhaltliche Aussage auf eine konkrete Quelle zurückgeführt werden kann,
5. fehlende oder unzureichende Evidenz sichtbar zu einer begrenzten oder verweigerten Antwort führt,
6. die Verarbeitungsschritte und Zwischenprodukte eines Antwortlaufs technisch rekonstruierbar protokolliert werden,
7. der vollständige Betriebsweg in einer dokumentierten lokalen Referenzumgebung ausführbar ist und

## Veröffentlichungsumfang

Das Repository enthält Quellcode, Konfiguration, Schemata, Tests und technische Dokumentation. Die operative SQLite-Datenbank, die verwendeten Quelldokumente, Modellartefakte sowie ausführliche Lauf- und Bewertungsdaten sind nicht Bestandteil des Repositorys. Für automatisierte Tests werden ausschließlich synthetische Testdaten und Modell-Stubs verwendet.