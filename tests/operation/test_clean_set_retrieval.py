from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from info_system.cli import main
from info_system.db import initialize_database
from info_system.operation.clean_set_retrieval import (
    CONTEXT_FIELD_WEIGHTS,
    EvidenceRetrievalError,
    SearchFacet,
    SearchIntent,
    SearchIntentError,
    _context_is_complementary,
    _is_safe_fts5_prefix_token,
    find_evidence,
)


def _faceted_intent_payload(
    *,
    query_text: str = (
        "Welche Türen bleiben offen und welche lichte Breite gilt?"
    ),
    terms: list[str] | None = None,
    facets: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "2.0",
        "query_text": query_text,
        "terms": terms or ["offen"],
        "facets": (
            facets
            if facets is not None
            else [
                {
                    "facet_type": "object_property",
                    "terms": ["lichte Breite"],
                    "statement_types": ["requirement"],
                    "required": True,
                }
            ]
        ),
        "retrieval_path": "fts5",
        "max_results": 5,
    }


def test_search_intent_is_strict_and_rejects_sql_or_shell_fields() -> None:
    intent = SearchIntent.from_dict(
        {
            "terms": ["Türen", "rauchdicht"],
            "statement_types": ["requirement"],
            "binding_statuses": ["binding"],
            "document_types": ["pdf"],
            "object_features": {
                "entity_type": "door",
                "entity_label": "Bauteil X-01",
                "entity_role": "subject",
            },
            "retrieval_path": "hybrid",
            "max_results": 5,
        }
    )

    assert intent.to_dict()["retrieval_path"] == "hybrid"
    assert intent.max_results == 5

    invalid = (
        {},
        {"retrieval_path": "structured", "max_results": 5},
        {"terms": [], "retrieval_path": "fts5", "max_results": 5},
        {"terms": ["Tür"], "retrieval_path": "shell", "max_results": 5},
        {"terms": ["Tür"], "retrieval_path": "fts5", "max_results": 0},
        {"terms": ["Tür"], "retrieval_path": "fts5", "max_results": 21},
        {"terms": ["Tür; DROP TABLE statements"], "retrieval_path": "fts5", "max_results": 5},
        {"terms": ["$(whoami)"], "retrieval_path": "fts5", "max_results": 5},
        {"sql": "SELECT * FROM statements", "retrieval_path": "structured", "max_results": 5},
        {"terms": ["Tür"], "where": "1=1", "retrieval_path": "fts5", "max_results": 5},
    )
    for payload in invalid:
        with pytest.raises(SearchIntentError):
            SearchIntent.from_dict(payload)


def test_search_intent_v2_accepts_at_most_three_anchored_facets() -> None:
    payload = _faceted_intent_payload(
        query_text=(
            "Welche Anforderungen gelten für Türen und welche Ausführungen "
            "sind im notwendigen Treppenraum geplant?"
        ),
        terms=["Türen", "notwendiger Treppenraum"],
        facets=[
            {
                "facet_type": "requirement",
                "terms": ["Anforderungen", "Türen"],
                "statement_types": ["requirement"],
                "required": True,
            },
            {
                "facet_type": "planning_measure",
                "terms": ["Ausführungen", "notwendigen Treppenraum"],
                "statement_types": ["planning_measure"],
                "required": False,
            },
        ],
    )

    intent = SearchIntent.from_dict(payload)

    assert intent.schema_version == "2.0"
    assert intent.query_text == payload["query_text"]
    assert intent.facets == (
        SearchFacet(
            facet_type="requirement",
            terms=("Anforderungen", "Türen"),
            statement_types=("requirement",),
            required=True,
        ),
        SearchFacet(
            facet_type="planning_measure",
            terms=("Ausführungen", "notwendigen Treppenraum"),
            statement_types=("planning_measure",),
            required=False,
        ),
    )
    assert SearchIntent.from_dict(
        {"terms": ["Tür"], "retrieval_path": "fts5", "max_results": 5}
    ).schema_version == "1.0"


@pytest.mark.parametrize(
    "facets, error",
    [
        (
            [
                {"facet_type": "requirement", "terms": ["Türen"]},
                {"facet_type": "project_finding", "terms": ["Türen"]},
                {"facet_type": "planning_measure", "terms": ["Türen"]},
                {"facet_type": "object_property", "terms": ["Türen"]},
            ],
            "at most 3",
        ),
        ([{"facet_type": "requirement", "terms": []}], "terms"),
        (
            [
                {"facet_type": "requirement", "terms": ["Türen"]},
                {"facet_type": "requirement", "terms": ["Türen"]},
            ],
            "duplicate",
        ),
        (
            [{"facet_type": "requirement", "terms": ["Dachterrasse"]}],
            "query_text",
        ),
        (
            [{"facet_type": "requirement", "terms": ["Anforderungen"]}],
            "anchor",
        ),
        (
            [{"facet_type": "requirement", "terms": ["SELECT"]}],
            "forbidden",
        ),
    ],
)
def test_search_intent_v2_rejects_uncontrolled_facets(
    facets: list[dict[str, object]], error: str
) -> None:
    query_text = (
        "Welche Anforderungen gelten für Türen und SELECT welche Ausführungen "
        "sind im notwendigen Treppenraum geplant?"
    )
    with pytest.raises(SearchIntentError, match=error):
        SearchIntent.from_dict(
            _faceted_intent_payload(
                query_text=query_text,
                terms=["Türen"],
                facets=facets,
            )
        )


def test_search_intent_v2_rejects_facets_without_versioned_question() -> None:
    payload = _faceted_intent_payload()
    payload.pop("query_text")
    with pytest.raises(SearchIntentError, match="query_text"):
        SearchIntent.from_dict(payload)

    payload = _faceted_intent_payload()
    payload["schema_version"] = "1.0"
    with pytest.raises(SearchIntentError, match="2.0"):
        SearchIntent.from_dict(payload)


