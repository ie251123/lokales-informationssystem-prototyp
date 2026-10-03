from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import tempfile
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from info_system.operation.clean_set_retrieval import (
    EvidenceItem,
    EvidencePackage,
    FacetCoverage,
    FacetRetrievalTrace,
    RetrievalTrace,
    SearchIntent,
)
import info_system.operation.local_llm as local_llm_module
from info_system.operation.local_llm import (
    ANSWER_ADAPTER_SCHEMA_VERSION,
    ANSWER_JSON_SCHEMA,
    ANSWER_PROMPT_VERSION,
    DETERMINISTIC_FACETED_ANSWER_VERSION,
    EVIDENCE_SELECTION_ADAPTER_SCHEMA_VERSION,
    EVIDENCE_SELECTION_JSON_SCHEMA,
    EVIDENCE_SELECTION_PROMPT_VERSION,
    LEGACY_RUN_PROTOCOL_SCHEMA_VERSION,
    LM_STUDIO_ENDPOINT,
    LM_STUDIO_MODEL,
    RUN_PROTOCOL_SCHEMA_VERSION,
    SEARCH_INTENT_ADAPTER_SCHEMA_VERSION,
    SEARCH_INTENT_FACET_CLEANUP_VERSION,
    SEARCH_INTENT_JSON_SCHEMA,
    SEARCH_INTENT_PROMPT_VERSION,
    SUPPORTED_RUN_PROTOCOL_SCHEMA_VERSIONS,
    LocalLlmError,
    LocalQuestionResult,
    LoopbackLmStudioClient,
    run_local_question,
)


_GRAMMAR_INTENSIVE_SCHEMA_KEYWORDS = {
    "$schema",
    "maxLength",
    "minLength",
    "minProperties",
    "uniqueItems",
}

_EVALUATION_IDENTIFIERS = {
    "project_id": "project_synthetic",
    "question_id": "question_synthetic_001",
    "model_artifact": "Qwen3-14B-Q6_K_L.gguf",
    "model_quantization": "Q6_K_L",
}
_EVALUATION_CONTEXT_KEYS = {
    "project_id",
    "question_id",
    "git_commit",
    "tracked_worktree_clean",
    "database_sha256",
    "database_user_version",
    "model_id",
    "model_artifact",
    "model_quantization",
    "prompt_versions",
    "adapter_schema_versions",
    "formatter_version",
    "protocol_schema_version",
}


def _evaluation_database(path: Path, *, user_version: int = 4) -> Path:
    with sqlite3.connect(path) as connection:
        connection.execute(f"PRAGMA user_version = {user_version}")
    return path


def _run_evaluation_question(**kwargs: object) -> LocalQuestionResult:
    db_path = Path(kwargs["db_path"])
    if not db_path.exists():
        _evaluation_database(db_path)
    with patch.object(
        local_llm_module,
        "_git_freeze_context",
        return_value=("d" * 40, True),
    ):
        return run_local_question(**kwargs, **_EVALUATION_IDENTIFIERS)


def _assert_complete_evaluation_context(value: object) -> None:
    assert isinstance(value, dict)
    assert set(value) == _EVALUATION_CONTEXT_KEYS
    assert value["project_id"] == _EVALUATION_IDENTIFIERS["project_id"]
    assert value["question_id"] == _EVALUATION_IDENTIFIERS["question_id"]
    assert value["model_artifact"] == _EVALUATION_IDENTIFIERS["model_artifact"]
    assert value["model_quantization"] == _EVALUATION_IDENTIFIERS[
        "model_quantization"
    ]
    assert value["model_id"] == LM_STUDIO_MODEL
    assert value["protocol_schema_version"] == RUN_PROTOCOL_SCHEMA_VERSION


def _assert_grammar_compatible_schema(value: object) -> None:
    if isinstance(value, dict):
        assert not (_GRAMMAR_INTENSIVE_SCHEMA_KEYWORDS & set(value))
        if value.get("type") == "array":
            assert "maxItems" in value
        for child in value.values():
            _assert_grammar_compatible_schema(child)
    elif isinstance(value, list):
        for child in value:
            _assert_grammar_compatible_schema(child)


class FakeClient:
    def __init__(
        self,
        content: str,
        *,
        answer_content: str | None = None,
        response_model: str = LM_STUDIO_MODEL,
        usages: tuple[dict[str, int] | None, ...] = (),
    ) -> None:
        self.contents = [content]
        if answer_content is not None:
            self.contents.append(answer_content)
        self.response_model = response_model
        self.usages = usages
        self.payloads: list[dict[str, object]] = []

    def complete(self, payload: dict[str, object]) -> str:
        self.payloads.append(payload)
        index = len(self.payloads) - 1
        if index >= len(self.contents):
            raise AssertionError("unexpected additional model call")
        response: dict[str, object] = {
            "model": self.response_model,
            "choices": [{"message": {"content": self.contents[index]}}],
        }
        if index < len(self.usages) and self.usages[index] is not None:
            response["usage"] = self.usages[index]
        return json.dumps(response)


def _valid_intent_payload() -> dict[str, object]:
    return {
        "terms": ["Prüfzyklus Alpha", "Prüfklappe Alpha", "Kennung K-7"],
        "retrieval_path": "fts5",
        "max_results": 10,
    }


def _valid_faceted_intent_payload() -> dict[str, object]:
    return {
        "schema_version": "2.0",
        "query_text": "Welche Anforderungen gelten für Türen?",
        "terms": ["Türen"],
        "facets": [
            {
                "facet_type": "requirement",
                "terms": ["Türen"],
                "statement_types": ["requirement"],
                "required": True,
            }
        ],
        "retrieval_path": "fts5",
        "max_results": 10,
    }


def _missing_object_property_intent_payload() -> dict[str, object]:
    question = "Welche Anforderungen und welchen Erfüllungsstatus gibt es für Türen?"
    return {
        "schema_version": "2.0",
        "query_text": question,
        "facets": [
            {
                "facet_type": "requirement",
                "terms": ["Anforderungen", "Türen"],
                "required": False,
            },
            {
                "facet_type": "object_property",
                "terms": ["Erfüllungsstatus"],
                "required": True,
            },
        ],
        "retrieval_path": "fts5",
        "max_results": 10,
    }


def _selection_payload(
    *selections: tuple[EvidenceItem, tuple[int, ...]],
    facet_count: int | None = None,
    coverage_statuses: tuple[str, ...] | None = None,
    uncovered_terms: tuple[tuple[str, ...], ...] | None = None,
) -> dict[str, object]:
    inferred_facet_count = max(
        (
            facet_index + 1
            for _, facet_indices in selections
            for facet_index in facet_indices
        ),
        default=0,
    )
    count = inferred_facet_count if facet_count is None else facet_count
    assigned_indices = {
        facet_index
        for _, facet_indices in selections
        for facet_index in facet_indices
    }
    statuses = coverage_statuses or tuple(
        "covered" if index in assigned_indices else "missing"
        for index in range(count)
    )
    uncovered = uncovered_terms or tuple(() for _ in range(count))
    return {
        "schema_version": "2.0",
        "selected_evidence": [
            {
                "evidence_id": (
                    f"{item.candidate_id}#passage={item.passage_order}"
                ),
                "facet_indices": list(facet_indices),
            }
            for item, facet_indices in selections
        ],
        "facet_coverage": [
            {
                "facet_index": index,
                "coverage_status": statuses[index],
                "uncovered_terms": list(uncovered[index]),
            }
            for index in range(count)
        ],
    }


def _package(
    *items: EvidenceItem,
    status: str = "sufficient",
    missing_required_facets: tuple[str, ...] = (),
) -> EvidencePackage:
    facet_runs = tuple(
        FacetRetrievalTrace(
            facet_type=facet_type,
            required=True,
            terms=(facet_type,),
            statement_types=(),
            executed_search_intent={
                "schema_version": "2.0",
                "terms": [facet_type],
                "retrieval_path": "fts5",
                "max_results": 10,
            },
            coverage_status="missing",
        )
        for facet_type in missing_required_facets
    )
    return EvidencePackage(
        schema_version="evidence_package_v2",
        status=status,
        insufficient_evidence=not items,
        conflicting_evidence=status == "conflicting_evidence",
        conflict_relation_ids=(
            ["relation:conflict"] if status == "conflicting_evidence" else []
        ),
        evidence=list(items),
        relations=[],
        trace=RetrievalTrace(
            search_intent={},
            iterations=(),
            fts5_projection=None,
            facet_runs=facet_runs,
        ),
        facet_coverage=tuple(
            FacetCoverage(
                facet_type=facet_type,
                required=True,
                status="missing",
                hit_count=0,
            )
            for facet_type in missing_required_facets
        ),
        missing_required_facets=list(missing_required_facets),
    )


def _evidence(
    *,
    candidate_id: str = "batch_001:candidate_syn_001",
    document_id: str = "document_rule_alpha",
    document_label: str = "Erfundene Regelquelle Alpha",
    version: str = "synthetic_rule_alpha_profile_v1",
    locator: str = "page=1;section=rule-alpha",
    statement: str = (
        "Türen im vollständig erfundenen Mustergebäude Alpha müssen "
        "selbstschließend sein."
    ),
    passage: str = (
        "Die synthetischen Türen von Mustergebäude Alpha erhalten "
        "selbsttätige Schließer."
    ),
    statement_type: str = "requirement",
    review_status: str = "needs_review",
    review_label: str = "automatisch_extrahiert_ungeprüft",
    value_origin: str = "raw_candidate",
    rank: int = 1,
) -> EvidenceItem:
    return EvidenceItem(
        candidate_id=candidate_id,
        statement_id=f"stmt:{candidate_id}",
        passage_order=1,
        exact_evidence_text=passage,
        document_id=document_id,
        document_label=document_label,
        filename=f"{document_id}.pdf",
        document_type="pdf",
        document_version_id=f"{document_id}:sha256:abc",
        document_version_label=version,
        document_node_id=f"node:{candidate_id}",
        locator=locator,
        page_start=1,
        page_end=1,
        char_start_in_node=0,
        char_end_in_node=len(passage),
        source_role="primary",
        statement_type=statement_type,
        binding_status="binding" if statement_type == "requirement" else "unknown",
        normalized_statement=statement,
        review_status=review_status,
        review_status_label=review_label,
        value_origin=value_origin,
        retrieval_method="fts5",
        rank=rank,
        score=-1.0,
        active_relation_ids=(),
    )


