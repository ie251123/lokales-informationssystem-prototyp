# ADR 08: Lokale Aussageextraktion kalibrieren

## Status

nicht vollständig umgesetzt

## Kontext

Die Aussageextraktion mit einem lokal betriebenen Sprachmodell soll gegen festgelegte JSON-Verträge geprüft werden können, ohne reale Quelldaten an externe Dienste zu übertragen. Dafür wird ein nachvollziehbarer Kalibrierungslauf benötigt.

## Entscheidung

Ein lokaler Kalibrierungslauf darf ausschließlich einen Loopback-Endpunkt ansprechen und keine Proxys oder automatischen Weiterleitungen verwenden. Eingaben und Modellausgaben werden gegen feste JSON-Schemata validiert.

Anfrage, unveränderte Modellantwort, validierte Instanz, Prüfbericht und Laufmanifest werden außerhalb des Repositorys gespeichert. Terminalausgaben enthalten keine vollständigen Quellentexte oder Modellantworten. Der Lauf verändert weder Datenbankschema noch operative Wissensbasis.

Der technische Kalibrierungsweg wurde umgesetzt, jedoch nicht als umfassender Nachweis einer vollständigen lokalen Dokumentextraktion ausgebaut.

## Konsequenzen

- Lokale Modellläufe können strukturiert geprüft und technisch rekonstruiert werden.
- Quelldaten und Modellantworten bleiben außerhalb des öffentlichen Repositorys.
- Die Kalibrierung belegt Vertragstreue und technische Ausführbarkeit, aber keine vollständige Extraktionsqualität.
- Einzelne komplexe Felder und Zuordnungen erfordern weiterhin eine fachliche Prüfung.
