# ADR 02: Zwei Phasen

## Status

accepted

## Kontext

Aufbau und Nutzung der Wissensbasis stellen unterschiedliche Anforderungen an Datenzugriff, Validierung und fachliche Verantwortung. Während der Wissensaufbau schreibende und prüfende Verarbeitungsschritte benötigt, soll der spätere Betrieb einen vorbereiteten Datenbestand ausschließlich kontrolliert und lesend verwenden.

## Entscheidung

Das System trennt zwei Verarbeitungspipelines innerhalb der gemeinsam bereitgestellten Anwendung.

### Konstruktionsphase

- Dokumente werden importiert, nach ihrer logischen Struktur segmentiert und mit Locatoren versehen.
- Sprachmodelle unterstützen die Erzeugung strukturierter Aussagekandidaten.
- Python validiert Schemata, Werte und Quellenbezüge deterministisch.
- Fachliche Prüfung und technischer Validierungsstatus bleiben unterscheidbar.
- Validierte Inhalte werden in der relationalen Wissensbasis gespeichert und indexiert.

### Betriebsphase

- Ein lokales Sprachmodell überführt die Nutzerfrage in einen strukturierten `SearchIntent`.
- Die Anwendung validiert den Suchauftrag und führt ausschließlich freigegebene, lesende Suchwege aus.
- Retrieval, Evidenzauswahl und Antwortformatierung arbeiten auf dem vorbereiteten operativen Datenbestand.
- Prüfstatus und Quellenbezüge bleiben in den Evidenzen sichtbar.
- Such- und Antwortläufe werden getrennt von der Wissensbasis protokolliert.

Die Betriebsphase verändert weder Dokumentinhalte noch fachliche Aussagen oder deren Status.

## Konsequenzen

- Schreibende Wissenskonstruktion und lesender Betrieb besitzen eine klare technische Sicherheitsgrenze.
- Der operative Datenbestand bleibt während der Nutzung stabil und Abfragen sind besser rekonstruierbar.
- Dokumentänderungen, neue Versionen und fachliche Korrekturen müssen erneut konstruiert, validiert und in den operativen Bestand übernommen werden.
- Der kontrollierte Wissensaufbau verursacht zusätzlichen Vorbereitungs- und Prüfaufwand.
- Fehler oder Lücken der Konstruktionsphase können in der Betriebsphase nicht durch freie Modellergänzungen ausgeglichen werden.
