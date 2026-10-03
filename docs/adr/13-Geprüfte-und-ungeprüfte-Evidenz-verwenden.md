# ADR 13: Geprüfte und ungeprüfte Evidenz verwenden

## Status

accepted

## Kontext

Ein ausschließlich fachlich bestätigter Datenbestand würde eine vollständige Prüfung zur Betriebsvoraussetzung machen und technisch valide, quellengebundene Evidenz ungenutzt lassen.

## Entscheidung

Es gibt einen operativen Evidenzbestand aus allen quellengebundenen und technisch validen Aussagekandidaten außer fachlich verworfenen Kandidaten.

- Bestätigte Kandidaten verwenden geprüfte Werte und die Kennzeichnung `fachlich_geprüft`.
- Kandidaten mit `needs_review` oder ohne bestätigte Prüfung verwenden Rohwerte und Originalpassagen mit der Kennzeichnung `automatisch_extrahiert_ungeprüft`.
- Der Prüfstatus beeinflusst Wertauswahl und Kennzeichnung, aber nicht die Rangfolge.
- Ungeprüfte Relationen bleiben Modellvorschläge. Es gibt keine operative Relationsnavigation; `fulfills` bleibt ausgeschlossen.
- Gold-, Referenz- und Kalibrierungsdaten bleiben getrennt.

ADR 13 ersetzt die operativen Einschränkungen aus ADR 10 bis ADR 12. Provenienz, Prüfkorrekturen, parametrisierte Abfragen und lesender Zugriff bleiben erhalten.

## Konsequenzen

- Die Abdeckung steigt, ohne fachlich verworfene Inhalte einzubeziehen.
- Jede Evidenz weist Dokument, Version, Locator und Prüfstatus aus.
- Ungeprüfte Inhalte können fehlerhaft sein und müssen sichtbar gekennzeichnet bleiben.
- Aus ihnen darf keine fachliche Freigabe oder Konformitätsaussage abgeleitet werden.
- Bereinigte Sichten bleiben für Qualitätssicherung und Tests nutzbar.