def test_valid_question_uses_fixed_local_qwen_and_validated_intent(tmp_path) -> None:
    received: list[SearchIntent] = []
    evidence = _evidence()
    expected_package = _package(evidence)
    client = FakeClient(
        json.dumps(_valid_faceted_intent_payload(), ensure_ascii=False),
        answer_content=json.dumps(
            _selection_payload((evidence, (0,))), ensure_ascii=False
        ),
    )

    def retrieve(db_path: str | Path, intent: SearchIntent) -> EvidencePackage:
        assert Path(db_path) == tmp_path / "operational.sqlite"
        received.append(intent)
        return expected_package

    result = run_local_question(
        db_path=tmp_path / "operational.sqlite",
        question="Welche Anforderungen gelten für Türen?",
        client=client,
        retriever=retrieve,
    )

    assert result.endpoint == LM_STUDIO_ENDPOINT == (
        "http://127.0.0.1:1234/v1/chat/completions"
    )
    assert result.model == LM_STUDIO_MODEL == "qwen3-14b"
    assert received == [result.search_intent]
    assert isinstance(received[0], SearchIntent)
    assert received[0].terms == ("Türen",)
    request = client.payloads[0]
    assert request["model"] == "qwen3-14b"
    assert request["temperature"] == 0
    assert request["response_format"]["type"] == "json_schema"
    assert request["response_format"]["json_schema"]["strict"] is True
    assert "SQL" in request["messages"][0]["content"]
    assert len(client.payloads) == 2
    assert result.search_intent_prompt_version == SEARCH_INTENT_PROMPT_VERSION
    assert result.answer_prompt_version == ANSWER_PROMPT_VERSION
    assert result.raw_search_intent_model_output is not None
    assert result.raw_answer_model_output is not None


def test_versioned_schema_and_prompt_require_controlled_facets(
    tmp_path,
) -> None:
    client = FakeClient(
        json.dumps(_valid_faceted_intent_payload(), ensure_ascii=False)
    )
    received: list[SearchIntent] = []

    def retrieve(db_path: str | Path, intent: SearchIntent) -> EvidencePackage:
        received.append(intent)
        return _package()

    result = run_local_question(
        db_path=tmp_path / "operational.sqlite",
        question="Welche Anforderungen gelten für Türen?",
        client=client,
        retriever=retrieve,
    )

    assert result.search_intent.schema_version == "2.0"
    assert result.search_intent.facets[0].facet_type == "requirement"
    assert received == [result.search_intent]
    properties = SEARCH_INTENT_JSON_SCHEMA["properties"]
    assert set(properties) == {
        "schema_version",
        "query_text",
        "facets",
        "retrieval_path",
        "max_results",
    }
    assert properties["schema_version"]["enum"] == ["2.0"]
    assert properties["facets"]["maxItems"] == 3
    facet_schema = properties["facets"]["items"]
    assert set(facet_schema["properties"]) == {
        "facet_type",
        "terms",
        "required",
        "statement_types",
    }
    assert set(facet_schema["required"]) == {
        "facet_type",
        "terms",
        "required",
    }
    request = client.payloads[0]
    assert request["response_format"]["json_schema"]["name"] == (
        SEARCH_INTENT_ADAPTER_SCHEMA_VERSION
    )
    prompt = request["messages"][0]["content"]
    assert SEARCH_INTENT_PROMPT_VERSION == "search_intent_v4_20260816"
    assert "Du übersetzt genau eine deutschsprachige Nutzerfrage" in prompt
    assert 'schema_version exakt auf "2.0"' in prompt
    assert "höchstens drei Facetten" in prompt
    assert "höchstens vier" in prompt
    assert "wörtlich in der Nutzerfrage" in prompt
    assert "nichtgenerischen fachlichen Anker" in prompt
    assert "required" in prompt
    assert "semantischen Rollen" in prompt
    assert "gemeinsame wörtliche Eigenschaft" in prompt
    assert "Objektwerte" in prompt
    assert "Räume oder Bereiche" in prompt
    assert "Grenzen, Nachweise oder offene Maßnahmen" in prompt
    assert "Brandschutzwand" not in prompt


def test_model_schemas_are_bounded_and_grammar_compatible() -> None:
    _assert_grammar_compatible_schema(SEARCH_INTENT_JSON_SCHEMA)
    _assert_grammar_compatible_schema(ANSWER_JSON_SCHEMA)

    assert SEARCH_INTENT_JSON_SCHEMA["required"] == [
        "schema_version",
        "query_text",
        "facets",
        "retrieval_path",
        "max_results",
    ]
    assert EVIDENCE_SELECTION_JSON_SCHEMA["properties"]["selected_evidence"][
        "maxItems"
    ] == 10
    assert EVIDENCE_SELECTION_JSON_SCHEMA["properties"]["schema_version"][
        "enum"
    ] == ["2.0"]
    assert set(EVIDENCE_SELECTION_JSON_SCHEMA["required"]) == {
        "schema_version",
        "selected_evidence",
        "facet_coverage",
    }


def test_composite_question_keeps_three_semantic_role_facets(tmp_path) -> None:
    question = (
        "Welche Eigenschaft gilt für Bauteil A und Raum Nord, und welche "
        "Nachweise sind offen?"
    )
    payload = {
        "schema_version": "2.0",
        "query_text": question,
        "facets": [
            {
                "facet_type": "object_property",
                "terms": ["Eigenschaft", "Bauteil A"],
                "required": True,
            },
            {
                "facet_type": "spatial_reference",
                "terms": ["Eigenschaft", "Raum Nord"],
                "required": True,
            },
            {
                "facet_type": "requirement",
                "terms": ["Nachweise", "offen"],
                "required": True,
            },
        ],
        "retrieval_path": "fts5",
        "max_results": 10,
    }

    result = run_local_question(
        db_path=tmp_path / "operational.sqlite",
        question=question,
        client=FakeClient(json.dumps(payload, ensure_ascii=False)),
        retriever=lambda *_: _package(),
    )

    assert [facet.facet_type for facet in result.search_intent.facets] == [
        "object_property",
        "spatial_reference",
        "requirement",
    ]
    assert result.search_intent.facets[0].terms[0] == "Eigenschaft"
    assert result.search_intent.facets[1].terms[0] == "Eigenschaft"


def test_version_2_model_shape_is_adapted_before_full_validation(tmp_path) -> None:
    question = "Welche Anforderungen gelten für Türen?"
    payload = {
        "schema_version": "2.0",
        "query_text": question,
        "facets": [
            {
                "facet_type": "requirement",
                "terms": ["Anforderungen", "Türen"],
                "required": True,
            }
        ],
        "retrieval_path": "fts5",
        "max_results": 10,
    }

    result = run_local_question(
        db_path=tmp_path / "operational.sqlite",
        question=question,
        client=FakeClient(json.dumps(payload, ensure_ascii=False)),
        retriever=lambda *_: _package(),
    )

    assert result.search_intent.schema_version == "2.0"
    assert result.search_intent.query_text == question
    assert result.search_intent.terms == ("Anforderungen", "Türen")


def test_unanchored_facet_terms_are_removed_and_logged_end_to_end(
    tmp_path,
) -> None:
    question = "Wie werden die Anforderungen für Türen beurteilt und begründet?"
    payload = {
        "schema_version": "2.0",
        "query_text": question,
        "facets": [
            {
                "facet_type": "requirement",
                "terms": [
                    "Anforderungen",
                    "Türen",
                    "Beurteilung",
                    "Begründung",
                ],
                "statement_types": ["requirement"],
                "required": True,
            }
        ],
        "retrieval_path": "fts5",
        "max_results": 10,
    }
    raw_intent = json.dumps(payload, ensure_ascii=False)
    evidence = _evidence()
    received: list[SearchIntent] = []

    def retrieve(db_path: str | Path, intent: SearchIntent) -> EvidencePackage:
        received.append(intent)
        return _package(evidence)

    with tempfile.TemporaryDirectory(prefix="local-e2e-intent-cleanup-") as external:
        result = _run_evaluation_question(
            db_path=tmp_path / "operational.sqlite",
            question=question,
            client=FakeClient(
                raw_intent,
                answer_content=json.dumps(
                    _selection_payload((evidence, (0,))),
                    ensure_ascii=False,
                ),
            ),
            retriever=retrieve,
            output_dir=external,
        )

        protocol = json.loads(
            Path(result.run_protocol_path).read_text(encoding="utf-8")
        )

    assert received == [result.search_intent]
    assert result.raw_search_intent_model_output == raw_intent
    assert result.search_intent.facets[0].terms == ("Anforderungen", "Türen")
    assert result.search_intent.terms == ("Anforderungen", "Türen")
    assert protocol["raw_search_intent_model_output"] == raw_intent
    assert protocol["validated_search_intent"] == result.search_intent.to_dict()
    assert protocol["search_intent_facet_cleanup"] == {
        "version": SEARCH_INTENT_FACET_CLEANUP_VERSION,
        "discarded_terms": [
            {
                "facet_type": "requirement",
                "original_term": "Beurteilung",
                "reason": "not_anchored_in_query",
            },
            {
                "facet_type": "requirement",
                "original_term": "Begründung",
                "reason": "not_anchored_in_query",
            },
        ],
        "removed_optional_facets": [],
    }
    assert "## Quellen" in result.answer_text


def test_fully_anchored_faceted_intent_is_not_cleaned(tmp_path) -> None:
    payload = _valid_faceted_intent_payload()
    raw_intent = json.dumps(payload, ensure_ascii=False)

    with tempfile.TemporaryDirectory(prefix="local-e2e-intent-unchanged-") as external:
        result = _run_evaluation_question(
            db_path=tmp_path / "operational.sqlite",
            question=str(payload["query_text"]),
            client=FakeClient(raw_intent),
            retriever=lambda *_: _package(),
            output_dir=external,
        )
        protocol = json.loads(
            Path(result.run_protocol_path).read_text(encoding="utf-8")
        )

    assert result.raw_search_intent_model_output == raw_intent
    assert result.search_intent.facets[0].terms == ("Türen",)
    assert result.search_intent.terms == ("Türen",)
    assert protocol["search_intent_facet_cleanup"] == {
        "version": SEARCH_INTENT_FACET_CLEANUP_VERSION,
        "discarded_terms": [],
        "removed_optional_facets": [],
    }


@pytest.mark.parametrize(
    ("question", "term"),
    [
        ("Welche Anforderungen gelten für Türen?", "TÜREN"),
        ("Welche Anforderungen gelten für Türen?", "Ｔüren"),
        (
            "Welche Anforderungen gelten für Türen und Anforderungen?",
            "Türen   und Anforderungen",
        ),
        (
            "Welche Anforderungen gelten für Türen, Anforderungen?",
            "Türen / Anforderungen",
        ),
    ],
)
def test_facet_cleanup_uses_existing_safe_question_normalization(
    tmp_path,
    question: str,
    term: str,
) -> None:
    payload = {
        "schema_version": "2.0",
        "query_text": question,
        "facets": [
            {
                "facet_type": "requirement",
                "terms": [term],
                "required": True,
            }
        ],
        "retrieval_path": "fts5",
        "max_results": 10,
    }

    result = run_local_question(
        db_path=tmp_path / "operational.sqlite",
        question=question,
        client=FakeClient(json.dumps(payload, ensure_ascii=False)),
        retriever=lambda *_: _package(),
    )

    assert result.search_intent.facets[0].terms == (term,)
    assert result.search_intent.terms == (term,)


