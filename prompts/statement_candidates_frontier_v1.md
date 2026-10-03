Du extrahierst vorläufige Aussagekandidaten aus einem lokalen
Dokumentknotenpaket.

Gib exakt ein JSON-Objekt zurück, das dem untenstehenden JSON-Schema entspricht.
Gib kein Markdown, keine Kommentare und keine erläuternde Prosa zurück.

Verwende ausschließlich die bereitgestellten Passagen aus
`source_nodes[*].source_text`. Verwende keine Entwicklungs-Goldreferenzen, keine
Hold-out-Fragen, keine erwarteten Antworten, kein externes Wissen,
`raw_extracted_text` und kein `search_text`.

Alle Kandidaten müssen `review_status = "needs_review"` behalten. Gib nicht
`confirmed`, `rejected`, `fulfills` oder automatische Konformitätsbewertungen
aus.

## Vertrag

{{CONTRACT_MARKDOWN}}

## JSON-Schema

{{JSON_SCHEMA}}

## Eingabepaket

{{INPUT_PACKAGE}}
