# Lokales Informationssystem mit natürlicher Sprachschnittstelle

## Ziel

Dieses Repository enthält den Prototyp für ein lokal betriebenes Informationssystem auf Datenbankbasis mit natürlicher Sprachschnittstelle.

Der Prototyp soll zeigen, wie Projektwissen aus heterogenen Dokumentenbeständen nachvollziehbar erschlossen, strukturiert gespeichert und zur Unterstützung von Entscheidungsprozessen bereitgestellt werden kann.

## Grundprinzipien

- lokaler Betrieb in der Nutzungs- bzw. Betriebsphase,
- kontrollierter LLM-Einsatz: so viel wie nötig, so wenig wie möglich,
- LLM in der Betriebsphase nur als sprachliche Vermittlungsschicht,
- externe Sprachmodelle nur unterstützend in der Konstruktionsphase und ausschließlich für öffentliche, synthetische, anonymisierte oder ausdrücklich freigegebene Inhalte,
- deterministische Verarbeitung, wo immer möglich,
- SQLite FTS5 als strukturierter Core-Retrieval-Mechanismus,
- ergänzendes Tool Retrieval mit optionalem `rg` und Python-nativem Fallback,
- keine freie Shell-Ausführung aus Nutzer- oder LLM-Eingaben,
- minimale Infrastruktur,
- klare Trennung von Konstruktionsphase und Betriebsphase,
- evidenzbasierte Antworten mit Quellennachweis,
- Entwicklung mit Git und Red/Green TDD.

## Technologieauswahl

| Bereich | Festlegung |
|---|---|
| Programmiersprache | Python 3.11 |
| Datenbank | SQLite |
| Volltextsuche | SQLite FTS5 |
| Tool Retrieval | optional `rg` auf extrahierten Textdateien; Python-Fallback mit `pathlib`/`glob`/`re` |
| Lokales LLM | Qwen3 über eine lokale OpenAI-kompatible Schnittstelle; Modellgröße und Quantisierung sind konfigurierbar |
| Entwicklungsprinzip | Red/Green Test Driven Development |
| Versionskontrolle | Git |

## Dokumentation

| Datei | Zweck |
|---|---|
| `AGENTS.md` | verbindliche Arbeitsanweisungen für Codex und andere Coding Agents |
| `docs/project_brief.md` | Zieldefinition, Umfang und Grenzen |
| `docs/requirements.md` | priorisierter Anforderungskatalog |
| `docs/architecture.md` | Architektur, Phasenmodell, Module und Grenzen |
| `docs/ai_contract.md` | erlaubter und verbotener LLM-Einsatz |
| `docs/data_model.md` | Datenmodell, Quellenmodell und Evidenzmodell |
| `docs/testing_strategy.md` | Teststrategie für Architektur, Retrieval und Antwortlogik |
| [`docs/evaluation.md`](docs/evaluation.md) | Im Evaluationsprotokoll bereitgestellte technische Werte |


## Veröffentlichungsstand

`v1.0-thesis` bezeichnet den bereinigten öffentlichen Stand des Prototyps. Enthalten sind das Schema und die Migrationen bis SQLite-Schema 4, die Konstruktions- und Retrievalmodule, die lokale Sprachschnittstelle, automatisierte Tests sowie die technische Dokumentation.

Der evaluierte interne Systemstand wurde mit `freeze-20260817` festgehalten. Bei der Bereinigung wurden Forschungsdaten, Evaluationsberichte, lokale Pfade und veraltete Ausführungswege entfernt; die evaluierte Kernfunktionalität blieb unverändert.

Die fachlich befüllte operative Datenbank ist nicht Bestandteil des Repositorys. Der archivierte Evaluationsstand besitzt den SHA-256-Hash `978a8045917739815e04898c7e6f2546605ee74464ecd48c9db961908eac588b` und kann auf Anfrage bereitgestellt werden. Eine leere Schema-4-Datenbank lässt sich aus dem veröffentlichten Code reproduzierbar erzeugen.

## Lokale Ausführung

Voraussetzungen:

- Python 3.11
- lokale SQLite-Unterstützung mit FTS5
- für `ask-local`: eine externe Schema-4-Betriebsdatenbank und ein beschreibbares Ausgabeverzeichnis
- für `ask-local`: ein kompatibles Qwen3-Modell über eine lokale OpenAI-kompatible Loopback-Schnittstelle, beispielsweise LM Studio

Installation:

```powershell
python -m pip install -e ".[dev]"
```

Die verfügbaren Parameter der beiden Einstiegspunkte zeigen:

```bash
find-evidence --help
ask-local --help
```

`find-evidence` führt die kontrollierte Evidenzsuche aus. `ask-local` ergänzt SearchIntent, Hauptankerprüfung, Evidence Selection, deterministische Antwortformatierung und ein extern gespeichertes Laufprotokoll. Datenbank-, Modell- und Ausgabepfade werden über die vorgesehenen Laufzeitparameter konfiguriert.

## Entwicklung

Die öffentliche Testsuite verwendet synthetische Fixtures, temporäre Datenbanken und Modell-Stubs. Qwen, LM Studio und die operative Datenbank sind dafür nicht erforderlich.

Standardprüfung:

```bash
python -m compileall src
python -m pytest
git diff --check
```

Falls das globale temporäre Windows-Verzeichnis nicht beschreibbar ist, kann Pytest mit einem lokalen temporären Verzeichnis gestartet werden:

```bash
python -m pytest --basetemp=.pytest-public-temp
```

Temporäre Testartefakte dürfen nicht in das Repository übernommen werden. Weitere Regeln stehen in [`docs/testing_strategy.md`](docs/testing_strategy.md) und [`AGENTS.md`](AGENTS.md).

