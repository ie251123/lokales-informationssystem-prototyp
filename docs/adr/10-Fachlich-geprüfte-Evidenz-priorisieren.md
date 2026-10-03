# ADR 10: Fachlich geprüfte Evidenz priorisieren

## Status

ungültig, wird von ADR 13 überschrieben

## Kontext

Technisch validierte Aussage- und Relationskandidaten sind nicht automatisch fachlich richtig. Für Suche und Antwortbildung musste daher festgelegt werden, wie fachlich geprüfte und ungeprüfte Inhalte behandelt werden.

## Entscheidung

Fachlich bestätigte Aussagen und Relationen sollten gegenüber ungeprüften Modellvorschlägen priorisiert werden. Bei verbindlichen Anforderungen mussten Normalisierung, Modalität, Zahlen, Einheiten, Bedingungen und Ausnahmen vollständig erhalten bleiben. Die Originalpassage blieb der maßgebliche Quellenbeleg.

Relationsvorschläge sollten erst nach einer gesonderten fachlichen Bestätigung als belastbare Zusammenhänge gelten. Die Bestätigung einer Aussage bestätigte nicht automatisch ihre Relationen.

## Konsequenzen

- Die Entscheidung trennte technische Validität und fachliche Bestätigung.
- Ihre vorgesehene Priorisierung nach Prüfstatus gilt nicht mehr.
- ADR 13 lässt auch ungeprüfte, aber technisch valide und quellengebundene Aussagen zu und kennzeichnet deren Prüfstatus sichtbar.
- Die Anforderungen an unveränderte Quellenpassagen und getrennt geprüfte Relationen werden in ADR 13 weitergeführt.