def test_synthetic_facets_are_validated_only_against_their_questions() -> None:
    fixture_path = (
        Path(__file__).parents[1]
        / "fixtures"
        / "retrieval"
        / "synthetic_search_intents.json"
    )
    fixture_text = fixture_path.read_text(encoding="utf-8")
    cases = json.loads(fixture_text)["cases"]

    assert "gold" not in fixture_text.casefold()

    for case in cases:
        specification = case["intent"]
        payload = {
            "schema_version": specification["schema_version"],
            "query_text": specification["query_text"],
            "terms": specification["search_terms"],
            "facets": specification["facets"],
            "retrieval_path": "fts5",
            "max_results": specification["result_limit"],
        }
        intent = SearchIntent.from_dict(payload)
        assert intent.schema_version == "2.0"
        assert 1 <= len(intent.facets) <= 3
        assert all(facet.terms for facet in intent.facets)
        assert intent.to_dict() == SearchIntent.from_dict(payload).to_dict()

    foreign_facet_payload = json.loads(json.dumps(cases[1]["intent"]))
    foreign_facet_payload["facets"] = [cases[0]["intent"]["facets"][0]]
    with pytest.raises(SearchIntentError, match="query_text"):
        SearchIntent.from_dict(
            {
                "schema_version": foreign_facet_payload["schema_version"],
                "query_text": foreign_facet_payload["query_text"],
                "terms": foreign_facet_payload["search_terms"],
                "facets": foreign_facet_payload["facets"],
                "retrieval_path": "fts5",
                "max_results": foreign_facet_payload["result_limit"],
            }
        )


def test_structured_filters_use_reviewed_values_and_object_features(tmp_path: Path) -> None:
    db_path = _clean_set_database(tmp_path / "clean.sqlite")
    result = find_evidence(
        db_path,
        SearchIntent.from_dict(
            {
                "statement_types": ["requirement"],
                "binding_statuses": ["binding"],
                "document_ids": ["doc-1"],
                "document_types": ["pdf"],
                "object_features": {
                    "entity_type": "door",
                    "entity_label": "Bauteil X-01",
                    "entity_role": "subject",
                },
                "retrieval_path": "structured",
                "max_results": 5,
            }
        ),
    )

    assert [item.candidate_id for item in result.evidence] == [
        "batch_001:candidate_001",
        "batch_001:candidate_001",
    ]
    assert [item.exact_evidence_text for item in result.evidence] == [
        "Türen müssen rauchdicht sein.",
        "Die lichte Breite beträgt 0,90 m.",
    ]
    assert all(
        item.normalized_statement
        == "Türen müssen rauchdicht und selbstschließend sein."
        for item in result.evidence
    )
    assert all(item.review_status == "confirmed" for item in result.evidence)
    assert all(item.review_status_label == "fachlich_geprüft" for item in result.evidence)
    assert all(item.value_origin == "review_overlay" for item in result.evidence)
    assert all(item.document_version_id == "doc-1:v1" for item in result.evidence)
    assert [item.locator for item in result.evidence] == [
        "page=1;section=doors",
        "page=2;section=doors",
    ]
    assert result.trace.iteration_count == 1
    assert result.trace.iterations[0].method == "sql"


def test_structured_retrieval_includes_raw_unreviewed_candidates_and_excludes_rejected(
    tmp_path: Path,
) -> None:
    db_path = _clean_set_database(tmp_path / "clean.sqlite")
    result = find_evidence(
        db_path,
        SearchIntent.from_dict(
            {
                "statement_types": ["requirement"],
                "binding_statuses": ["binding"],
                "retrieval_path": "structured",
                "max_results": 10,
            }
        ),
    )

    by_candidate = {item.candidate_id: item for item in result.evidence}
    assert set(by_candidate) == {
        "batch_001:candidate_001",
        "batch_001:candidate_004",
        "batch_001:candidate_006",
    }
    confirmed = by_candidate["batch_001:candidate_001"]
    assert confirmed.normalized_statement == (
        "Türen müssen rauchdicht und selbstschließend sein."
    )
    assert confirmed.review_status == "confirmed"
    assert confirmed.review_status_label == "fachlich_geprüft"
    assert confirmed.value_origin == "review_overlay"

    needs_review = by_candidate["batch_001:candidate_004"]
    assert needs_review.normalized_statement == "Model unclear"
    assert needs_review.exact_evidence_text == "Ungeklärte Forderung ist rauchdicht."
    assert needs_review.review_status == "needs_review"
    assert needs_review.review_status_label == "automatisch_extrahiert_ungeprüft"
    assert needs_review.value_origin == "raw_candidate"

    without_review = by_candidate["batch_001:candidate_006"]
    assert without_review.normalized_statement == "Model without review"
    assert without_review.exact_evidence_text == (
        "Nicht geprüfte Forderung ist rauchdicht."
    )
    assert without_review.review_status == "needs_review"
    assert without_review.review_status_label == "automatisch_extrahiert_ungeprüft"
    assert without_review.value_origin == "raw_candidate"

    assert all(item.document_id == "doc-1" for item in result.evidence)
    assert all(item.document_version_id == "doc-1:v1" for item in result.evidence)
    assert all(item.locator is not None for item in result.evidence)
    serialized = json.dumps(result.to_dict(), ensure_ascii=False)
    assert "batch_001:candidate_003" not in serialized
    assert "Verworfene Forderung" not in serialized


def test_fts5_projection_indexes_reviewed_normalized_text_and_is_deterministic(
    tmp_path: Path,
) -> None:
    db_path = _clean_set_database(tmp_path / "clean.sqlite")
    intent = SearchIntent.from_dict(
        {
            "terms": ["selbstschließend"],
            "retrieval_path": "fts5",
            "max_results": 5,
        }
    )

    first = find_evidence(db_path, intent)
    second = find_evidence(db_path, intent)

    assert first.to_dict() == second.to_dict()
    assert [item.passage_order for item in first.evidence] == [1, 2]
    assert all(item.retrieval_method == "fts5" for item in first.evidence)
    assert all(item.review_status_label == "fachlich_geprüft" for item in first.evidence)
    assert all(item.value_origin == "review_overlay" for item in first.evidence)
    assert first.trace.fts5_projection is not None
    assert {
        key: value
        for key, value in first.trace.fts5_projection.items()
        if key != "context"
    } == {
        "authoritative": False,
        "row_count": 6,
        "source_tables": [
            "statement_candidates_v3",
            "candidate_review_overlays",
            "candidate_review_evidence_passages",
        ],
        "storage": "in_memory",
        "effective_term_groups": [["selbstschließend"]],
        "match_expression": '"selbstschließend"',
        "exact_match_expression": '"selbstschließend"',
        "prefix_match_expression": '"selbstschließend"*',
    }


