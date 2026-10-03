# ADR 12: Nur geprüfte Evidenz abrufen

## Status

ungültig, wird von ADR 13 überschrieben

## Kontext

Die fachlich geprüften Korrekturen waren nicht in den bestehenden FTS5-Tabellen enthalten. Für die Betriebsphase wurde deshalb ein kontrollierter Zugriff auf den bereinigten, ausschließlich geprüften Datenbestand vorgesehen.

## Entscheidung

`find_evidence` sollte ausschließlich die bereinigten Datenbanksichten verwenden. Eine reproduzierbare FTS5-Projektion im Arbeitsspeicher indexierte die geprüften Aussagen und getrennten Evidenzpassagen. SQL-Abfragen blieben parametrisiert und die Datenbank wurde ausschließlich lesend geöffnet.

Das Evidenzpaket sollte geprüfte Werte, Originalpassagen, Dokumentversionen und Locatoren enthalten. Ungeprüfte oder verworfene Kandidaten wurden vollständig ausgeschlossen.

## Konsequenzen

- Der Zugriff war kontrolliert, reproduzierbar und quellentreu, setzte aber eine vollständige fachliche Prüfung voraus.
- Technisch valide ungeprüfte Evidenz blieb ungenutzt und verringerte die Abdeckung.
- ADR 13 ersetzt den ausschließlichen Clean-Set-Zugriff durch einen inklusiven operativen Evidenzbestand.
- Parametrisierte Abfragen, lesender Datenbankzugriff und genaue Quellenbezüge bleiben weiterhin verbindlich.