def test_required_facet_empty_after_cleanup_remains_invalid_and_is_logged(
    tmp_path,
) -> None:
    question = "Wie werden Türen beurteilt?"
    payload = {
        "schema_version": "2.0",
        "query_text": question,
        "facets": [
            {
                "facet_type": "requirement",
                "terms": ["Beurteilung"],
                "required": True,
            }
        ],
        "retrieval_path": "fts5",
        "max_results": 10,
    }
    called = False

    def retrieve(db_path: str | Path, intent: SearchIntent) -> EvidencePackage:
        nonlocal called
        called = True
        return _package()

    with tempfile.TemporaryDirectory(prefix="local-e2e-intent-invalid-") as external:
        with pytest.raises(LocalLlmError, match="invalid SearchIntent"):
            _run_evaluation_question(
                db_path=tmp_path / "operational.sqlite",
                question=question,
                client=FakeClient(json.dumps(payload, ensure_ascii=False)),
                retriever=retrieve,
                output_dir=external,
            )
        protocol_path = next(Path(external).glob("*.json"))
        protocol = json.loads(protocol_path.read_text(encoding="utf-8"))

    assert called is False
    assert protocol["validated_search_intent"] is None
    assert protocol["search_intent_facet_cleanup"]["discarded_terms"] == [
        {
            "facet_type": "requirement",
            "original_term": "Beurteilung",
            "reason": "not_anchored_in_query",
        }
    ]
    assert protocol["search_intent_facet_cleanup"][
        "removed_optional_facets"
    ] == []


def test_empty_optional_facet_is_removed_with_valid_required_facet(
    tmp_path,
) -> None:
    question = "Welche Anforderungen gelten für Türen und wie wird dies beurteilt?"
    payload = {
        "schema_version": "2.0",
        "query_text": question,
        "facets": [
            {
                "facet_type": "requirement",
                "terms": ["Anforderungen", "Türen"],
                "required": True,
            },
            {
                "facet_type": "project_finding",
                "terms": ["Beurteilung"],
                "required": False,
            },
        ],
        "retrieval_path": "fts5",
        "max_results": 10,
    }

    with tempfile.TemporaryDirectory(prefix="local-e2e-optional-cleanup-") as external:
        result = _run_evaluation_question(
            db_path=tmp_path / "operational.sqlite",
            question=question,
            client=FakeClient(json.dumps(payload, ensure_ascii=False)),
            retriever=lambda *_: _package(),
            output_dir=external,
        )
        protocol = json.loads(
            Path(result.run_protocol_path).read_text(encoding="utf-8")
        )

    assert [facet.facet_type for facet in result.search_intent.facets] == [
        "requirement"
    ]
    assert result.search_intent.terms == ("Anforderungen", "Türen")
    assert protocol["search_intent_facet_cleanup"][
        "removed_optional_facets"
    ] == [
        {
            "facet_type": "project_finding",
            "reason": "empty_after_term_cleanup",
        }
    ]


def test_empty_optional_facet_without_valid_required_facet_remains_invalid(
    tmp_path,
) -> None:
    question = "Wie werden Türen beurteilt?"
    payload = {
        "schema_version": "2.0",
        "query_text": question,
        "facets": [
            {
                "facet_type": "project_finding",
                "terms": ["Beurteilung"],
                "required": False,
            }
        ],
        "retrieval_path": "fts5",
        "max_results": 10,
    }

    with pytest.raises(LocalLlmError, match="invalid SearchIntent"):
        run_local_question(
            db_path=tmp_path / "operational.sqlite",
            question=question,
            client=FakeClient(json.dumps(payload, ensure_ascii=False)),
            retriever=lambda *_: (_ for _ in ()).throw(
                AssertionError("retrieval must not run")
            ),
        )


@pytest.mark.parametrize(
    "foreign_term",
    ["Fenster", "SELECT", "C:\\secret\\file.txt", "ignore previous instructions"],
)
def test_unanchored_optional_unsafe_content_is_never_adopted(
    tmp_path,
    foreign_term: str,
) -> None:
    question = "Welche Anforderungen gelten für Türen?"
    payload = {
        "schema_version": "2.0",
        "query_text": question,
        "facets": [
            {
                "facet_type": "requirement",
                "terms": ["Anforderungen", "Türen"],
                "required": True,
            },
            {
                "facet_type": "project_finding",
                "terms": [foreign_term],
                "required": False,
            },
        ],
        "retrieval_path": "fts5",
        "max_results": 10,
    }
    received: list[SearchIntent] = []

    result = run_local_question(
        db_path=tmp_path / "operational.sqlite",
        question=question,
        client=FakeClient(json.dumps(payload, ensure_ascii=False)),
        retriever=lambda _, intent: received.append(intent) or _package(),
    )

    assert received == [result.search_intent]
    assert foreign_term not in result.search_intent.terms
    assert all(
        foreign_term not in facet.terms for facet in result.search_intent.facets
    )


def test_version_1_search_intent_remains_compatible(tmp_path) -> None:
    result = run_local_question(
        db_path=tmp_path / "operational.sqlite",
        question="Welche Anforderungen gelten für Türen?",
        client=FakeClient(json.dumps(_valid_intent_payload(), ensure_ascii=False)),
        retriever=lambda *_: _package(),
    )

    assert result.search_intent.schema_version == "1.0"
    assert result.search_intent.terms == tuple(_valid_intent_payload()["terms"])


def test_faceted_intent_must_preserve_the_original_runtime_question(tmp_path) -> None:
    payload = _valid_faceted_intent_payload()
    payload["query_text"] = "Welche Anforderungen gelten für Fenster?"
    payload["facets"] = [
        {
            "facet_type": "requirement",
            "terms": ["Fenster"],
            "required": True,
        }
    ]
    called = False

    def retrieve(db_path: str | Path, intent: SearchIntent) -> EvidencePackage:
        nonlocal called
        called = True
        return _package()

    with pytest.raises(LocalLlmError, match="original question"):
        run_local_question(
            db_path=tmp_path / "operational.sqlite",
            question="Welche Anforderungen gelten für Türen?",
            client=FakeClient(json.dumps(payload, ensure_ascii=False)),
            retriever=retrieve,
        )

    assert called is False


def test_simple_question_accepts_version_2_without_artificial_facets(tmp_path) -> None:
    question = "Welche Vorgabe gilt für Rauchableitung?"
    payload = {
        "schema_version": "2.0",
        "query_text": question,
        "facets": [],
        "retrieval_path": "fts5",
        "max_results": 10,
    }

    result = run_local_question(
        db_path=tmp_path / "operational.sqlite",
        question=question,
        client=FakeClient(json.dumps(payload, ensure_ascii=False)),
        retriever=lambda *_: _package(),
    )

    assert result.search_intent.schema_version == "2.0"
    assert result.search_intent.query_text == question
    assert result.search_intent.facets == ()
    assert result.search_intent.terms == (question,)


def test_composite_question_accepts_at_most_three_question_anchored_facets(
    tmp_path,
) -> None:
    question = (
        "Welche Anforderungen, Ausführungen und Feststellungen gelten für Türen?"
    )
    payload = {
        "schema_version": "2.0",
        "query_text": question,
        "terms": ["Türen"],
        "facets": [
            {
                "facet_type": "requirement",
                "terms": ["Anforderungen", "Türen"],
                "statement_types": ["requirement"],
                "required": True,
            },
            {
                "facet_type": "planning_measure",
                "terms": ["Ausführungen", "Türen"],
                "statement_types": ["planning_measure"],
                "required": True,
            },
            {
                "facet_type": "project_finding",
                "terms": ["Feststellungen", "Türen"],
                "statement_types": ["finding"],
                "required": False,
            },
        ],
        "retrieval_path": "fts5",
        "max_results": 10,
    }

    result = run_local_question(
        db_path=tmp_path / "operational.sqlite",
        question=question,
        client=FakeClient(json.dumps(payload, ensure_ascii=False)),
        retriever=lambda *_: _package(),
    )

    assert len(result.search_intent.facets) == 3
    assert [facet.required for facet in result.search_intent.facets] == [
        True,
        True,
        False,
    ]


@pytest.mark.parametrize(
    "foreign_term",
    ["Fenster", "SELECT", "find_evidence", "C:\\secret\\file.txt"],
)
def test_version_2_rejects_foreign_sql_tool_command_and_path_facet_terms(
    tmp_path, foreign_term: str
) -> None:
    question = "Welche Anforderungen gelten für Türen?"
    payload = _valid_faceted_intent_payload()
    payload["facets"] = [
        {
            "facet_type": "requirement",
            "terms": [foreign_term],
            "statement_types": ["requirement"],
            "required": True,
        }
    ]

    with pytest.raises(LocalLlmError):
        run_local_question(
            db_path=tmp_path / "operational.sqlite",
            question=question,
            client=FakeClient(json.dumps(payload, ensure_ascii=False)),
            retriever=lambda *_: _package(),
        )


@pytest.mark.parametrize(
    ("question", "facets"),
    [
        (
            "Welche Anforderungen gelten?",
            [
                {
                    "facet_type": "requirement",
                    "terms": ["Anforderungen"],
                    "required": True,
                }
            ],
        ),
        (
            f"Welche Vorgabe gilt für {'T' * 65}?",
            [
                {
                    "facet_type": "requirement",
                    "terms": ["T" * 65],
                    "required": True,
                }
            ],
        ),
        (
            "Welche Anforderungen gelten für Türen?",
            [
                {
                    "facet_type": "requirement",
                    "terms": ["Türen"],
                    "required": True,
                },
                {
                    "facet_type": "object_property",
                    "terms": ["Türen"],
                    "required": False,
                },
            ],
        ),
    ],
)
def test_slim_model_shape_still_uses_strict_facet_postvalidation(
    tmp_path, question: str, facets: list[dict[str, object]]
) -> None:
    payload = {
        "schema_version": "2.0",
        "query_text": question,
        "facets": facets,
        "retrieval_path": "fts5",
        "max_results": 10,
    }

    with pytest.raises(LocalLlmError, match="invalid SearchIntent"):
        run_local_question(
            db_path=tmp_path / "operational.sqlite",
            question=question,
            client=FakeClient(json.dumps(payload, ensure_ascii=False)),
            retriever=lambda *_: (_ for _ in ()).throw(
                AssertionError("retrieval must not run")
            ),
        )


def test_runtime_prompt_is_case_neutral_for_a_differently_worded_question() -> None:
    client = FakeClient(
        json.dumps(
            {
                "terms": ["Rauchableitung", "oberstes Geschoss"],
                "retrieval_path": "fts5",
                "max_results": 10,
            },
            ensure_ascii=False,
        )
    )
    received: list[SearchIntent] = []

    def retrieve(db_path: str | Path, intent: SearchIntent) -> EvidencePackage:
        received.append(intent)
        return _package()

    result = run_local_question(
        db_path="unused.sqlite",
        question="Welche Vorgaben betreffen die Rauchableitung im obersten Geschoss?",
        client=client,
        retriever=retrieve,
    )

    messages = client.payloads[0]["messages"]
    sent_prompt = "\n".join(message["content"] for message in messages)
    forbidden = (
        "vertrauliche Musterregel 99",
        "Bauteil SECRET-99",
        "Kennung INTERN-7",
        "Welche Eigenschaften hat das nicht gefragte Bauteil SECRET-99?",
        "document_secret_delta",
        "page=99;section=secret",
        "row=SECRET_99",
    )

    assert all(value not in sent_prompt for value in forbidden)
    assert received == [result.search_intent]
    assert result.search_intent.terms == ("Rauchableitung", "oberstes Geschoss")


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"terms":["Tür"],"retrieval_path":"fts5","max_results":5,"sql":"SELECT *"}',
        '{"terms":["Tür"],"retrieval_path":"free_sql","max_results":5}',
        '```json\n{"terms":["Tür"],"retrieval_path":"fts5","max_results":5}\n```',
    ],
)
def test_invalid_model_output_never_triggers_retrieval_or_sql(
    tmp_path, content: str
) -> None:
    called = False

    def retrieve(db_path: str | Path, intent: SearchIntent) -> EvidencePackage:
        nonlocal called
        called = True
        raise AssertionError("retrieval must not run")

    with pytest.raises(LocalLlmError):
        run_local_question(
            db_path=tmp_path / "operational.sqlite",
            question="Tür?",
            client=FakeClient(content),
            retriever=retrieve,
        )

    assert called is False


