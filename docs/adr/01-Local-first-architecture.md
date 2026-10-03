# ADR 01: Local-first architecture

## Status

accepted

## Kontext

Projektunterlagen können personenbezogene Daten sowie vertrauliche Informationen zu Planung, Kosten und Verträgen enthalten. Datenbank, Suchanfragen, Verarbeitungsergebnisse und Protokolle sollen deshalb unter der Kontrolle des Betreibers bleiben. Die Betriebsphase darf nicht von einem externen Modell- oder Cloud-Dienst abhängig sein.

## Entscheidung

- Die Betriebsphase wird vollständig in einer lokal kontrollierten Umgebung ausgeführt.
- Wissensbasis und Volltextindex werden lokal mit SQLite und FTS5 betrieben.
- Das Sprachmodell wird über eine lokale HTTP-Schnittstelle auf demselben Rechner beziehungsweise innerhalb der kontrollierten lokalen Umgebung angebunden.
- Modellzugriffe in der Betriebsphase sind auf den konfigurierten lokalen Endpunkt begrenzt. Proxy-Nutzung und Weiterleitungen sind deaktiviert.
- Reale vertrauliche Projektdaten dürfen das lokale System nicht verlassen.
- Externe Sprachmodelle dürfen in der Konstruktionsphase nur mit öffentlichen, synthetischen, wirksam anonymisierten oder ausdrücklich freigegebenen Daten verwendet werden.

## Konsequenzen

- Daten, Suchanfragen und Protokolle bleiben lokal verfügbar und kontrollierbar.
- Die Betriebsphase funktioniert ohne externe Modell-API und verursacht keine laufenden API-Kosten.
- Modellgröße, Antwortzeit und mögliche Parallelität werden durch die lokale Hardware begrenzt.
- Installation, Modellbetrieb, Zugriffsschutz, Datensicherung und Wartung liegen in der Verantwortung des Betreibers.
- Lokale Verarbeitung allein gewährleistet weder Datenschutz noch Informationssicherheit; dafür sind zusätzliche organisatorische und technische Maßnahmen erforderlich.
