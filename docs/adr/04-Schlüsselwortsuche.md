# ADR 04: Schlüsselwortsuche

## Status

accepted

## Kontext

Das relationale Retrieval und die Volltextsuche mit FTS5 greifen auf strukturierte beziehungsweise indexierte Inhalte zu. Für die unmittelbare und nachvollziehbare Kontrolle aufbereiteter Originaltexte ist zusätzlich ein einfacher, indexfreier Suchweg sinnvoll. Eine freie Shell- oder Werkzeugnutzung durch das Sprachmodell wäre dafür unnötig und würde den kontrollierten Datenzugriff aufheben.

## Entscheidung

- Eine Schlüsselwortsuche mit `ripgrep` (`rg`) wird als optionaler Ergänzungs- und Kontrollsuchweg bereitgestellt.
- Der Suchraum ist auf ausdrücklich freigegebene, lokal aufbereitete Textdateien begrenzt.
- Suchbegriff, Pfad, Trefferzahl und weitere Parameter werden durch die Anwendung validiert. Das Sprachmodell erhält weder eine freie Shell noch die Möglichkeit, beliebige Befehle auszuführen.
- Die Schlüsselwortsuche arbeitet ausschließlich lesend und verändert keine Dateien oder Datenbankinhalte.
- Ein Texttreffer wird erst dann als Evidenz verwendet, wenn er eindeutig auf eine gespeicherte Quellenpassage zurückgeführt werden kann.
- SQL und FTS5 bleiben die primären Suchwege. Die Schlüsselwortsuche ersetzt sie nicht und ist für den regulären Antwortpfad nicht erforderlich.

## Konsequenzen

- Der Suchweg ist lokal, transparent und mit geringem Infrastrukturaufwand prüfbar.
- Er kann Textstellen auffinden, die nicht ausreichend strukturiert oder indexiert wurden.
- Die Suche erkennt keine semantische Ähnlichkeit und hängt stark von Schreibweise und Suchbegriffen ab.
- Die Rückführung auf eine gespeicherte Quellenpassage verursacht zusätzlichen Validierungsaufwand.
- Der kontrollierte Funktionsumfang ist geringer als bei einer autonomen Werkzeug- oder Agentennutzung.
