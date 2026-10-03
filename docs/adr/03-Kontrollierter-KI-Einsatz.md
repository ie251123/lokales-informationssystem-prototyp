# ADR 03: Kontrollierter KI-Einsatz

## Status

accepted

## Kontext

Sprachmodelle eignen sich für die Analyse und Interpretation unterschiedlicher Formulierungen. Ihre Ausgaben sind jedoch nicht deterministisch und können falsch, unbelegt oder unvollständig sein. Quellenbindung, kontrollierter Datenzugriff und technische Rekonstruierbarkeit dürfen deshalb nicht allein vom Modell abhängen.

## Entscheidung

- Sprachmodelle werden nur an sprachlich mehrdeutigen Übergängen eingesetzt: in der Konstruktionsphase zur Erzeugung von Aussagekandidaten und in der Betriebsphase zur Ermittlung des `SearchIntent` sowie zur Evidenzauswahl.
- Modellausgaben müssen festgelegten JSON-Schemata, zulässigen Werten und technischen Verträgen entsprechen. Python validiert sie vor der weiteren Verarbeitung.
- Das Sprachmodell erzeugt kein SQL. Die Anwendung erstellt ausschließlich parametrisierte SQL- und FTS5-Abfragen aus freigegebenen Suchmustern.
- Die natürliche Sprachschnittstelle löst nur lesende Operationen aus und erhält keinen Schreibzugriff auf die Wissensbasis.
- Für die Antwort dürfen ausschließlich validierte, ausgewählte Evidenzen verwendet werden. Allgemeines Modellwissen ist kein Quellenbeleg.
- Ein deterministischer Hauptankerwächter sichert die maßgebliche Evidenz ab. Die endgültige Antwort wird anschließend deterministisch mit Quellenangaben formatiert.
- Fehlende oder unzureichende Evidenz führt zu einer sichtbaren Begrenzung oder Verweigerung der Antwort.
- Rohe und validierte Modellausgaben sowie die nachfolgenden Verarbeitungsschritte werden getrennt protokolliert.

## Konsequenzen

- Unkontrollierte Datenbankzugriffe und unbelegte Antwortaussagen werden technisch begrenzt.
- Quellenbezug und Verarbeitungsschritte bleiben trotz modellbasierter Teilschritte überprüfbar.
- Das System bevorzugt begrenzte oder unvollständige Antworten gegenüber freien Ergänzungen.
- Die sprachliche und semantische Flexibilität ist geringer als bei einem frei generierenden System.
- Prompts, Schemata, Suchverträge und Validatoren müssen gemeinsam versioniert, getestet und gepflegt werden.
- Die interne Entscheidungsbildung des Sprachmodells bleibt nicht vollständig erklärbar oder identisch reproduzierbar.
