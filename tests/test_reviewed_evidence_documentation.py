from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADR_10 = ROOT / "docs" / "adr" / "10-Fachlich-geprüfte-Evidenz-priorisieren.md"
ADR_13 = ROOT / "docs" / "adr" / "13-Geprüfte-und-ungeprüfte-Evidenz-verwenden.md"
DATA_MODEL = ROOT / "docs" / "data_model.md"


def test_reviewed_evidence_decision_is_authoritative_and_referenced() -> None:
    adr_10_text = ADR_10.read_text(encoding="utf-8")
    adr_13_text = ADR_13.read_text(encoding="utf-8")
    data_model_text = DATA_MODEL.read_text(encoding="utf-8")

    for statement_type in ("requirement", "planning_measure", "finding", "decision"):
        assert f"`{statement_type}`" in data_model_text
    for status_value in (
        "`binding`",
        "`unknown`",
        "`needs_review`",
        "`confirmed`",
        "`rejected`",
    ):
        assert status_value in data_model_text

    assert "ungültig, wird von ADR 13 überschrieben" in adr_10_text
    assert "Originalpassage" in adr_10_text
    assert "gesonderten fachlichen Bestätigung" in adr_10_text
    assert "außer fachlich verworfenen Kandidaten" in adr_13_text
    assert "`needs_review`" in adr_13_text
    assert "`rejected` ist ausgeschlossen" in data_model_text
    assert "Ungeprüfte Relationen bleiben Modellvorschläge" in adr_13_text
    assert "Dokument, Version, Locator und Prüfstatus" in adr_13_text

    documented_semantics = {
        "docs/architecture.md": ("deterministischer Formatter", "Prüfstatus"),
        "docs/ai_contract.md": ("`rejected`", "automatisch_extrahiert_ungeprüft"),
        "docs/data_model.md": ("`confirmed`", "technisch valide `needs_review`"),
    }
    for relative_path, required_terms in documented_semantics.items():
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert all(term in text for term in required_terms)