def test_mismatching_response_model_is_rejected_before_retrieval(tmp_path) -> None:
    called = False

    def retrieve(db_path: str | Path, intent: SearchIntent) -> EvidencePackage:
        nonlocal called
        called = True
        raise AssertionError("retrieval must not run")

    with pytest.raises(LocalLlmError, match="model"):
        run_local_question(
            db_path=tmp_path / "operational.sqlite",
            question="Tür?",
            client=FakeClient(
                json.dumps(_valid_intent_payload()),
                response_model="another-model",
            ),
            retriever=retrieve,
        )

    assert called is False



def test_evidence_selection_request_is_strict_and_contains_only_relevance_data(
    tmp_path,
) -> None:
    question = "Welche Anforderungen und Ausführungen gelten für Türen?"
    intent_payload = {
        "schema_version": "2.0",
        "query_text": question,
        "facets": [
            {
                "facet_type": "requirement",
                "terms": ["Anforderungen", "Türen"],
                "required": True,
            },
            {
                "facet_type": "planning_measure",
                "terms": ["Ausführungen", "Türen"],
                "required": True,
            },
        ],
        "retrieval_path": "fts5",
        "max_results": 10,
    }
    evidence = _evidence(rank=4)
    client = FakeClient(
        json.dumps(intent_payload, ensure_ascii=False),
        answer_content=json.dumps(
            _selection_payload((evidence, (0,)), facet_count=2),
            ensure_ascii=False,
        ),
    )

    result = run_local_question(
        db_path=tmp_path / "operational.sqlite",
        question=question,
        client=client,
        retriever=lambda *_: _package(evidence),
    )

    request = client.payloads[1]
    selection_input = json.loads(request["messages"][1]["content"])
    selection_item = selection_input["evidence_items"][0]
    assert request["response_format"]["json_schema"]["strict"] is True
    assert request["response_format"]["json_schema"]["name"] == (
        EVIDENCE_SELECTION_ADAPTER_SCHEMA_VERSION
    )
    assert EVIDENCE_SELECTION_PROMPT_VERSION == "evidence_selection_v2_20260816"
    assert EVIDENCE_SELECTION_ADAPTER_SCHEMA_VERSION == (
        "evidence_selection_adapter_v2_20260816"
    )
    assert DETERMINISTIC_FACETED_ANSWER_VERSION == (
        "deterministic_faceted_answer_v2_20260816"
    )
    assert request["response_format"]["json_schema"]["schema"] == (
        EVIDENCE_SELECTION_JSON_SCHEMA
    )
    selection_prompt = request["messages"][0]["content"]
    assert "kleinste hinreichende Menge" in selection_prompt
    assert "inhaltlich reichere direkte Evidenz" in selection_prompt
    assert "coverage_status" in selection_prompt
    assert "uncovered_terms" in selection_prompt
    assert set(selection_input) == {"question", "facets", "evidence_items"}
    assert selection_input["facets"] == [
        {
            "index": 0,
            "facet_type": "requirement",
            "terms": ["Anforderungen", "Türen"],
            "required": True,
        },
        {
            "index": 1,
            "facet_type": "planning_measure",
            "terms": ["Ausführungen", "Türen"],
            "required": True,
        },
    ]
    assert set(selection_item) == {
        "evidence_id",
        "normalized_statement",
        "statement_type",
        "review_status",
        "retrieval_rank",
    }
    assert selection_item["retrieval_rank"] == 4
    assert evidence.exact_evidence_text not in request["messages"][1]["content"]
    assert evidence.document_version_id not in request["messages"][1]["content"]
    assert result.answer_prompt_version == EVIDENCE_SELECTION_PROMPT_VERSION


def test_doors_selection_produces_deterministic_faceted_answer_and_protocol(
    tmp_path,
) -> None:
    question = (
        "Welche Anforderungen, Ausführungen und welchen Erfüllungsstatus "
        "gibt es für Türen?"
    )
    intent_payload = {
        "schema_version": "2.0",
        "query_text": question,
        "facets": [
            {
                "facet_type": "requirement",
                "terms": ["Anforderungen", "Türen"],
                "required": True,
            },
            {
                "facet_type": "planning_measure",
                "terms": ["Ausführungen", "Türen"],
                "required": True,
            },
            {
                "facet_type": "object_property",
                "terms": ["Erfüllungsstatus", "Türen"],
                "required": True,
            },
        ],
        "retrieval_path": "fts5",
        "max_results": 10,
    }
    requirement = _evidence(
        candidate_id="door:requirement",
        statement="Türen müssen dicht und selbstschließend sein.",
        rank=2,
    )
    planning = _evidence(
        candidate_id="door:planning",
        statement="Türen werden dicht und selbstschließend ausgeführt.",
        statement_type="planning_measure",
        review_status="confirmed",
        review_label="fachlich_geprüft",
        value_origin="review_overlay",
        rank=1,
    )
    unselected = _evidence(
        candidate_id="door:context",
        statement="Ein thematisch verwandter Kontextbefund.",
        rank=3,
    )
    selection = _selection_payload(
        (requirement, (0,)),
        (planning, (1,)),
        facet_count=3,
    )
    raw_selection = json.dumps(selection, ensure_ascii=False)
    package = _package(
        requirement,
        planning,
        unselected,
        missing_required_facets=("object_property",),
    )

    with tempfile.TemporaryDirectory(prefix="local-e2e-selection-") as external:
        result = _run_evaluation_question(
            db_path=tmp_path / "operational.sqlite",
            question=question,
            client=FakeClient(
                json.dumps(intent_payload, ensure_ascii=False),
                answer_content=raw_selection,
            ),
            retriever=lambda *_: package,
            output_dir=external,
        )
        protocol = json.loads(
            Path(result.run_protocol_path).read_text(encoding="utf-8")
        )

    assert result.answer_text.startswith(
        "Evidenzbasierte Antwort\n\nStatus: Begrenzte Antwort: Mindestens "
        "ein Teil der Frage ist nicht vollständig belegt."
    )
    assert "## Anforderungen" in result.answer_text
    assert "Gefragter Bezug: Anforderungen, Türen" in result.answer_text
    assert "## Planungsmaßnahmen" in result.answer_text
    assert "Gefragter Bezug: Ausführungen, Türen" in result.answer_text
    assert "## Objekteigenschaften" in result.answer_text
    assert "Gefragter Bezug: Erfüllungsstatus, Türen" in result.answer_text
    assert result.answer_text.index("## Anforderungen") < result.answer_text.index(
        "## Planungsmaßnahmen"
    ) < result.answer_text.index("## Objekteigenschaften")
    assert requirement.normalized_statement in result.answer_text
    assert planning.normalized_statement in result.answer_text
    assert unselected.normalized_statement not in result.answer_text
    assert result.answer_text.index(planning.normalized_statement) > (
        result.answer_text.index(requirement.normalized_statement)
    )
    assert "automatisch extrahiert, ungeprüft" in result.answer_text
    assert "fachlich geprüft" in result.answer_text
    assert "Zu diesem Teil der Frage wurden keine geeigneten Belege gefunden." in (
        result.answer_text
    )
    assert result.answer_text.count("### Erfundene Regelquelle Alpha — Version") == 1
    assert result.raw_evidence_selection_model_output == raw_selection
    assert result.raw_answer_model_output == raw_selection
    assert protocol["schema_version"] == "local_e2e_run_v3"
    assert protocol["raw_evidence_selection_model_output"] == raw_selection
    assert protocol["validated_evidence_selection"] == selection
    assert protocol["token_usage"] == {
        "search_intent": None,
        "evidence_selection": None,
    }
    assert len(protocol["evidence_items"]) == 3
    assert any(
        item["exact_evidence_text"] == unselected.exact_evidence_text
        for item in protocol["evidence_items"]
    )
    assert protocol["validated_answer"] == result.answer_text


def test_formatter_v2_is_compact_deterministic_and_keeps_selection(
    tmp_path,
) -> None:
    evidence = _evidence(
        candidate_id="source:first",
        statement="Türen müssen selbstschließend sein.",
        passage="Die Türen erhalten geeignete Schließmittel.",
        locator="page=39;section=5.5",
        review_status="confirmed",
        review_label="fachlich_geprüft",
        value_origin="review_overlay",
    )
    selection = _selection_payload((evidence, (0,)))

    def execute() -> LocalQuestionResult:
        return run_local_question(
            db_path=tmp_path / "operational.sqlite",
            question="Welche Anforderungen gelten für Türen?",
            client=FakeClient(
                json.dumps(_valid_faceted_intent_payload(), ensure_ascii=False),
                answer_content=json.dumps(selection, ensure_ascii=False),
            ),
            retriever=lambda *_: _package(evidence),
        )

    first = execute()
    second = execute()

    assert first.answer_text == second.answer_text
    assert first.validated_evidence_selection == selection
    assert second.validated_evidence_selection == selection
    assert first.answer_text.startswith(
        "Evidenzbasierte Antwort\n\nStatus: Alle erforderlichen Teile der "
        "Frage sind durch ausgewählte Evidenzen belegt."
    )
    assert first.answer_text.count(evidence.normalized_statement) == 1
    assert (
        f"- {evidence.normalized_statement} [1] — fachlich geprüft"
        in first.answer_text
    )
    assert (
        "### Erfundene Regelquelle Alpha — Version "
        "synthetic_rule_alpha_profile_v1"
    ) in (
        first.answer_text
    )
    assert (
        "- [1] S. 39, Abschnitt 5.5; fachlich geprüft; Wertherkunft: "
        "fachlich geprüfte Fassung; Evidence-ID: source:first#passage=1"
        in first.answer_text
    )
    assert "confirmed / fachlich_geprüft" not in first.answer_text
    assert "review_overlay" not in first.answer_text
    assert evidence.exact_evidence_text not in first.answer_text
    assert first.answer_text.endswith(
        "Die Antwort gibt ausschließlich ausgewählte Quellenbefunde wieder "
        "und enthält keine Erfüllungs-, Konformitäts- oder Rechtsbewertung."
    )


