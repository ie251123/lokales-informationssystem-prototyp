# ADR 05: Fach- und Evidenzmodell

## Status

accepted

## Kontext

Für quellengebundene Antworten genügt es nicht, Dokumenttexte nur als unstrukturierte Segmente zu speichern. Das System muss Herkunft, Version, Fundstelle und Prüfstatus einer Aussage ebenso abbilden wie ihre fachliche Bedeutung. Diese Informationen sollen dauerhaft konsistent, filterbar und technisch prüfbar sein.

## Entscheidung

Das relationale Datenmodell besteht aus zwei Ebenen:

- Der generische Quellen- und Evidenzkern verwaltet Projekte, Dokumente, Dokumentversionen, Dokumentstellen, Aussagen, Quellenbezüge, Status, Suchläufe und verwendete Evidenzen.
- Das anwendungsspezifische Fachmodell ergänzt fachliche Objekte, Klassifikationen, Aliase, Werte, Bedingungen und ausgewählte Beziehungen.

Originalpassage und normalisierte Aussage werden getrennt gespeichert. Jede Aussage wird mit ihrer Dokumentversion und einer konkreten Fundstelle verbunden. Aussagearten wie Anforderung, Feststellung, Entscheidung und Planungsmaßnahme bleiben unterscheidbar. Zusätzlich werden Verbindlichkeits-, Lebenszyklus- und Prüfstatus explizit geführt.

Beziehungen zwischen Aussagen können gespeichert werden. Aus ihnen wird jedoch keine automatische Erfüllungs-, Konformitäts- oder Rechtsbewertung abgeleitet. Erweiterungen des Fachmodells erfolgen kontrolliert, während der generische Quellen- und Evidenzkern stabil bleibt.

## Konsequenzen

- Quellenherkunft, Version, Status und fachliche Bedeutung können gemeinsam abgefragt werden.
- Strukturierte Filter und Volltextsuche greifen auf dieselbe quellengebundene Wissensbasis zu.
- Antwortaussagen lassen sich bis zur gespeicherten Originalpassage zurückverfolgen.
- Datenqualität und Suchergebnis hängen von Schemaqualität, Extraktionsabdeckung und fachlicher Prüfung ab.
- Neue Themen, Dokumentarten oder Fachbereiche können zusätzliche Klassifikationen, Extraktionsregeln und Schemaerweiterungen erfordern.
- Die explizite Modellierung erhöht den Aufwand für Konstruktion, Validierung und Pflege der Wissensbasis.