@pytest.mark.parametrize(
    ("term", "candidate_id", "raw_value", "original_passage"),
    (
        (
            "Model unclear",
            "batch_001:candidate_004",
            "Model unclear",
            "Ungeklärte Forderung ist rauchdicht.",
        ),
        (
            "Model without review",
            "batch_001:candidate_006",
            "Model without review",
            "Nicht geprüfte Forderung ist rauchdicht.",
        ),
    ),
)
def test_fts5_retrieval_uses_raw_values_for_unreviewed_candidates(
    tmp_path: Path,
    term: str,
    candidate_id: str,
    raw_value: str,
    original_passage: str,
) -> None:
    db_path = _clean_set_database(tmp_path / "clean.sqlite")
    result = find_evidence(
        db_path,
        SearchIntent.from_dict(
            {
                "terms": [term],
                "retrieval_path": "fts5",
                "max_results": 5,
            }
        ),
    )

    assert len(result.evidence) == 1
    item = result.evidence[0]
    assert item.candidate_id == candidate_id
    assert item.normalized_statement == raw_value
    assert item.exact_evidence_text == original_passage
    assert item.review_status == "needs_review"
    assert item.review_status_label == "automatisch_extrahiert_ungeprüft"
    assert item.value_origin == "raw_candidate"
    assert item.document_id == "doc-1"
    assert item.document_version_id == "doc-1:v1"
    assert item.locator is not None