def test_one_evidence_can_map_to_multiple_facets_without_statement_duplication(
    tmp_path,
) -> None:
    question = "Welche Anforderungen und Ausführungen gelten für Türen?"
    intent_payload = {
        "schema_version": "2.0",
        "query_text": question,
        "facets": [
            {
                "facet_type": "requirement",
                "terms": ["Anforderungen", "Türen"],
                "required": True,
            },
            {
                "facet_type": "planning_measure",
                "terms": ["Ausführungen", "Türen"],
                "required": True,
            },
        ],
        "retrieval_path": "fts5",
        "max_results": 10,
    }
    evidence = _evidence(statement="Türen müssen selbstschließend sein.")

    result = run_local_question(
        db_path=tmp_path / "operational.sqlite",
        question=question,
        client=FakeClient(
            json.dumps(intent_payload, ensure_ascii=False),
            answer_content=json.dumps(
                _selection_payload((evidence, (0, 1))), ensure_ascii=False
            ),
        ),
        retriever=lambda *_: _package(evidence),
    )

    assert result.answer_text.count(evidence.normalized_statement) == 1
    assert "Siehe auch Aussage [1] im vorherigen Abschnitt." in result.answer_text
    assert result.answer_text.count("Evidence-ID:") == 1


def test_building_class_answer_contains_only_selected_relevant_evidence(
    tmp_path,
) -> None:
    question = "Welche Gebäudeklasse ist für das Gebäude dokumentiert?"
    intent_payload = {
        "schema_version": "2.0",
        "query_text": question,
        "facets": [
            {
                "facet_type": "object_property",
                "terms": ["Gebäudeklasse", "Gebäude"],
                "required": True,
            }
        ],
        "retrieval_path": "fts5",
        "max_results": 10,
    }
    relevant = _evidence(
        candidate_id="building:class",
        statement="Für das Gebäude ist die Gebäudeklasse 4 dokumentiert.",
        statement_type="finding",
        rank=2,
    )
    related = _evidence(
        candidate_id="building:storeys",
        statement="Das Gebäude hat vier oberirdische Geschosse.",
        statement_type="finding",
        rank=1,
    )

    result = run_local_question(
        db_path=tmp_path / "operational.sqlite",
        question=question,
        client=FakeClient(
            json.dumps(intent_payload, ensure_ascii=False),
            answer_content=json.dumps(
                _selection_payload((relevant, (0,))), ensure_ascii=False
            ),
        ),
        retriever=lambda *_: _package(related, relevant),
    )

    assert relevant.normalized_statement in result.answer_text
    assert related.normalized_statement not in result.answer_text
    assert "## Objekteigenschaften" in result.answer_text
    assert "Gefragter Bezug: Gebäudeklasse, Gebäude" in result.answer_text


def test_deterministic_answer_orders_selected_items_by_retrieval_rank(
    tmp_path,
) -> None:
    first = _evidence(
        candidate_id="rank:first",
        statement="Erster Quellenbefund nach Retrievalrang.",
        rank=1,
    )
    second = _evidence(
        candidate_id="rank:second",
        statement="Zweiter Quellenbefund nach Retrievalrang.",
        rank=2,
    )
    selection = _selection_payload((second, (0,)), (first, (0,)))

    result = run_local_question(
        db_path=tmp_path / "operational.sqlite",
        question="Welche Anforderungen gelten für Türen?",
        client=FakeClient(
            json.dumps(_valid_faceted_intent_payload(), ensure_ascii=False),
            answer_content=json.dumps(selection, ensure_ascii=False),
        ),
        retriever=lambda *_: _package(second, first),
    )

    assert result.answer_text.index(first.normalized_statement) < (
        result.answer_text.index(second.normalized_statement)
    )
    assert f"{first.normalized_statement} [1]" in result.answer_text
    assert f"{second.normalized_statement} [2]" in result.answer_text


def test_free_model_answer_fields_are_rejected(tmp_path) -> None:
    evidence = _evidence()
    invalid = _selection_payload((evidence, (0,)))
    invalid["answer_text"] = "Diese freie Modellformulierung darf nicht erscheinen."

    with pytest.raises(LocalLlmError, match="controlled contract"):
        run_local_question(
            db_path=tmp_path / "operational.sqlite",
            question="Welche Anforderungen gelten für Türen?",
            client=FakeClient(
                json.dumps(_valid_faceted_intent_payload(), ensure_ascii=False),
                answer_content=json.dumps(invalid, ensure_ascii=False),
            ),
            retriever=lambda *_: _package(evidence),
        )


def test_selected_prohibited_assessment_remains_rejected(tmp_path) -> None:
    evidence = _evidence(statement="Die Anforderungen sind erfüllt.")

    with pytest.raises(LocalLlmError, match="prohibited assessment"):
        run_local_question(
            db_path=tmp_path / "operational.sqlite",
            question="Welche Anforderungen gelten für Türen?",
            client=FakeClient(
                json.dumps(_valid_faceted_intent_payload(), ensure_ascii=False),
                answer_content=json.dumps(
                    _selection_payload((evidence, (0,))), ensure_ascii=False
                ),
            ),
            retriever=lambda *_: _package(evidence),
        )


@pytest.mark.parametrize(
    "invalid_selection",
    [
        {
            "schema_version": "2.0",
            "selected_evidence": [
                {"evidence_id": "unknown#passage=1", "facet_indices": [0]}
            ],
            "facet_coverage": [{"facet_index": 0, "coverage_status": "covered", "uncovered_terms": []}],
        },
        {
            "schema_version": "2.0",
            "selected_evidence": [
                {
                    "evidence_id": "{evidence_id}",
                    "facet_indices": [0],
                },
                {
                    "evidence_id": "{evidence_id}",
                    "facet_indices": [0],
                },
            ],
            "facet_coverage": [{"facet_index": 0, "coverage_status": "covered", "uncovered_terms": []}],
        },
        {
            "schema_version": "2.0",
            "selected_evidence": [
                {"evidence_id": "{evidence_id}", "facet_indices": [1]}
            ],
            "facet_coverage": [{"facet_index": 0, "coverage_status": "covered", "uncovered_terms": []}],
        },
        {
            "schema_version": "2.0",
            "selected_evidence": [
                {"evidence_id": "{evidence_id}", "facet_indices": [0, 0]}
            ],
            "facet_coverage": [{"facet_index": 0, "coverage_status": "covered", "uncovered_terms": []}],
        },
    ],
)
def test_invalid_evidence_selection_is_rejected(
    tmp_path,
    invalid_selection: dict[str, object],
) -> None:
    evidence = _evidence()
    evidence_id = f"{evidence.candidate_id}#passage={evidence.passage_order}"
    encoded = json.dumps(invalid_selection).replace("{evidence_id}", evidence_id)

    with pytest.raises(LocalLlmError):
        run_local_question(
            db_path=tmp_path / "operational.sqlite",
            question="Welche Anforderungen gelten für Türen?",
            client=FakeClient(
                json.dumps(_valid_faceted_intent_payload(), ensure_ascii=False),
                answer_content=encoded,
            ),
            retriever=lambda *_: _package(evidence),
        )


def test_empty_selection_is_limited_and_zero_evidence_skips_second_call(
    tmp_path,
) -> None:
    empty_selection = _selection_payload(facet_count=1)
    evidence = _evidence()
    with_evidence = FakeClient(
        json.dumps(_valid_faceted_intent_payload(), ensure_ascii=False),
        answer_content=json.dumps(empty_selection),
    )

    limited_result = run_local_question(
        db_path=tmp_path / "with.sqlite",
        question="Welche Anforderungen gelten für Türen?",
        client=with_evidence,
        retriever=lambda *_: _package(evidence),
    )
    without_evidence = FakeClient(
        json.dumps(_valid_faceted_intent_payload(), ensure_ascii=False)
    )
    no_evidence_result = run_local_question(
        db_path=tmp_path / "without.sqlite",
        question="Welche Anforderungen gelten für Türen?",
        client=without_evidence,
        retriever=lambda *_: _package(),
    )

    assert len(with_evidence.payloads) == 2
    assert "keine geeigneten Belege gefunden" in limited_result.answer_text
    assert len(without_evidence.payloads) == 1
    assert no_evidence_result.raw_answer_model_output is None
    assert "keine geeigneten Belege gefunden" in no_evidence_result.answer_text


def test_main_anchor_guard_discards_unrelated_evidence_and_skips_selection(
    tmp_path,
) -> None:
    unrelated = _evidence(
        candidate_id="unrelated:lifts",
        statement="Anforderungen sind im Dokument enthalten.",
        passage="Die Anforderungen werden in einem Abschnitt beschrieben.",
    )
    client = FakeClient(
        json.dumps(_valid_faceted_intent_payload(), ensure_ascii=False)
    )

    with tempfile.TemporaryDirectory(prefix="local-anchor-negative-") as external:
        result = _run_evaluation_question(
            db_path=tmp_path / "operational.sqlite",
            question="Welche Anforderungen gelten für Türen?",
            client=client,
            retriever=lambda *_: _package(unrelated),
            output_dir=external,
        )
        protocol = json.loads(
            Path(result.run_protocol_path).read_text(encoding="utf-8")
        )

    assert len(client.payloads) == 1
    assert result.evidence_package.evidence == []
    assert result.raw_evidence_selection_model_output is None
    assert protocol["main_anchor_guard"] == {
        "version": "search_intent_main_anchor_guard_v1_20260816",
        "anchors": ["tueren"],
        "discarded_evidence_count": 1,
        "reason": "no_main_anchor_match",
    }
    assert len(protocol["retrieval_evidence_items"]) == 1
    assert protocol["post_anchor_guard_evidence_items"] == []
    assert protocol["status"] == "limited"


def test_quantenaufzug_main_anchor_guard_still_returns_zero_evidence(
    tmp_path,
) -> None:
    question = "Welche Eigenschaften hat der Quantenaufzug?"
    intent = {
        "schema_version": "2.0",
        "query_text": question,
        "facets": [
            {
                "facet_type": "object_property",
                "terms": ["Eigenschaften", "Quantenaufzug"],
                "required": True,
            }
        ],
        "retrieval_path": "fts5",
        "max_results": 10,
    }
    unrelated = _evidence(
        candidate_id="lift:ordinary",
        statement="Der Personenaufzug verbindet mehrere Geschosse.",
        passage="Ein Aufzug erschließt die Ebenen des Gebäudes.",
    )
    client = FakeClient(json.dumps(intent, ensure_ascii=False))

    result = run_local_question(
        db_path=tmp_path / "operational.sqlite",
        question=question,
        client=client,
        retriever=lambda *_: _package(unrelated),
    )

    assert len(client.payloads) == 1
    assert result.evidence_package.evidence == []
    assert result.raw_evidence_selection_model_output is None
    assert result.validated_evidence_selection["selected_evidence"] == []
    assert result.answer_text.startswith(
        "Evidenzbasierte Antwort\n\nStatus: Begrenzte Antwort: Mindestens "
        "ein Teil der Frage ist nicht vollständig belegt."
    )
    assert "## Objekteigenschaften" in result.answer_text
    assert "Gefragter Bezug: Eigenschaften, Quantenaufzug" in result.answer_text
    assert "Zu diesem Teil der Frage wurden keine geeigneten Belege gefunden." in (
        result.answer_text
    )
    assert "## Quellen" not in result.answer_text


