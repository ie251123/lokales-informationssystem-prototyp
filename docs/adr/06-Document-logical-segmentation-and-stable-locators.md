# ADR 06: Document-logical segmentation and stable locators

## Status

Accepted

## Context

Rein längenbasierte Chunks verlieren Dokumentstruktur und erschweren präzise Quellenangaben.

## Decision

Dokumente werden nach ihrer logischen Struktur segmentiert.

Unterstützte Einheiten können sein:

- Kapitel,
- Absatz,
- Listeneintrag,
- Tabellenzeile,
- Protokollpunkt.

Jede Quellenstelle erhält einen stabilen lokalen Locator. Originaltext und normalisierter Suchtext werden getrennt gespeichert.

Nicht erkannte Strukturen verwenden einen einfachen dokumentierten Fallback.

## Consequences

- bessere Quellenrekonstruktion,
- präzisere Retrieval- und Evidenzbezüge,
- strukturabhängige Parser oder Adapter erforderlich,
- zusätzlicher Aufwand für unterschiedliche Dokumenttypen.
# ADR 06: Segmentierung und Quellenadressierung

## Status

accepted

## Kontext

Quellenbelege müssen auf eine konkrete und wieder auffindbare Textstelle verweisen. Eine ausschließlich längenbasierte Zerlegung kann Absätze, Listen oder Tabellen trennen, fachliche Zusammenhänge verlieren und die Rekonstruktion der Fundstelle erschweren.

## Entscheidung

- Dokumente werden nach ihrer erkennbaren logischen Struktur segmentiert.
- Mögliche Einheiten sind Kapitel, Unterkapitel, Absatz, Listeneintrag, Tabellenzeile oder Protokollpunkt.
- Die Dokumenthierarchie und die Reihenfolge der Segmente werden in den Dokumentstellen erhalten.
- Jede gespeicherte Quellenpassage erhält einen Locator, der sie innerhalb einer bestimmten Dokumentversion eindeutig adressiert.
- Dokument, Version, Gliederungspfad und verfügbare Positionsangaben bilden gemeinsam den Quellenbezug.
- Originaltext und normalisierter Suchtext werden getrennt gespeichert.
- Nicht erkannte Dokumentstrukturen verwenden ein einfaches, dokumentiertes Ersatzverfahren.
- Eine neue Dokumentfassung wird als eigene Version verarbeitet. Ihre Locatoren werden neu erzeugt; eine automatische versionsübergreifende Gleichsetzung von Fundstellen erfolgt nicht.

## Konsequenzen

- Retrievaltreffer und Antwortaussagen können auf feingranulare Originalpassagen zurückgeführt werden.
- Gliederung und Kontext eines Treffers bleiben für Suche und Quellenangabe verfügbar.
- Unterschiedliche Dokumentstrukturen benötigen angepasste Parser, Profile oder kontrollierte Importpakete.
- Fehler bei Struktur- oder Locatorerkennung wirken sich unmittelbar auf die Genauigkeit der Quellenangabe aus.
- Änderungen an einer Dokumentversion können Locatoren verschieben und erfordern eine erneute Verarbeitung.
- Der Verarbeitungsaufwand ist höher als bei einer rein längenbasierten Segmentierung.