def test_fts5_projection_matches_german_umlaut_transliteration(
    tmp_path: Path,
) -> None:
    db_path = _clean_set_database(tmp_path / "clean.sqlite")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE candidate_review_overlays
            SET reviewed_normalized_text =
                'Tueren muessen rauchdicht und selbstschliessend sein.'
            WHERE candidate_review_pk = 1
            """
        )
        connection.execute(
            """
            UPDATE document_nodes
            SET source_text = 'Tueren muessen rauchdicht sein.'
            WHERE document_node_pk = 1
            """
        )
        connection.execute(
            """
            UPDATE candidate_review_evidence_passages
            SET exact_text = 'Tueren muessen rauchdicht sein.',
                char_end_in_node = length('Tueren muessen rauchdicht sein.')
            WHERE candidate_review_pk = 1 AND passage_order = 1
            """
        )

    result = find_evidence(
        db_path,
        SearchIntent.from_dict(
            {
                "terms": ["T\u00fcren"],
                "retrieval_path": "fts5",
                "max_results": 5,
            }
        ),
    )

    assert "batch_001:candidate_001" in {
        item.candidate_id for item in result.evidence
    }
    assert result.trace.iterations[0].candidate_ids == [
        "batch_001:candidate_001"
    ]


def test_fts5_projection_compiles_multiword_terms_to_safe_lexeme_groups(
    tmp_path: Path,
) -> None:
    db_path = _clean_set_database(tmp_path / "clean.sqlite")

    result = find_evidence(
        db_path,
        SearchIntent.from_dict(
            {
                "terms": ["rauchdichte geplant"],
                "retrieval_path": "fts5",
                "max_results": 5,
            }
        ),
    )

    assert [item.candidate_id for item in result.evidence] == [
        "batch_001:candidate_002"
    ]
    assert result.trace.search_intent["terms"] == ["rauchdichte geplant"]
    assert result.trace.fts5_projection is not None
    assert result.trace.fts5_projection["effective_term_groups"] == [
        ["rauchdichte", "geplant"]
    ]
    assert result.trace.fts5_projection["match_expression"] == (
        '("rauchdichte" AND "geplant")'
    )


@pytest.mark.parametrize(
    ("token", "expected"),
    (
        ("Türen", True),
        ("Rettungsweglänge", True),
        ("Tür", False),
        ("12345", False),
        ("X01", False),
        ("A-100", False),
    ),
)
def test_fts5_prefixes_only_sufficiently_long_alphabetic_tokens(
    token: str,
    expected: bool,
) -> None:
    assert _is_safe_fts5_prefix_token(token) is expected


def test_fts5_keeps_exact_and_prefix_rankings_separate_and_deduplicated(
    tmp_path: Path,
) -> None:
    db_path = _clean_set_database(tmp_path / "clean.sqlite")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE statements SET text = 'Rettungsweglängen bleiben offen.' "
            "WHERE statement_pk = 6"
        )
        connection.execute(
            "UPDATE statement_candidates_v3 "
            "SET evidence_source_text = 'Rettungsweglängen bleiben offen.' "
            "WHERE statement_candidate_v3_pk = 6"
        )
        connection.execute(
            "UPDATE document_nodes SET source_text = 'Rettungsweglängen bleiben offen.' "
            "WHERE document_node_pk = 7"
        )
    intent = SearchIntent.from_dict(
        {
            "terms": ["Rettungsweglänge"],
            "retrieval_path": "fts5",
            "max_results": 5,
        }
    )

    first = find_evidence(db_path, intent)
    second = find_evidence(db_path, intent)

    assert first.to_dict() == second.to_dict()
    assert [item.candidate_id for item in first.evidence] == [
        "batch_001:candidate_006"
    ]
    assert first.evidence[0].exact_evidence_text == "Rettungsweglängen bleiben offen."
    assert first.evidence[0].document_version_id == "doc-1:v1"
    assert first.evidence[0].locator == "page=7;section=doors"
    assert first.evidence[0].review_status_label == (
        "automatisch_extrahiert_ungeprüft"
    )
    assert first.evidence[0].value_origin == "raw_candidate"
    assert [iteration.method for iteration in first.trace.iterations] == [
        "fts5_exact",
        "fts5_prefix",
        "fts5_context",
    ]
    assert first.trace.iterations[0].candidate_ids == []
    assert first.trace.iterations[1].candidate_ids == [
        "batch_001:candidate_006"
    ]
    assert first.trace.fts5_projection is not None
    assert first.trace.fts5_projection["exact_match_expression"] == (
        '"Rettungsweglänge" OR "Rettungsweglaenge"'
    )
    assert first.trace.fts5_projection["prefix_match_expression"] == (
        '"Rettungsweglänge"* OR "Rettungsweglaenge"*'
    )


def test_fts5_candidate_matching_exact_and_prefix_lists_is_returned_once(
    tmp_path: Path,
) -> None:
    db_path = _clean_set_database(tmp_path / "clean.sqlite")
    result = find_evidence(
        db_path,
        SearchIntent.from_dict(
            {
                "terms": ["selbstschließend"],
                "retrieval_path": "fts5",
                "max_results": 5,
            }
        ),
    )

    assert [item.candidate_id for item in result.evidence] == [
        "batch_001:candidate_001",
        "batch_001:candidate_001",
    ]
    assert result.trace.iterations[0].candidate_ids == [
        "batch_001:candidate_001"
    ]
    assert result.trace.iterations[1].candidate_ids == [
        "batch_001:candidate_001"
    ]


def test_context_projection_uses_one_general_weighting_and_only_existing_fields(
    tmp_path: Path,
) -> None:
    db_path = _clean_set_database(tmp_path / "clean.sqlite")

    result = find_evidence(
        db_path,
        SearchIntent.from_dict(
            {
                "terms": ["Türen"],
                "retrieval_path": "fts5",
                "max_results": 10,
            }
        ),
    )

    assert CONTEXT_FIELD_WEIGHTS == {
        "normalized_statement": 5.0,
        "original_passage": 4.0,
        "section_heading": 3.0,
        "parent_heading": 2.0,
        "document_title": 1.0,
        "document_type": 0.5,
        "object_context": 2.0,
        "value_context": 2.0,
    }
    assert result.trace.fts5_projection is not None
    assert result.trace.fts5_projection["context"]["field_weights"] == (
        CONTEXT_FIELD_WEIGHTS
    )
    assert result.trace.fts5_projection["context"]["source_columns"] == {
        "normalized_statement": "statements.text/review overlay",
        "original_passage": "candidate/review evidence passage",
        "section_heading": "document_nodes.heading",
        "parent_heading": "parent document_nodes.heading",
        "document_title": "documents.title/filename",
        "document_type": "documents.file_type",
        "object_context": "statement_entities/entities/entity_aliases",
        "value_context": "statement_values",
    }


def test_context_fusion_is_complementary_deterministic_and_traceable(
    tmp_path: Path,
) -> None:
    db_path = _clean_set_database(tmp_path / "clean.sqlite")
    intent = SearchIntent.from_dict(
        {
            "terms": ["Türen"],
            "retrieval_path": "fts5",
            "max_results": 10,
        }
    )

    first = find_evidence(db_path, intent)
    second = find_evidence(db_path, intent)

    assert first.to_dict() == second.to_dict()
    assert [iteration.method for iteration in first.trace.iterations] == [
        "fts5_exact",
        "fts5_prefix",
        "fts5_context",
    ]
    assert first.trace.fusion == {
        "method": "reciprocal_rank_fusion",
        "k": 60,
        "complementarity_criterion": (
            "context top-N contains an allowed candidate-passage key absent "
            "from baseline top-N"
        ),
        "complementary": True,
        "activated": True,
    }
    row_keys = [(item.candidate_id, item.passage_order) for item in first.evidence]
    assert len(row_keys) == len(set(row_keys))
    assert {item.review_status for item in first.evidence} == {
        "confirmed",
        "needs_review",
    }
    assert "batch_001:candidate_003" not in {
        item.candidate_id for item in first.evidence
    }
    assert all(item.exact_evidence_text for item in first.evidence)
    assert all(item.document_id == "doc-1" for item in first.evidence)
    assert all(item.document_version_id == "doc-1:v1" for item in first.evidence)
    assert all(item.locator for item in first.evidence)
    context_trace = first.trace.iterations[2]
    assert context_trace.ranked_hits
    assert all("component_rank" in hit for hit in context_trace.ranked_hits)
    assert all("component_score" in hit for hit in context_trace.ranked_hits)
    assert all("fusion_contribution" in hit for hit in context_trace.ranked_hits)
    assert all("final_rank" in hit for hit in context_trace.ranked_hits)
    serialized_evidence = json.dumps(
        [item.to_dict() for item in first.evidence], ensure_ascii=False
    )
    assert "section_heading" not in serialized_evidence
    assert "parent_heading" not in serialized_evidence
    assert "object_context" not in serialized_evidence
    assert "value_context" not in serialized_evidence


def test_context_complementarity_requires_an_independent_allowed_candidate() -> None:
    baseline = [("candidate-1", 1), ("candidate-2", 1)]

    assert _context_is_complementary(
        baseline,
        [("candidate-2", 1), ("candidate-3", 1)],
    ) is True
    assert _context_is_complementary(
        baseline,
        [("candidate-2", 1), ("candidate-1", 1)],
    ) is False


def test_doors_regression_keeps_non_complementary_exact_prefix_result(
    tmp_path: Path,
) -> None:
    db_path = _clean_set_database(tmp_path / "clean.sqlite")

    result = find_evidence(
        db_path,
        SearchIntent.from_dict(
            {
                "terms": ["selbstschließend"],
                "retrieval_path": "fts5",
                "max_results": 5,
            }
        ),
    )

    assert [item.candidate_id for item in result.evidence] == [
        "batch_001:candidate_001",
        "batch_001:candidate_001",
    ]
    assert result.trace.fusion["complementary"] is False
    assert result.trace.fusion["activated"] is False


def test_building_class_regression_keeps_baseline_hits_during_context_fusion(
    tmp_path: Path,
) -> None:
    db_path = _clean_set_database(tmp_path / "clean.sqlite")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE candidate_review_overlays "
            "SET reviewed_normalized_text = 'Gebäudeklasse 5' "
            "WHERE candidate_review_pk = 1"
        )
        connection.execute(
            "UPDATE candidate_review_overlays "
            "SET reviewed_normalized_text = 'Gebäudeklasse 4' "
            "WHERE candidate_review_pk = 2"
        )
        connection.execute(
            "UPDATE statements SET text = 'Abweichung begründet' "
            "WHERE statement_pk = 6"
        )
        connection.execute(
            "UPDATE documents "
            "SET title = 'Gebäudeklassen und Abweichung' WHERE document_pk = 1"
        )
    intent = SearchIntent.from_dict(
        {
            "terms": ["Gebäudeklasse 5", "Gebäudeklasse 4", "Abweichung"],
            "retrieval_path": "fts5",
            "max_results": 5,
        }
    )

    first = find_evidence(db_path, intent)
    second = find_evidence(db_path, intent)

    assert first.to_dict() == second.to_dict()
    assert first.trace.fusion["activated"] is True
    top_candidates = {item.candidate_id for item in first.evidence}
    assert {
        "batch_001:candidate_001",
        "batch_001:candidate_002",
        "batch_001:candidate_006",
    }.issubset(top_candidates)
    assert "batch_001:candidate_003" not in top_candidates


def test_search_intent_rejects_case_insensitive_duplicate_terms() -> None:
    with pytest.raises(SearchIntentError, match="terms contains duplicate values"):
        SearchIntent.from_dict(
            {
                "terms": ["rauchdichte geplant", "RAUCHDICHTE GEPLANT"],
                "retrieval_path": "fts5",
                "max_results": 5,
            }
        )


def test_fts5_projection_quotes_model_lexemes_instead_of_executing_operators(
    tmp_path: Path,
) -> None:
    db_path = _clean_set_database(tmp_path / "clean.sqlite")
    original_terms = ["rauchdicht OR geplant"]

    result = find_evidence(
        db_path,
        SearchIntent.from_dict(
            {
                "terms": original_terms,
                "retrieval_path": "fts5",
                "max_results": 5,
            }
        ),
    )

    assert result.evidence == []
    assert result.trace.search_intent["terms"] == original_terms
    assert result.trace.fts5_projection is not None
    assert result.trace.fts5_projection["effective_term_groups"] == [
        ["rauchdicht", "OR", "geplant"]
    ]
    assert result.trace.fts5_projection["match_expression"] == (
        '("rauchdicht" AND "OR" AND "geplant")'
    )


def test_hybrid_retrieval_includes_unreviewed_and_excludes_rejected_candidates(
    tmp_path: Path,
) -> None:
    db_path = _clean_set_database(tmp_path / "clean.sqlite")
    result = find_evidence(
        db_path,
        SearchIntent.from_dict(
            {
                "terms": ["rauchdicht"],
                "statement_types": ["requirement"],
                "retrieval_path": "hybrid",
                "max_results": 10,
            }
        ),
    )

    assert {item.candidate_id for item in result.evidence} == {
        "batch_001:candidate_001",
        "batch_001:candidate_004",
        "batch_001:candidate_006",
    }
    by_candidate = {item.candidate_id: item for item in result.evidence}
    assert by_candidate["batch_001:candidate_001"].review_status_label == (
        "fachlich_geprüft"
    )
    assert by_candidate["batch_001:candidate_001"].value_origin == "review_overlay"
    for candidate_id in ("batch_001:candidate_004", "batch_001:candidate_006"):
        assert by_candidate[candidate_id].review_status_label == (
            "automatisch_extrahiert_ungeprüft"
        )
        assert by_candidate[candidate_id].value_origin == "raw_candidate"
    assert "Verworfene Forderung" not in json.dumps(result.to_dict(), ensure_ascii=False)
    assert [iteration.method for iteration in result.trace.iterations] == [
        "sql+fts5_exact",
        "sql+fts5_prefix",
        "sql+fts5_context",
    ]


@pytest.mark.parametrize(
    ("candidate_review_pk", "review_status", "review_decision", "candidate_id"),
    (
        (3, "needs_review", "verwerfen", "batch_001:candidate_003"),
        (4, "rejected", "unklar", "batch_001:candidate_004"),
    ),
)
def test_current_rejected_status_or_verwerfen_decision_excludes_candidate(
    tmp_path: Path,
    candidate_review_pk: int,
    review_status: str,
    review_decision: str,
    candidate_id: str,
) -> None:
    db_path = _clean_set_database(tmp_path / "clean.sqlite")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE candidate_review_overlays
            SET review_status = ?, review_decision = ?,
                reviewed_by = 'reviewer_alpha', reviewed_at = '2026-08-10'
            WHERE candidate_review_pk = ?
            """,
            (review_status, review_decision, candidate_review_pk),
        )

    result = find_evidence(
        db_path,
        SearchIntent.from_dict(
            {
                "statement_types": ["requirement"],
                "retrieval_path": "structured",
                "max_results": 10,
            }
        ),
    )

    assert candidate_id not in {item.candidate_id for item in result.evidence}