def test_safe_prefix_anchor_match_continues_to_selection(tmp_path) -> None:
    question = "Welche Angaben gibt es zum Gebäude?"
    intent = {
        "schema_version": "2.0",
        "query_text": question,
        "facets": [
            {
                "facet_type": "object_property",
                "terms": ["Gebäude"],
                "required": True,
            }
        ],
        "retrieval_path": "fts5",
        "max_results": 10,
    }
    evidence = _evidence(
        candidate_id="building:prefix",
        statement="Die Gebäudeklasse ist dokumentiert.",
        passage="Eine Gebäudeklasse ist eingetragen.",
    )
    client = FakeClient(
        json.dumps(intent, ensure_ascii=False),
        answer_content=json.dumps(_selection_payload((evidence, (0,)))),
    )

    result = run_local_question(
        db_path=tmp_path / "operational.sqlite",
        question=question,
        client=client,
        retriever=lambda *_: _package(evidence),
    )

    assert len(client.payloads) == 2
    assert result.evidence_package.evidence == [evidence]
    assert evidence.normalized_statement in result.answer_text


@pytest.mark.parametrize(
    ("question", "facet_type", "term", "statement", "positive"),
    [
        (
            "Welche Anforderungen gelten für Türen?",
            "requirement",
            "Türen",
            "Türen müssen selbstschließend sein.",
            True,
        ),
        (
            "Welche Gebäudeklasse ist dokumentiert?",
            "object_property",
            "Gebäudeklasse",
            "Die Gebäudeklasse 4 ist dokumentiert.",
            True,
        ),
        (
            "Welche Anforderungen gelten für die Brandschutzwand?",
            "requirement",
            "Brandschutzwand",
            "Die Brandschutzwand muss feuerbeständig sein.",
            True,
        ),
        (
            "Welche Rettungsweglänge ist dokumentiert?",
            "object_property",
            "Rettungsweglänge",
            "Eine Rettungsweglänge von 25 Metern ist dokumentiert.",
            True,
        ),
        (
            "Welche Anforderungen gelten für die Brandschutzwand?",
            "requirement",
            "Brandschutzwand",
            "Aufzüge werden im Untergeschoss angeordnet.",
            False,
        ),
    ],
)
def test_five_mocked_end_to_end_anchor_cases(
    tmp_path,
    question,
    facet_type,
    term,
    statement,
    positive,
) -> None:
    intent = {
        "schema_version": "2.0",
        "query_text": question,
        "facets": [
            {
                "facet_type": facet_type,
                "terms": [term],
                "required": True,
            }
        ],
        "retrieval_path": "fts5",
        "max_results": 10,
    }
    evidence = _evidence(
        candidate_id=f"case:{term}",
        statement=statement,
        passage=statement,
    )
    client = FakeClient(
        json.dumps(intent, ensure_ascii=False),
        answer_content=(
            json.dumps(_selection_payload((evidence, (0,))), ensure_ascii=False)
            if positive
            else None
        ),
    )

    result = run_local_question(
        db_path=tmp_path / "operational.sqlite",
        question=question,
        client=client,
        retriever=lambda *_: _package(evidence),
    )

    assert len(client.payloads) == (2 if positive else 1)
    assert (statement in result.answer_text) is positive
    assert bool(result.evidence_package.evidence) is positive


def test_selected_evidence_without_every_assigned_facet_anchor_is_diagnostic(
    tmp_path,
) -> None:
    question = "Welche Anforderungen gelten für Türen in Räumen?"
    intent = {
        "schema_version": "2.0",
        "query_text": question,
        "facets": [
            {
                "facet_type": "requirement",
                "terms": ["Türen"],
                "required": True,
            },
            {
                "facet_type": "spatial_reference",
                "terms": ["Räumen"],
                "required": True,
            },
        ],
        "retrieval_path": "fts5",
        "max_results": 10,
    }
    wrongly_multi_assigned = _evidence(
        candidate_id="doors:only",
        statement="Türen müssen selbstschließend sein.",
        passage="Türen sind mit Schließmitteln ausgestattet.",
    )
    spatial = _evidence(
        candidate_id="rooms:spatial",
        statement="In Räumen ist die Lage gekennzeichnet.",
        passage="Die Räume sind im Plan bezeichnet.",
    )
    raw_selection = _selection_payload(
        (wrongly_multi_assigned, (0, 1)),
        (spatial, (1,)),
        facet_count=2,
    )

    with tempfile.TemporaryDirectory(prefix="local-anchor-selection-") as external:
        result = _run_evaluation_question(
            db_path=tmp_path / "operational.sqlite",
            question=question,
            client=FakeClient(
                json.dumps(intent, ensure_ascii=False),
                answer_content=json.dumps(raw_selection, ensure_ascii=False),
            ),
            retriever=lambda *_: _package(wrongly_multi_assigned, spatial),
            output_dir=external,
        )
        protocol = json.loads(
            Path(result.run_protocol_path).read_text(encoding="utf-8")
        )

    assert result.validated_evidence_selection == raw_selection
    assert result.validated_evidence_selection["facet_coverage"][0][
        "coverage_status"
    ] == "covered"
    assert wrongly_multi_assigned.normalized_statement in result.answer_text
    assert spatial.normalized_statement in result.answer_text
    assert protocol["rejected_selected_evidence"] == []
    assert protocol["selection_lexical_anchor_mismatches"] == [
        {
            "evidence_id": "doors:only#passage=1",
            "facet_index": 1,
            "facet_type": "spatial_reference",
            "facet_terms": ["Räumen"],
            "normalized_anchors": ["raeumen"],
            "reason": "no_lexical_anchor_match",
        }
    ]
    assert protocol["status"] == "success"


def test_brandwand_selection_keeps_both_lexically_different_items(
    tmp_path,
) -> None:
    question = (
        "Welche Änderung enthält die erste Fortschreibung hinsichtlich der "
        "Brandwände beziehungsweise Brandabschnittstrennungen und wie wird "
        "die geänderte Ausführung beschrieben?"
    )
    intent = {
        "schema_version": "2.0",
        "query_text": question,
        "facets": [
            {
                "facet_type": "project_finding",
                "terms": ["Änderung", "erste Fortschreibung", "Brandwände"],
                "required": True,
            },
            {
                "facet_type": "planning_measure",
                "terms": ["geänderte Ausführung", "Brandabschnittstrennungen"],
                "required": True,
            },
        ],
        "retrieval_path": "fts5",
        "max_results": 10,
    }
    finding = _evidence(
        candidate_id="batch_003:candidate_054",
        statement="Die bisher vorgesehene Gebäudetrennwand entfällt.",
        passage="Eine bislang vorgesehene Gebäudetrennwand ist nicht mehr vorgesehen.",
        statement_type="finding",
    )
    measure = _evidence(
        candidate_id="batch_001:candidate_002",
        statement="Stattdessen werden hochfeuerhemmende Wände geplant.",
        passage="Ersatzweise sind hochfeuerhemmende Wände vorgesehen.",
        statement_type="planning_measure",
    )
    anchor_context = _evidence(
        candidate_id="brandwand:context",
        statement="Brandwände trennen ausgedehnte Gebäude.",
        passage="Brandwände sind zur Unterteilung vorgesehen.",
    )
    selection = _selection_payload(
        (finding, (0,)),
        (measure, (1,)),
        facet_count=2,
        coverage_statuses=("partial", "covered"),
        uncovered_terms=(("Brandwände",), ()),
    )

    with tempfile.TemporaryDirectory(prefix="local-brandwand-diagnostic-") as external:
        result = _run_evaluation_question(
            db_path=tmp_path / "operational.sqlite",
            question=question,
            client=FakeClient(
                json.dumps(intent, ensure_ascii=False),
                answer_content=json.dumps(selection, ensure_ascii=False),
            ),
            retriever=lambda *_: _package(finding, measure, anchor_context),
            output_dir=external,
        )
        protocol = json.loads(
            Path(result.run_protocol_path).read_text(encoding="utf-8")
        )

    assert result.validated_evidence_selection == selection
    assert [
        item["evidence_id"]
        for item in result.validated_evidence_selection["selected_evidence"]
    ] == [
        "batch_003:candidate_054#passage=1",
        "batch_001:candidate_002#passage=1",
    ]
    assert [
        item["coverage_status"]
        for item in result.validated_evidence_selection["facet_coverage"]
    ] == ["partial", "covered"]
    assert finding.normalized_statement in result.answer_text
    assert measure.normalized_statement in result.answer_text
    assert protocol["status"] == "limited"
    assert len(protocol["selection_lexical_anchor_mismatches"]) == 2
    assert protocol["raw_evidence_selection_model_output"] == json.dumps(
        selection,
        ensure_ascii=False,
    )


def test_synthetic_class_selection_keeps_candidate_for_compound_wording(
    tmp_path,
) -> None:
    question = "Nach welcher Prüfklasse wird das Musterbauteil beurteilt?"
    intent = {
        "schema_version": "2.0",
        "query_text": question,
        "facets": [
            {
                "facet_type": "project_finding",
                "terms": ["Prüfklasse", "Musterbauteil"],
                "required": True,
            }
        ],
        "retrieval_path": "fts5",
        "max_results": 10,
    }
    synthetic_candidate = _evidence(
        candidate_id="batch_002:candidate_syn_049",
        statement=(
            "Das musterbauteilförmige Element wird der Prüfklasse 4 "
            "zugeordnet."
        ),
        passage=(
            "Ein musterbauteilförmiges Testelement ist in Prüfklasse 4 "
            "eingetragen."
        ),
        statement_type="finding",
    )
    selection = _selection_payload((synthetic_candidate, (0,)))

    result = run_local_question(
        db_path=tmp_path / "operational.sqlite",
        question=question,
        client=FakeClient(
            json.dumps(intent, ensure_ascii=False),
            answer_content=json.dumps(selection, ensure_ascii=False),
        ),
        retriever=lambda *_: _package(synthetic_candidate),
    )

    assert result.validated_evidence_selection == selection
    assert synthetic_candidate.normalized_statement in result.answer_text


def test_partial_facet_coverage_is_visible_and_limited(tmp_path) -> None:
    question = "Welche Nachweise gelten für Türen?"
    intent = {
        "schema_version": "2.0",
        "query_text": question,
        "facets": [
            {
                "facet_type": "requirement",
                "terms": ["Nachweise", "Türen"],
                "required": True,
            }
        ],
        "retrieval_path": "fts5",
        "max_results": 10,
    }
    evidence = _evidence(statement="Türen müssen selbstschließend sein.")
    selection = _selection_payload(
        (evidence, (0,)),
        coverage_statuses=("partial",),
        uncovered_terms=(("Nachweise",),),
    )

    with tempfile.TemporaryDirectory(prefix="local-partial-coverage-") as external:
        result = _run_evaluation_question(
            db_path=tmp_path / "operational.sqlite",
            question=question,
            client=FakeClient(
                json.dumps(intent, ensure_ascii=False),
                answer_content=json.dumps(selection, ensure_ascii=False),
            ),
            retriever=lambda *_: _package(evidence),
            output_dir=external,
        )
        protocol = json.loads(
            Path(result.run_protocol_path).read_text(encoding="utf-8")
        )

    assert "Abdeckung: teilweise. Offen: Nachweise." in result.answer_text
    assert protocol["status"] == "limited"
    _assert_complete_evaluation_context(protocol["evaluation_context"])
    assert protocol["selection_facet_coverage"] == selection["facet_coverage"]


