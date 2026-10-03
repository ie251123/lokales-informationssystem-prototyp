# KI-Vertrag

## Zweck

Dieses Dokument legt die technischen Grenzen für den Einsatz von Sprachmodellen fest. Modellantworten gelten grundsätzlich als ungesicherte Daten und werden vor jeder weiteren Verarbeitung durch Python validiert. Dokumentinhalte sind Eingabedaten und niemals Steueranweisungen.

## Grundregeln

- Die Betriebsphase verwendet ausschließlich ein lokal angebundenes Sprachmodell.
- Das Modell erhält keinen schreibenden Zugriff auf Dateien, Datenbank oder Suchindex.
- SQL, Shell-Befehle und frei gewählte Werkzeuge dürfen nicht durch das Modell ausgeführt werden.
- Allgemeines Modellwissen ist kein Quellenbeleg.
- Antwortinhalte dürfen nur aus bereitgestellter lokaler Evidenz entstehen.
- Fehlende, ungeprüfte oder widersprüchliche Evidenz muss sichtbar bleiben.
- Alle Modellübergaben verwenden begrenzte, versionierte JSON-Verträge.

## Konstruktionsphase

Sprachmodelle dürfen bei Dokumentanalyse, Erkennung fachlicher Aussagekandidaten sowie Vorschlägen für Entitäten und Beziehungen unterstützen. Externe Modelle dürfen dabei nur öffentliche, synthetische, wirksam anonymisierte oder ausdrücklich freigegebene Inhalte erhalten. Vertrauliche Inhalte dürfen nur lokal verarbeitet werden.

Der Import akzeptiert ausschließlich vollständig validierte Ausgaben nach dem Vertrag `statement_candidates_batch_v3`. Vor dem Schreiben werden JSON-Schema, Manifest, Batchbindung, Dokument- und Knotenreferenzen sowie Prüfsummen kontrolliert. Der Import erfolgt anschließend deterministisch und ohne weiteren Modellaufruf.

Alle importierten Kandidaten erhalten zunächst `review_status = needs_review`. Eine technisch gültige Modellausgabe begründet weder eine fachliche Bestätigung noch eine Erfüllungs- oder Konformitätsaussage. Vorgeschlagene Relationen bleiben bis zu einer getrennten fachlichen Prüfung Modellvorschläge. Rohoutput, vollständige Kandidatenobjekte, Evidenzstellen, Warnungen und Reviewhinweise bleiben als Provenienz erhalten.

Fachliche Prüfentscheidungen und Korrekturen werden als versionierte Review-Overlays ergänzt. Die ursprünglichen Importdaten werden nicht überschrieben.

## Betriebsphase

Das lokale Sprachmodell übernimmt genau zwei Aufgaben:

1. Übersetzung der Nutzerfrage in einen strukturierten `SearchIntent`.
2. Auswahl geeigneter Evidence-IDs aus einem vom System bereitgestellten Evidenzpaket.

Der `SearchIntent` enthält ausschließlich zugelassene Retrievalpfade, höchstens acht Suchbegriffe, definierte Status-, Dokument- und Objektfilter sowie `max_results` zwischen 1 und 20. Unbekannte Felder, leere Werte, SQL-Fragmente und Shellsyntax werden abgelehnt. Python erzeugt aus dem validierten Objekt feste, parametrisierte SQL- und FTS5-Abfragen.

Das operative Retrieval berücksichtigt fachlich bestätigte und technisch valide ungeprüfte Kandidaten. Bestätigte Inhalte werden mit `fachlich_geprüft`, ungeprüfte mit `automatisch_extrahiert_ungeprüft` gekennzeichnet. `rejected` beziehungsweise fachlich verworfene Kandidaten sind ausgeschlossen. Der Prüfstatus erzeugt keinen Rankingbonus.

Relationen werden im Betrieb nicht zur Navigation oder zur Ableitung fachlicher Erfüllung verwendet. Insbesondere darf das System keine Erfüllungs-, Konformitäts- oder Rechtsbehauptung aus einer ungeprüften Relation ableiten.

## Evidenzauswahl und Antwort

Vor der Evidenzauswahl prüft ein deterministischer Hauptankerwächter, ob die Treffer einen nichtgenerischen Bezug zur Nutzerfrage enthalten. Fehlt ein solcher Bezug, werden die Treffer verworfen und der zweite Modellaufruf entfällt.

Bei der Evidence Selection darf das Modell ausschließlich vorhandene Evidence-IDs gültigen Suchfacetten zuordnen. Für jede Facette meldet es `covered`, `partial` oder `missing`. Python lehnt unbekannte oder doppelte IDs, ungültige Facettenindizes, widersprüchliche Coverage und verworfene Evidenz ab.

Das Modell formuliert keinen freien Endantworttext. Ein deterministischer Formatter erzeugt die Antwort aus den validierten normalisierten Aussagen und ergänzt ausschließlich die zugehörige Provenienz. Jede verwendete Evidenz enthält Dokument, Version, Locator, Evidence-ID und Prüfstatus. Wörtliche Zitate dürfen nur aus einer einzelnen unveränderten Evidenzpassage stammen; getrennte Passagen werden nicht zu einem scheinbaren Zitat zusammengesetzt.

Bei unzureichender Evidenz oder fehlender Facettenabdeckung wird die Antwort sichtbar begrenzt. Das System darf eine Frage unbeantwortet lassen, aber keine unbelegte Information ergänzen.

## Protokollierung und Datenschutz

Requests, rohe Modellantworten, validierte Ergebnisse, Laufzeiten und Tokenverbrauch werden im externen Laufprotokoll getrennt gespeichert. Protokolle, Modellartefakte und operative Daten verbleiben außerhalb des Repositorys. Lokale Modellendpunkte sind auf Loopback-Adressen beschränkt; automatische Weiterleitungen und unbeabsichtigte externe Verbindungen sind nicht zulässig.