def test_review_status_does_not_add_a_ranking_bonus(tmp_path: Path) -> None:
    db_path = _clean_set_database(tmp_path / "clean.sqlite")
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE statement_candidates_v3
            SET candidate_id = 'batch_001:candidate_900'
            WHERE statement_candidate_v3_pk = 1
            """
        )
        connection.execute(
            """
            UPDATE statement_candidates_v3
            SET candidate_id = 'batch_001:candidate_000'
            WHERE statement_candidate_v3_pk = 6
            """
        )

    result = find_evidence(
        db_path,
        SearchIntent.from_dict(
            {
                "statement_types": ["requirement"],
                "binding_statuses": ["binding"],
                "retrieval_path": "structured",
                "max_results": 10,
            }
        ),
    )

    first_rank_by_candidate: dict[str, int] = {}
    for item in result.evidence:
        first_rank_by_candidate.setdefault(item.candidate_id, item.rank)
    assert first_rank_by_candidate["batch_001:candidate_000"] < (
        first_rank_by_candidate["batch_001:candidate_900"]
    )
    by_candidate = {item.candidate_id: item for item in result.evidence}
    assert by_candidate["batch_001:candidate_000"].review_status == "needs_review"
    assert by_candidate["batch_001:candidate_900"].review_status == "confirmed"


def test_only_active_relations_are_returned_and_conflicts_are_marked(tmp_path: Path) -> None:
    db_path = _clean_set_database(tmp_path / "clean.sqlite")
    result = find_evidence(
        db_path,
        SearchIntent.from_dict(
            {
                "terms": ["Bauteil X-01"],
                "retrieval_path": "fts5",
                "max_results": 10,
            }
        ),
    )

    assert {relation.relation_id for relation in result.relations} == {
        "batch_001:relation_001",
        "batch_001:relation_003",
    }
    assert "batch_001:relation_002" not in json.dumps(result.to_dict())
    assert result.status == "conflicting_evidence"
    assert result.conflicting_evidence is True
    assert result.conflict_relation_ids == ["batch_001:relation_003"]


def test_rg_control_uses_fixed_argument_list_and_maps_only_clean_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = _clean_set_database(tmp_path / "clean.sqlite")
    text_dir = tmp_path / "text"
    text_dir.mkdir()
    artifact = text_dir / "doc-1.txt"
    artifact.write_text(
        "# document_id: doc-1\n\n"
        "[segment_id=doc-1:p1:s1 page=1 order=1]\n"
        "Türen müssen rauchdicht sein.\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr("info_system.operation.tool_retrieval.shutil.which", lambda _: "rg")

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        payload = {
            "type": "match",
            "data": {
                "path": {"text": str(artifact)},
                "lines": {"text": "Türen müssen rauchdicht sein.\n"},
                "line_number": 4,
            },
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    monkeypatch.setattr("info_system.operation.tool_retrieval.subprocess.run", fake_run)
    result = find_evidence(
        db_path,
        SearchIntent.from_dict(
            {
                "terms": ["Türen", "rauchdicht"],
                "retrieval_path": "fts5_with_rg_control",
                "max_results": 5,
            }
        ),
        tool_text_dir=text_dir,
        project_root=tmp_path,
    )

    assert captured["command"] == [
        "rg",
        "--fixed-strings",
        "--ignore-case",
        "--line-number",
        "--json",
        "--glob",
        "*.txt",
        "--",
        "Türen rauchdicht",
        str(text_dir.resolve()),
    ]
    assert captured["kwargs"]["shell"] is False
    assert result.trace.iteration_count == 2
    assert result.trace.iterations[1].method == "rg_control"
    assert result.trace.iterations[1].tool_names == ["rg"]
    assert result.evidence[0].retrieval_method == "fts5+rg_control"


def test_unanswerable_limit_and_read_only_invariants(tmp_path: Path) -> None:
    db_path = _clean_set_database(tmp_path / "clean.sqlite")
    before = hashlib.sha256(db_path.read_bytes()).hexdigest()
    result = find_evidence(
        db_path,
        SearchIntent.from_dict(
            {
                "terms": ["Quantenaufzug"],
                "retrieval_path": "fts5_then_rg_fallback",
                "max_results": 1,
            }
        ),
        tool_text_dir=tmp_path / "missing-text",
        project_root=tmp_path,
        prefer_rg=False,
    )

    assert result.evidence == []
    assert result.status == "insufficient_evidence"
    assert result.insufficient_evidence is True
    assert result.trace.iteration_count == 2
    assert len(result.evidence) <= 1
    assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before


def test_max_results_limits_evidence_and_trace_hits(tmp_path: Path) -> None:
    db_path = _clean_set_database(tmp_path / "clean.sqlite")
    result = find_evidence(
        db_path,
        SearchIntent.from_dict(
            {
                "terms": ["rauchdicht"],
                "retrieval_path": "fts5",
                "max_results": 1,
            }
        ),
    )

    assert len(result.evidence) == 1
    assert result.trace.iterations[0].evidence_hit_count == 1
    assert result.trace.iterations[0].ranks == [1]


def test_cli_accepts_intent_json_and_outputs_evidence_package(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_path = _clean_set_database(tmp_path / "clean.sqlite")
    intent = json.dumps(
        {
            "terms": ["rauchdicht"],
            "retrieval_path": "fts5",
            "max_results": 2,
        },
        ensure_ascii=False,
    )

    exit_code = main(["find-evidence", intent, "--db", str(db_path)])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "evidence_package_v2"
    assert payload["evidence"][0]["exact_evidence_text"] == "Türen müssen rauchdicht sein."
    assert payload["evidence"][0]["review_status_label"] == "fachlich_geprüft"
    assert payload["evidence"][0]["value_origin"] == "review_overlay"


def test_find_evidence_rejects_non_v4_database(tmp_path: Path) -> None:
    db_path = tmp_path / "old.sqlite"
    sqlite3.connect(db_path).close()
    with pytest.raises(EvidenceRetrievalError, match="schema version 4"):
        find_evidence(
            db_path,
            SearchIntent.from_dict(
                {"terms": ["Tür"], "retrieval_path": "fts5", "max_results": 5}
            ),
        )


def test_faceted_retrieval_runs_stage_one_queries_and_fuses_deterministically(
    tmp_path: Path,
) -> None:
    db_path = _clean_set_database(tmp_path / "clean.sqlite")
    intent = SearchIntent.from_dict(_faceted_intent_payload())

    first = find_evidence(db_path, intent)
    second = find_evidence(db_path, intent)

    assert first.to_dict() == second.to_dict()
    keys = [(item.candidate_id, item.passage_order) for item in first.evidence]
    assert len(keys) == len(set(keys))
    assert keys[0] == ("batch_001:candidate_005", 1)
    assert ("batch_001:candidate_001", 2) in keys
    assert all(item.review_status != "rejected" for item in first.evidence)
    for item in first.evidence:
        assert item.exact_evidence_text
        assert item.document_id == "doc-1"
        assert item.document_version_id == "doc-1:v1"
        assert item.locator
        assert item.review_status_label

    assert first.trace.search_intent["schema_version"] == "2.0"
    assert first.trace.fusion["facets"]["strategy"] == (
        "baseline_protected_capacity_interleaving"
    )
    assert first.trace.fusion["facets"]["max_new_hits_per_facet"] == 3
    assert first.trace.fusion["facets"]["facet_type_priority"] == [
        "planning_measure",
        "requirement",
        "project_finding",
        "object_property",
        "spatial_reference",
    ]
    assert first.trace.fusion["facets"]["negative_guard_activated"] is False
    assert len(first.trace.facet_runs) == 1
    facet_run = first.trace.facet_runs[0]
    assert facet_run.facet_type == "object_property"
    assert facet_run.terms == ("lichte Breite",)
    assert facet_run.executed_search_intent["terms"] == ["lichte Breite"]
    assert facet_run.coverage_status == "hits"
    assert facet_run.ranked_hits
    assert all(hit["component_rank"] > 0 for hit in facet_run.ranked_hits)
    assert any(hit["fusion_contribution"] > 0 for hit in facet_run.ranked_hits)
    assert first.facet_coverage[0].status == "hits"
    serialized_evidence = json.dumps(
        [item.to_dict() for item in first.evidence], ensure_ascii=False
    )
    assert "facet_type" not in serialized_evidence
    assert "coverage_status" not in serialized_evidence


def test_faceted_retrieval_falls_back_exactly_without_facets_or_facet_hits(
    tmp_path: Path,
) -> None:
    db_path = _clean_set_database(tmp_path / "clean.sqlite")
    legacy = SearchIntent.from_dict(
        {"terms": ["rauchdicht"], "retrieval_path": "fts5", "max_results": 5}
    )
    empty_facets = SearchIntent.from_dict(
        _faceted_intent_payload(
            query_text="Welche Türen sind rauchdicht?",
            terms=["rauchdicht"],
            facets=[],
        )
    )
    missing_facet = SearchIntent.from_dict(
        _faceted_intent_payload(
            query_text="Welche Türen sind rauchdicht und was gilt am Dach?",
            terms=["rauchdicht"],
            facets=[
                {
                    "facet_type": "spatial_reference",
                    "terms": ["Dach"],
                    "required": True,
                }
            ],
        )
    )

    legacy_result = find_evidence(db_path, legacy)
    empty_result = find_evidence(db_path, empty_facets)
    missing_result = find_evidence(db_path, missing_facet)

    assert [item.to_dict() for item in empty_result.evidence] == [
        item.to_dict() for item in legacy_result.evidence
    ]
    assert [item.to_dict() for item in missing_result.evidence] == [
        item.to_dict() for item in legacy_result.evidence
    ]
    assert missing_result.trace.fusion["facets"]["fallback_reason"] == (
        "no_independent_facet_hits"
    )
    assert missing_result.facet_coverage[0].status == "missing"
    assert missing_result.missing_required_facets == ["spatial_reference"]


def test_faceted_negative_guard_keeps_empty_stage_one_result(tmp_path: Path) -> None:
    db_path = _clean_set_database(tmp_path / "clean.sqlite")
    intent = SearchIntent.from_dict(
        _faceted_intent_payload(
            query_text="Welche Eigenschaften hat der Quantenaufzug?",
            terms=["Quantenaufzug"],
            facets=[
                {
                    "facet_type": "object_property",
                    "terms": ["Eigenschaften", "Quantenaufzug"],
                    "required": True,
                }
            ],
        )
    )

    result = find_evidence(db_path, intent)

    assert result.evidence == []
    assert result.trace.fusion["facets"]["negative_guard_activated"] is True
    assert result.trace.fusion["facets"]["fallback_reason"] == "baseline_empty"
    assert result.trace.facet_runs[0].coverage_status == "not_run"
    assert result.missing_required_facets == ["object_property"]


def _clean_set_database(db_path: Path) -> Path:
    initialize_database(db_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("INSERT INTO projects VALUES (1, 'project-1', 'Projekt', NULL, 'now')")
        connection.execute(
            """
            INSERT INTO documents (
                document_pk, document_id, project_pk, filename, file_type,
                source_path, imported_at, title
            ) VALUES (1, 'doc-1', 1, 'synthetic_plan.pdf', 'pdf',
                      'synthetic/synthetic_plan.pdf', 'now',
                      'Synthetische Planungsunterlage')
            """
        )
        connection.execute(
            """
            INSERT INTO document_versions (
                document_version_pk, document_version_id, document_pk,
                version_label, content_hash, is_current, created_at
            ) VALUES (1, 'doc-1:v1', 1, 'synthetic_profile_v1', 'hash', 1, 'now')
            """
        )
        texts = (
            "Türen müssen rauchdicht sein.",
            "Die lichte Breite beträgt 0,90 m.",
            "Für Bauteil X-01 ist eine rauchdichte Tür geplant.",
            "Verworfene Forderung ist rauchdicht.",
            "Ungeklärte Forderung ist rauchdicht.",
            "Bauteil X-01 bleibt offen.",
            "Nicht geprüfte Forderung ist rauchdicht.",
        )
        for index, text in enumerate(texts, start=1):
            connection.execute(
                """
                INSERT INTO document_nodes (
                    document_node_pk, document_node_id, document_version_pk,
                    node_path, node_rank, element_type, heading, locator,
                    page_start, page_end, raw_extracted_text, source_text,
                    search_text, is_citable, created_at
                ) VALUES (?, ?, 1, ?, ?, 'page_text', 'Türen', ?, ?, ?, ?, ?, ?, 1, 'now')
                """,
                (
                    index,
                    f"node-{index}",
                    f"/pages/{index}",
                    index,
                    f"page={index};section=doors",
                    index,
                    index,
                    text,
                    text,
                    text.casefold(),
                ),
            )
        connection.execute(
            """
            INSERT INTO extraction_runs (
                extraction_run_pk, extraction_run_id, document_id, started_at,
                method, status
            ) VALUES (1, 'run-1', 'doc-1', 'now', 'model', 'completed')
            """
        )
        connection.execute(
            """
            INSERT INTO statement_candidate_batches_v3 (
                statement_candidate_batch_v3_pk, batch_import_id, extraction_run_pk,
                document_version_pk, package_version, contract_version, prompt_version,
                batch_id, batch_number, total_batches, model_name,
                batch_manifest_sha256, input_sha256, prompt_sha256, batch_sha256,
                raw_output_sha256, binding_json, node_processing_json, raw_output_json,
                candidate_count, relation_count, created_at
            ) VALUES (
                1, 'batch-import', 1, 1, 'statement_candidates_batch_v3',
                'statement_candidates_batch_v3', 'statement_candidates_frontier_v3',
                'batch_001', 1, 1, 'model', ?, ?, ?, ?, ?, '{}', '[]', '{}', 6, 3, 'now'
            )
            """,
            tuple(letter * 64 for letter in "abcde"),
        )
        candidate_rows = (
            (1, "requirement", "Model requirement", "binding", 1),
            (2, "finding", "Model plan", "unknown", 3),
            (3, "requirement", "Model rejected", "binding", 4),
            (4, "requirement", "Model unclear", "binding", 5),
            (5, "finding", "Model conflict", "unknown", 6),
            (6, "requirement", "Model without review", "binding", 7),
        )
        for pk, statement_type, text, binding, node_pk in candidate_rows:
            candidate_id = f"batch_001:candidate_{pk:03d}"
            connection.execute(
                """
                INSERT INTO statements (
                    statement_pk, statement_id, statement_type, text, binding_status,
                    lifecycle_status, review_status, creation_method, created_at
                ) VALUES (?, ?, ?, ?, ?, 'active', 'needs_review', 'model_candidate', 'now')
                """,
                (pk, f"statement-{pk}", statement_type, text, binding),
            )
            connection.execute(
                """
                INSERT INTO statement_sources (
                    statement_source_id, statement_pk, document_node_pk,
                    source_role, created_at
                ) VALUES (?, ?, ?, 'primary', 'now')
                """,
                (f"source-{pk}", pk, node_pk),
            )
            connection.execute(
                """
                INSERT INTO statement_candidates_v3 (
                    statement_candidate_v3_pk, statement_candidate_v3_id,
                    statement_candidate_batch_v3_pk, candidate_id, candidate_sha256,
                    statement_pk, document_node_pk, evidence_source_text, source_role,
                    statement_subtype, candidate_json, review_notes_json,
                    validation_warnings_json, created_at
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, 'primary', 'test', '{}', '[]', '[]', 'now')
                """,
                (pk, f"candidate-v3-{pk}", candidate_id, str(pk) * 64, pk, node_pk, texts[node_pk - 1]),
            )
        reviews = (
            (1, "korrigieren", "requirement", "Türen müssen rauchdicht und selbstschließend sein.", "binding", "confirmed"),
            (2, "korrigieren", "planning_measure", "Für Bauteil X-01 ist eine rauchdichte Tür geplant.", "unknown", "confirmed"),
            (3, "verwerfen", "requirement", "Verworfene Forderung ist rauchdicht.", "binding", "rejected"),
            (4, "unklar", "requirement", "Ungeklärte Forderung ist rauchdicht.", "binding", "needs_review"),
            (5, "akzeptieren", "finding", "Bauteil X-01 bleibt offen.", "unknown", "confirmed"),
        )
        for pk, decision, statement_type, normalized, binding, status in reviews:
            reviewed_by = None if status == "needs_review" else "reviewer_alpha"
            reviewed_at = None if status == "needs_review" else "2026-08-10"
            connection.execute(
                """
                INSERT INTO candidate_review_overlays (
                    candidate_review_pk, candidate_review_id, statement_candidate_v3_pk,
                    review_decision, original_review_decision, reviewed_statement_type,
                    reviewed_normalized_text, reviewed_binding_status, review_status,
                    review_source, review_version, reviewed_by, reviewed_at,
                    is_current, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'synthetic.csv', 'v1', ?, ?, 1, 'now')
                """,
                (
                    pk,
                    f"review-{pk}",
                    pk,
                    decision,
                    decision,
                    statement_type,
                    normalized,
                    binding,
                    status,
                    reviewed_by,
                    reviewed_at,
                ),
            )
        evidence_rows = (
            (1, 1, 1, 1, texts[0], "primary"),
            (2, 1, 2, 2, texts[1], "supporting"),
            (3, 2, 1, 3, texts[2], "primary"),
            (4, 3, 1, 4, texts[3], "primary"),
            (5, 4, 1, 5, texts[4], "primary"),
            (6, 5, 1, 6, texts[5], "primary"),
        )
        for pk, review_pk, order, node_pk, exact_text, role in evidence_rows:
            connection.execute(
                """
                INSERT INTO candidate_review_evidence_passages (
                    candidate_review_evidence_passage_pk,
                    candidate_review_evidence_passage_id, candidate_review_pk,
                    passage_order, document_pk, document_node_pk, locator,
                    exact_text, char_start_in_node, char_end_in_node, source_role,
                    source_candidate_v3_pk, created_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, 0, ?, ?, ?, 'now')
                """,
                (
                    pk,
                    f"evidence-{pk}",
                    review_pk,
                    order,
                    node_pk,
                    f"page={node_pk};section=doors",
                    exact_text,
                    len(exact_text),
                    role,
                    review_pk,
                ),
            )
        connection.execute(
            "INSERT INTO entities VALUES (1, 'entity-component-x01', "
            "'door', 'Bauteil X-01', 'now')"
        )
        connection.execute(
            "INSERT INTO statement_entities VALUES (1, 'statement-entity-1', 1, 1, 'subject', 'now')"
        )
        relations = (
            (1, 2, 1, "addresses", "active"),
            (2, 5, 1, "addresses", "inactive_catalog_conflict"),
            (3, 2, 5, "conflicts_with", "active"),
        )
        for pk, source_pk, target_pk, relation_type, activation in relations:
            connection.execute(
                """
                INSERT INTO statement_relations (
                    statement_relation_pk, statement_relation_id,
                    source_statement_pk, target_statement_pk, relation_type, created_at
                ) VALUES (?, ?, ?, ?, ?, 'now')
                """,
                (pk, f"statement-relation-{pk}", source_pk, target_pk, relation_type),
            )
            connection.execute(
                """
                INSERT INTO statement_relations_v3 (
                    statement_relation_v3_pk, statement_relation_v3_id,
                    statement_candidate_batch_v3_pk, relation_id, relation_sha256,
                    statement_relation_pk, source_candidate_v3_pk,
                    target_candidate_v3_pk, relation_type, relation_json, created_at
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, '{}', 'now')
                """,
                (
                    pk,
                    f"relation-v3-{pk}",
                    f"batch_001:relation_{pk:03d}",
                    (str(pk + 5) * 64)[:64],
                    pk,
                    source_pk,
                    target_pk,
                    relation_type,
                ),
            )
            reason = None if activation == "active" else "synthetic catalog conflict"
            connection.execute(
                """
                INSERT INTO relation_review_overlays (
                    relation_review_pk, relation_review_id, statement_relation_v3_pk,
                    professional_decision, original_review_code, acceptance_mode,
                    technical_activation_status, non_activation_reason,
                    review_source, review_version, reviewed_by, reviewed_at,
                    is_current, created_at
                ) VALUES (?, ?, ?, 'akzeptieren', 'RELATION_OK', 'immediate', ?, ?,
                          'synthetic.csv', 'v1', 'reviewer_alpha', '2026-08-10', 1, 'now')
                """,
                (pk, f"relation-review-{pk}", pk, activation, reason),
            )
    return db_path