@pytest.mark.parametrize(
    "selection",
    [
        _selection_payload(facet_count=1, coverage_statuses=("covered",)),
        _selection_payload(facet_count=1, coverage_statuses=("partial",)),
        {
            "schema_version": "2.0",
            "selected_evidence": [],
            "facet_coverage": [
                {
                    "facet_index": 0,
                    "coverage_status": "missing",
                    "uncovered_terms": ["fremder Begriff"],
                }
            ],
        },
    ],
)
def test_contradictory_or_nonliteral_selection_coverage_is_rejected(
    tmp_path,
    selection,
) -> None:
    evidence = _evidence()
    with pytest.raises(LocalLlmError):
        run_local_question(
            db_path=tmp_path / "operational.sqlite",
            question="Welche Anforderungen gelten für Türen?",
            client=FakeClient(
                json.dumps(_valid_faceted_intent_payload(), ensure_ascii=False),
                answer_content=json.dumps(selection, ensure_ascii=False),
            ),
            retriever=lambda *_: _package(evidence),
        )


def test_rejected_evidence_is_not_sent_to_selection_or_rendered(tmp_path) -> None:
    rejected = _evidence(
        candidate_id="rejected:candidate",
        statement="Diese verworfene Aussage darf nicht erscheinen.",
        review_status="rejected",
        review_label="verworfen",
    )
    client = FakeClient(
        json.dumps(_valid_faceted_intent_payload(), ensure_ascii=False)
    )

    result = run_local_question(
        db_path=tmp_path / "operational.sqlite",
        question="Welche Anforderungen gelten für Türen?",
        client=client,
        retriever=lambda *_: _package(rejected),
    )

    assert len(client.payloads) == 1
    assert rejected.normalized_statement not in result.answer_text
    assert result.validated_evidence_selection == {
        "schema_version": "2.0",
        "selected_evidence": [],
        "facet_coverage": [
            {
                "facet_index": 0,
                "coverage_status": "missing",
                "uncovered_terms": ["Türen"],
            }
        ],
    }


def test_selection_of_rejected_evidence_remains_a_hard_error(tmp_path) -> None:
    available = _evidence(candidate_id="available:doors")
    rejected = _evidence(
        candidate_id="rejected:doors",
        statement="Türen sind als verworfener Befund gespeichert.",
        review_status="rejected",
        review_label="verworfen",
    )
    invalid_selection = _selection_payload((rejected, (0,)))

    with pytest.raises(LocalLlmError, match="missing or unsupported evidence"):
        run_local_question(
            db_path=tmp_path / "operational.sqlite",
            question="Welche Anforderungen gelten für Türen?",
            client=FakeClient(
                json.dumps(_valid_faceted_intent_payload(), ensure_ascii=False),
                answer_content=json.dumps(invalid_selection, ensure_ascii=False),
            ),
            retriever=lambda *_: _package(available, rejected),
        )


def test_protocol_v3_records_stage_timings_and_optional_model_usage(tmp_path) -> None:
    evidence = _evidence()
    selection = _selection_payload((evidence, (0,)))
    usages = (
        {"prompt_tokens": 101, "completion_tokens": 22, "total_tokens": 123},
        {"prompt_tokens": 55, "completion_tokens": 11, "total_tokens": 66},
    )
    with tempfile.TemporaryDirectory(prefix="local-e2e-usage-") as external:
        result = _run_evaluation_question(
            db_path=tmp_path / "operational.sqlite",
            question="Welche Anforderungen gelten für Türen?",
            client=FakeClient(
                json.dumps(_valid_faceted_intent_payload(), ensure_ascii=False),
                answer_content=json.dumps(selection),
                usages=usages,
            ),
            retriever=lambda *_: _package(evidence),
            output_dir=external,
        )
        protocol = json.loads(
            Path(result.run_protocol_path).read_text(encoding="utf-8")
        )

    assert protocol["schema_version"] == RUN_PROTOCOL_SCHEMA_VERSION == (
        "local_e2e_run_v3"
    )
    assert set(protocol["timings_ms"]) == {
        "search_intent",
        "retrieval",
        "evidence_selection",
        "total",
    }
    assert all(value >= 0 for value in protocol["timings_ms"].values())
    assert protocol["token_usage"] == {
        "search_intent": usages[0],
        "evidence_selection": usages[1],
    }
    assert protocol["formatter_version"] == DETERMINISTIC_FACETED_ANSWER_VERSION


def test_historical_v2_protocol_schema_remains_recognized() -> None:
    historical_payload = json.loads(
        '{"schema_version":"local_e2e_run_v2","run_id":"historic"}'
    )

    assert LEGACY_RUN_PROTOCOL_SCHEMA_VERSION == "local_e2e_run_v2"
    assert SUPPORTED_RUN_PROTOCOL_SCHEMA_VERSIONS == {
        LEGACY_RUN_PROTOCOL_SCHEMA_VERSION,
        RUN_PROTOCOL_SCHEMA_VERSION,
    }
    assert historical_payload["schema_version"] in (
        SUPPORTED_RUN_PROTOCOL_SCHEMA_VERSIONS
    )
    assert "evaluation_context" not in historical_payload


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"project_id": None}, "project_id is required"),
        ({"question_id": "invalid/question"}, "question_id is invalid"),
        (
            {"model_artifact": "Qwen3-14B-Q5_K_M.gguf"},
            "model_artifact must include",
        ),
        (
            {"model_artifact": "Llama-14B-Q6_K_L.gguf"},
            "model_artifact does not match",
        ),
    ],
)
def test_invalid_evaluation_context_is_rejected_before_model_call(
    tmp_path,
    override: dict[str, str | None],
    message: str,
) -> None:
    client = FakeClient(json.dumps(_valid_faceted_intent_payload()))
    context = {**_EVALUATION_IDENTIFIERS, **override}
    with tempfile.TemporaryDirectory(prefix="local-e2e-context-invalid-") as external:
        with pytest.raises(LocalLlmError, match=message):
            run_local_question(
                db_path=_evaluation_database(tmp_path / "operational.sqlite"),
                question="Welche Anforderungen gelten für Türen?",
                client=client,
                retriever=lambda *_: _package(),
                output_dir=external,
                **context,
            )

    assert client.payloads == []


def test_missing_operational_database_is_rejected_before_model_call(
    monkeypatch,
    tmp_path,
) -> None:
    client = FakeClient(json.dumps(_valid_faceted_intent_payload()))
    monkeypatch.setattr(
        local_llm_module,
        "_git_freeze_context",
        lambda _: ("d" * 40, True),
    )
    with tempfile.TemporaryDirectory(prefix="local-e2e-database-invalid-") as external:
        with pytest.raises(LocalLlmError, match="database cannot be resolved"):
            run_local_question(
                db_path=tmp_path / "missing.sqlite",
                question="Welche Anforderungen gelten für Türen?",
                client=client,
                retriever=lambda *_: _package(),
                output_dir=external,
                **_EVALUATION_IDENTIFIERS,
            )

    assert client.payloads == []


@pytest.mark.parametrize(
    ("tracked_status", "expected_clean"),
    [("", True), (" M tracked-file.py", False)],
)
def test_git_freeze_context_detects_tracked_worktree_state(
    monkeypatch,
    tracked_status: str,
    expected_clean: bool,
) -> None:
    expected_commit = "b" * 40

    def git_output(repository_root: Path, *arguments: str) -> str:
        if arguments[0] == "rev-parse":
            return expected_commit
        return tracked_status

    monkeypatch.setattr(local_llm_module, "_run_git", git_output)

    assert local_llm_module._git_freeze_context(Path.cwd()) == (
        expected_commit,
        expected_clean,
    )


def test_dirty_tracked_worktree_is_rejected_before_model_call(
    monkeypatch,
    tmp_path,
) -> None:
    client = FakeClient(json.dumps(_valid_faceted_intent_payload()))

    def git_output(repository_root: Path, *arguments: str) -> str:
        if arguments[0] == "rev-parse":
            return "c" * 40
        return " M tracked-file.py"

    monkeypatch.setattr(local_llm_module, "_run_git", git_output)
    with tempfile.TemporaryDirectory(prefix="local-e2e-dirty-git-") as external:
        with pytest.raises(LocalLlmError, match="tracked Git worktree must be clean"):
            run_local_question(
                db_path=_evaluation_database(tmp_path / "operational.sqlite"),
                question="Welche Anforderungen gelten für Türen?",
                client=client,
                retriever=lambda *_: _package(),
                output_dir=external,
                **_EVALUATION_IDENTIFIERS,
            )

    assert client.payloads == []



def test_duplicate_evidence_id_in_package_remains_an_error(tmp_path) -> None:
    evidence = _evidence()

    with pytest.raises(LocalLlmError, match="duplicate evidence IDs"):
        run_local_question(
            db_path=tmp_path / "operational.sqlite",
            question="Welche Anforderungen gelten für Türen?",
            client=FakeClient(
                json.dumps(_valid_faceted_intent_payload(), ensure_ascii=False),
                answer_content=json.dumps(
                    _selection_payload((evidence, (0,))),
                    ensure_ascii=False,
                ),
            ),
            retriever=lambda *_: _package(evidence, evidence),
        )


def test_missing_required_provenance_data_remains_an_error(tmp_path) -> None:
    evidence = _evidence(locator="")

    with pytest.raises(LocalLlmError):
        run_local_question(
            db_path=tmp_path / "operational.sqlite",
            question="Welche Anforderungen gelten für Türen?",
            client=FakeClient(
                json.dumps(_valid_faceted_intent_payload(), ensure_ascii=False),
                answer_content=json.dumps(
                    _selection_payload((evidence, (0,))),
                    ensure_ascii=False,
                ),
            ),
            retriever=lambda *_: _package(evidence),
        )



def test_missing_facet_notice_is_added_without_answer_model_call(tmp_path) -> None:
    intent_payload = _missing_object_property_intent_payload()
    client = FakeClient(json.dumps(intent_payload, ensure_ascii=False))

    result = run_local_question(
        db_path=tmp_path / "operational.sqlite",
        question=str(intent_payload["query_text"]),
        client=client,
        retriever=lambda *_: _package(
            missing_required_facets=("object_property",)
        ),
    )

    assert len(client.payloads) == 1
    assert result.raw_answer_model_output is None
    assert "## Objekteigenschaften" in result.answer_text
    assert "Gefragter Bezug: Erfüllungsstatus" in result.answer_text
    assert "keine geeigneten Belege gefunden" in result.answer_text



def test_clean_tracked_worktree_receives_complete_atomic_success_protocol(
    monkeypatch,
    tmp_path,
) -> None:
    db_path = _evaluation_database(tmp_path / "operational.sqlite")
    before_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()
    expected_commit = "a" * 40

    def git_output(repository_root: Path, *arguments: str) -> str:
        assert repository_root == Path(local_llm_module.__file__).resolve().parents[3]
        if arguments[0] == "rev-parse":
            return expected_commit
        assert arguments[0] == "status"
        return ""

    monkeypatch.setattr(local_llm_module, "_run_git", git_output)
    evidence = _evidence()
    package = _package(evidence)
    retrieval_databases: list[Path] = []

    def retrieve(path: str | Path, intent: SearchIntent) -> EvidencePackage:
        retrieval_databases.append(Path(path))
        return package

    client = FakeClient(
        json.dumps(_valid_faceted_intent_payload(), ensure_ascii=False),
        answer_content=json.dumps(
            _selection_payload((evidence, (0,))), ensure_ascii=False
        ),
    )

    with tempfile.TemporaryDirectory(prefix="local-e2e-success-") as external:
        output_dir = Path(external)
        result = run_local_question(
            db_path=db_path,
            question="Welche Anforderungen gelten für Türen?",
            client=client,
            retriever=retrieve,
            output_dir=output_dir,
            **_EVALUATION_IDENTIFIERS,
        )

        assert result.run_protocol_path is not None
        protocol_path = Path(result.run_protocol_path)
        assert protocol_path.parent == output_dir.resolve()
        protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
        assert protocol["schema_version"] == RUN_PROTOCOL_SCHEMA_VERSION
        assert protocol["run_id"] == result.run_id
        assert protocol["started_at"] <= protocol["ended_at"]
        assert protocol["duration_ms"] >= 0
        assert protocol["status"] == "success"
        assert protocol["user_question"] == "Welche Anforderungen gelten für Türen?"
        assert protocol["endpoint"] == LM_STUDIO_ENDPOINT
        assert protocol["model_id"] == LM_STUDIO_MODEL
        _assert_complete_evaluation_context(protocol["evaluation_context"])
        assert protocol["evaluation_context"] == {
            **_EVALUATION_IDENTIFIERS,
            "git_commit": expected_commit,
            "tracked_worktree_clean": True,
            "database_sha256": before_hash,
            "database_user_version": 4,
            "model_id": LM_STUDIO_MODEL,
            "prompt_versions": {
                "search_intent": SEARCH_INTENT_PROMPT_VERSION,
                "evidence_selection": EVIDENCE_SELECTION_PROMPT_VERSION,
            },
            "adapter_schema_versions": {
                "search_intent": SEARCH_INTENT_ADAPTER_SCHEMA_VERSION,
                "evidence_selection": EVIDENCE_SELECTION_ADAPTER_SCHEMA_VERSION,
            },
            "formatter_version": DETERMINISTIC_FACETED_ANSWER_VERSION,
            "protocol_schema_version": RUN_PROTOCOL_SCHEMA_VERSION,
        }
        assert protocol["prompt_versions"] == {
            "search_intent": SEARCH_INTENT_PROMPT_VERSION,
            "evidence_selection": EVIDENCE_SELECTION_PROMPT_VERSION,
        }
        assert protocol["adapter_schema_versions"] == {
            "search_intent": SEARCH_INTENT_ADAPTER_SCHEMA_VERSION,
            "evidence_selection": EVIDENCE_SELECTION_ADAPTER_SCHEMA_VERSION,
        }
        assert protocol["raw_search_intent_model_output"] != protocol[
            "validated_search_intent"
        ]
        assert protocol["validated_search_intent"] == result.search_intent.to_dict()
        assert protocol["facets"] == [
            result.search_intent.facets[0].to_dict()
        ]
        assert protocol["executed_subintents"] == []
        assert protocol["retrieval_strategy"] == "fts5"
        assert protocol["retrieval_trace"] == package.trace.to_dict()
        assert protocol["evidence_items"] == [evidence.to_dict()]
        assert protocol["facet_coverage"] == []
        assert protocol["missing_required_facets"] == []
        assert protocol["raw_evidence_selection_model_output"] != protocol[
            "validated_answer"
        ]
        assert protocol["validated_answer"] == result.answer_text
        assert protocol["error"] is None
        assert not list(protocol_path.parent.glob("*.tmp"))
    assert retrieval_databases == [db_path.resolve()]
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before_hash


def test_invalid_search_intent_writes_limited_error_protocol(tmp_path) -> None:
    temporary_output = tempfile.TemporaryDirectory(prefix="local-e2e-error-")
    output_dir = Path(temporary_output.name)

    with pytest.raises(LocalLlmError):
        _run_evaluation_question(
            db_path=tmp_path / "operational.sqlite",
            question="Welche Anforderungen gelten für Türen?",
            client=FakeClient("not json"),
            retriever=lambda *_: (_ for _ in ()).throw(
                AssertionError("retrieval must not run")
            ),
            output_dir=output_dir,
        )

    files = list(output_dir.glob("*.json"))
    assert len(files) == 1
    protocol = json.loads(files[0].read_text(encoding="utf-8"))
    assert protocol["status"] == "error"
    _assert_complete_evaluation_context(protocol["evaluation_context"])
    assert protocol["raw_search_intent_model_output"] == "not json"
    assert protocol["validated_search_intent"] is None
    assert protocol["raw_evidence_selection_model_output"] is None
    assert protocol["validated_evidence_selection"] is None
    assert protocol["validated_answer"] is None
    assert protocol["error"]["type"] == "LocalLlmError"
    assert "JSON object" in protocol["error"]["message"]
    temporary_output.cleanup()


def test_http_400_protocol_contains_bounded_lm_studio_response_text(
    tmp_path,
) -> None:
    visible_error = "Failed to initialize samplers: failed to parse grammar"
    hidden_tail = "must-not-appear-after-limit"
    response_body = (visible_error + " " + ("x" * 10_000) + hidden_tail).encode(
        "utf-8"
    )

    class FailingOpener:
        def open(self, request, *, timeout):
            raise urllib.error.HTTPError(
                request.full_url,
                400,
                "Bad Request",
                hdrs=None,
                fp=io.BytesIO(response_body),
            )

    client = LoopbackLmStudioClient()
    client._opener = FailingOpener()

    with tempfile.TemporaryDirectory(prefix="local-e2e-http-error-") as external:
        with pytest.raises(LocalLlmError, match="HTTP 400"):
            _run_evaluation_question(
                db_path=tmp_path / "operational.sqlite",
                question="Welche Anforderungen gelten für Türen?",
                client=client,
                retriever=lambda *_: (_ for _ in ()).throw(
                    AssertionError("retrieval must not run")
                ),
                output_dir=external,
            )

        files = list(Path(external).glob("*.json"))
        assert len(files) == 1
        protocol = json.loads(files[0].read_text(encoding="utf-8"))
        _assert_complete_evaluation_context(protocol["evaluation_context"])
        message = protocol["error"]["message"]
        assert "HTTP 400" in message
        assert visible_error in message
        assert hidden_tail not in message
        assert len(message) < 5_000


def test_output_dir_inside_repository_is_rejected_before_model_call() -> None:
    repository_root = Path(local_llm_module.__file__).resolve().parents[3]
    client = FakeClient(json.dumps(_valid_intent_payload()))

    with pytest.raises(LocalLlmError, match="outside the repository"):
        run_local_question(
            db_path="unused.sqlite",
            question="Welche Anforderungen gelten für Türen?",
            client=client,
            retriever=lambda *_: _package(),
            output_dir=repository_root / ".forbidden-run-output",
        )

    assert client.payloads == []


def test_existing_run_protocol_is_never_overwritten(monkeypatch, tmp_path) -> None:
    evidence = _evidence()
    monkeypatch.setattr(local_llm_module, "_new_run_id", lambda: "fixed-run-id")
    with tempfile.TemporaryDirectory(prefix="local-e2e-collision-") as external:
        output_dir = Path(external)

        def execute() -> LocalQuestionResult:
            return _run_evaluation_question(
                db_path=tmp_path / "operational.sqlite",
                    question="Welche Anforderungen gelten für Türen?",
                    client=FakeClient(
                        json.dumps(
                            _valid_faceted_intent_payload(), ensure_ascii=False
                        ),
                        answer_content=json.dumps(
                            _selection_payload((evidence, (0,))),
                            ensure_ascii=False,
                        ),
                ),
                retriever=lambda *_: _package(evidence),
                output_dir=output_dir,
            )

        first = execute()
        protocol_path = Path(first.run_protocol_path)
        original_bytes = protocol_path.read_bytes()

        with pytest.raises(LocalLlmError, match="already exists"):
            execute()

        assert protocol_path.read_bytes() == original_bytes
        protocol_files = list(output_dir.glob("*.json"))
        assert len(protocol_files) == 1
        assert protocol_files[0].samefile(protocol_path)


def test_cli_ask_local_requires_output_dir_before_model_call(
    monkeypatch, tmp_path
) -> None:
    from info_system.cli import main

    def run(**kwargs: object) -> LocalQuestionResult:
        raise AssertionError("model path must not run without --output-dir")

    monkeypatch.setattr("info_system.cli.run_local_question", run)

    with pytest.raises(SystemExit, match="2"):
        main(
            [
                "ask-local",
                "Welche Anforderungen gelten für Türen?",
                "--db",
                str(tmp_path / "operational.sqlite"),
                "--project-id",
                _EVALUATION_IDENTIFIERS["project_id"],
                "--question-id",
                _EVALUATION_IDENTIFIERS["question_id"],
                "--model-artifact",
                _EVALUATION_IDENTIFIERS["model_artifact"],
                "--model-quantization",
                _EVALUATION_IDENTIFIERS["model_quantization"],
            ]
        )


def test_cli_ask_local_accepts_external_output_dir(
    monkeypatch, tmp_path, capsys
) -> None:
    from info_system.cli import main

    output_dir = tmp_path / "private-runs"
    protocol_path = output_dir / "local-e2e-run-v1_test.json"
    intent = SearchIntent.from_dict(_valid_intent_payload())
    expected = LocalQuestionResult(
        endpoint=LM_STUDIO_ENDPOINT,
        model=LM_STUDIO_MODEL,
        search_intent=intent,
        evidence_package=_package(_evidence()),
        answer_text="Begrenzte Antwort mit Quellen.",
        run_id="test",
        run_protocol_path=str(protocol_path),
    )

    def run(**kwargs: object) -> LocalQuestionResult:
        assert kwargs == {
            "db_path": tmp_path / "operational.sqlite",
            "question": "Welche Anforderungen gelten für Türen?",
            "output_dir": output_dir,
            **_EVALUATION_IDENTIFIERS,
        }
        return expected

    monkeypatch.setattr("info_system.cli.run_local_question", run)

    exit_code = main(
        [
            "ask-local",
            "Welche Anforderungen gelten für Türen?",
            "--db",
            str(tmp_path / "operational.sqlite"),
            "--output-dir",
            str(output_dir),
            "--project-id",
            _EVALUATION_IDENTIFIERS["project_id"],
            "--question-id",
            _EVALUATION_IDENTIFIERS["question_id"],
            "--model-artifact",
            _EVALUATION_IDENTIFIERS["model_artifact"],
            "--model-quantization",
            _EVALUATION_IDENTIFIERS["model_quantization"],
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert f"run_protocol: {protocol_path}" in output
