from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

from info_system.operation.tool_retrieval import ToolSearchError, ToolSearchRunner


ALLOWED_STATEMENT_TYPES = (
    "requirement",
    "planning_measure",
    "decision",
    "finding",
)
ALLOWED_BINDING_STATUSES = ("binding", "unknown")
ALLOWED_DOCUMENT_TYPES = ("pdf",)
ALLOWED_ENTITY_TYPES = (
    "building",
    "building_component",
    "door",
    "project_metric",
    "space",
    "storey",
    "use_unit",
)
ALLOWED_ENTITY_ROLES = ("object", "reference", "scope", "subject")
ALLOWED_RETRIEVAL_PATHS = (
    "structured",
    "fts5",
    "hybrid",
    "fts5_with_rg_control",
    "fts5_then_rg_fallback",
)
MAX_TERMS = 8
MAX_TERM_LENGTH = 64
MAX_RESULTS = 20
MAX_SEARCH_ITERATIONS = 3
MIN_FTS5_PREFIX_LENGTH = 5
RECIPROCAL_RANK_FUSION_K = 60
ALLOWED_SEARCH_INTENT_VERSIONS = ("1.0", "2.0")
ALLOWED_FACET_TYPES = (
    "requirement",
    "project_finding",
    "planning_measure",
    "object_property",
    "spatial_reference",
)
MAX_FACETS = 3
MAX_FACET_TERMS = 4
MAX_NEW_HITS_PER_FACET = 3
FACET_TYPE_PRIORITY = (
    "planning_measure",
    "requirement",
    "project_finding",
    "object_property",
    "spatial_reference",
)
CONTEXT_COMPLEMENTARITY_CRITERION = (
    "context top-N contains an allowed candidate-passage key absent from baseline top-N"
)
CONTEXT_FIELD_WEIGHTS = {
    "normalized_statement": 5.0,
    "original_passage": 4.0,
    "section_heading": 3.0,
    "parent_heading": 2.0,
    "document_title": 1.0,
    "document_type": 0.5,
    "object_context": 2.0,
    "value_context": 2.0,
}
_CONTEXT_SOURCE_COLUMNS = {
    "normalized_statement": "statements.text/review overlay",
    "original_passage": "candidate/review evidence passage",
    "section_heading": "document_nodes.heading",
    "parent_heading": "parent document_nodes.heading",
    "document_title": "documents.title/filename",
    "document_type": "documents.file_type",
    "object_context": "statement_entities/entities/entity_aliases",
    "value_context": "statement_values",
}

_INTENT_FIELDS = {
    "schema_version",
    "query_text",
    "facets",
    "terms",
    "statement_types",
    "binding_statuses",
    "document_ids",
    "document_types",
    "object_features",
    "retrieval_path",
    "max_results",
}
_OBJECT_FIELDS = {"entity_type", "entity_label", "entity_role"}
_FACET_FIELDS = {"facet_type", "terms", "statement_types", "required"}
_UNSAFE_TEXT = re.compile(r"[\x00-\x1f'\"`;|&<>$(){}\[\]\\]")
_FACET_FORBIDDEN_TEXT = re.compile(
    r"\b(?:select|insert|update|delete|drop|alter|pragma|attach|detach|union|"
    r"find_evidence|powershell|cmd|bash|python|grep|rg)\b",
    flags=re.IGNORECASE,
)
_FACET_GENERIC_TOKENS = {
    "anforderung",
    "anforderungen",
    "ausfuehrung",
    "ausfuehrungen",
    "bezug",
    "eigenschaft",
    "eigenschaften",
    "feststellung",
    "feststellungen",
    "massnahme",
    "massnahmen",
    "objekt",
    "objekte",
    "planung",
    "planungsmassnahme",
    "planungsmassnahmen",
    "projektfeststellung",
    "projektfeststellungen",
    "raeumlich",
    "raeumliche",
    "raeumlichen",
}
_FACET_STOP_TOKENS = {
    "als",
    "an",
    "auf",
    "aus",
    "bei",
    "das",
    "dem",
    "den",
    "der",
    "des",
    "die",
    "ein",
    "eine",
    "einem",
    "einen",
    "einer",
    "eines",
    "fuer",
    "gelten",
    "gilt",
    "im",
    "in",
    "ist",
    "mit",
    "nach",
    "oder",
    "sind",
    "und",
    "von",
    "was",
    "welche",
    "welcher",
    "welches",
    "werden",
    "wie",
    "wird",
    "zu",
    "zum",
    "zur",
}
_IDENTIFIER = re.compile(r"^[\w][\w:.-]{0,127}$", flags=re.UNICODE)
_GERMAN_QUERY_TRANSLITERATION = str.maketrans(
    {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "Ä": "Ae",
        "Ö": "Oe",
        "Ü": "Ue",
        "ß": "ss",
    }
)


class SearchIntentError(ValueError):
    """Raised when an untrusted SearchIntent violates the fixed contract."""


class EvidenceRetrievalError(RuntimeError):
    """Raised when controlled retrieval cannot be executed safely."""


@dataclass(frozen=True)
class ObjectFeatures:
    entity_type: str | None = None
    entity_label: str | None = None
    entity_role: str | None = None

    @classmethod
    def from_dict(cls, value: object) -> ObjectFeatures:
        if not isinstance(value, Mapping) or not value:
            raise SearchIntentError("object_features must be a non-empty object")
        unknown = set(value) - _OBJECT_FIELDS
        if unknown:
            raise SearchIntentError(f"unknown object_features fields: {sorted(unknown)}")
        entity_type = _optional_allowed_string(
            value, "entity_type", ALLOWED_ENTITY_TYPES
        )
        entity_role = _optional_allowed_string(
            value, "entity_role", ALLOWED_ENTITY_ROLES
        )
        entity_label = _optional_safe_text(value, "entity_label", max_length=128)
        if entity_type is None and entity_role is None and entity_label is None:
            raise SearchIntentError("object_features must contain an allowed filter")
        return cls(
            entity_type=entity_type,
            entity_label=entity_label,
            entity_role=entity_role,
        )

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


@dataclass(frozen=True)
class SearchFacet:
    facet_type: str
    terms: tuple[str, ...]
    statement_types: tuple[str, ...] = ()
    required: bool = False

    @classmethod
    def from_dict(cls, value: object, *, query_text: str) -> SearchFacet:
        if not isinstance(value, Mapping) or not value:
            raise SearchIntentError("facet must be a non-empty object")
        unknown = set(value) - _FACET_FIELDS
        if unknown:
            raise SearchIntentError(f"unknown facet fields: {sorted(unknown)}")
        facet_type = _required_allowed_string(
            value, "facet_type", ALLOWED_FACET_TYPES
        )
        terms = _safe_text_list(
            value,
            "terms",
            max_items=MAX_FACET_TERMS,
            max_length=MAX_TERM_LENGTH,
        )
        statement_types = _allowed_list(
            value, "statement_types", ALLOWED_STATEMENT_TYPES
        )
        required = value.get("required", False)
        if not isinstance(required, bool):
            raise SearchIntentError("facet required must be a boolean")
        _validate_facet_terms(terms, query_text=query_text)
        return cls(
            facet_type=facet_type,
            terms=terms,
            statement_types=statement_types,
            required=required,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "facet_type": self.facet_type,
            "terms": list(self.terms),
            "statement_types": list(self.statement_types),
            "required": self.required,
        }


@dataclass(frozen=True)
class SearchIntent:
    terms: tuple[str, ...]
    statement_types: tuple[str, ...]
    binding_statuses: tuple[str, ...]
    document_ids: tuple[str, ...]
    document_types: tuple[str, ...]
    object_features: ObjectFeatures | None
    retrieval_path: str
    max_results: int
    schema_version: str = "1.0"
    query_text: str | None = None
    facets: tuple[SearchFacet, ...] = ()

    @classmethod
    def from_dict(cls, value: object) -> SearchIntent:
        if not isinstance(value, Mapping) or not value:
            raise SearchIntentError("SearchIntent must be a non-empty object")
        unknown = set(value) - _INTENT_FIELDS
        if unknown:
            raise SearchIntentError(f"unknown SearchIntent fields: {sorted(unknown)}")
        if "retrieval_path" not in value or "max_results" not in value:
            raise SearchIntentError("retrieval_path and max_results are required")

        schema_version = value.get("schema_version", "1.0")
        if (
            not isinstance(schema_version, str)
            or schema_version not in ALLOWED_SEARCH_INTENT_VERSIONS
        ):
            raise SearchIntentError("schema_version contains an unsupported value")
        query_text = _optional_question_text(value, "query_text")
        facets = _facets_from_value(
            value,
            schema_version=schema_version,
            query_text=query_text,
        )

        terms = _safe_text_list(
            value,
            "terms",
            max_items=MAX_TERMS,
            max_length=MAX_TERM_LENGTH,
        )
        statement_types = _allowed_list(
            value, "statement_types", ALLOWED_STATEMENT_TYPES
        )
        binding_statuses = _allowed_list(
            value, "binding_statuses", ALLOWED_BINDING_STATUSES
        )
        document_types = _allowed_list(
            value, "document_types", ALLOWED_DOCUMENT_TYPES
        )
        document_ids = _identifier_list(value, "document_ids")
        object_features = (
            ObjectFeatures.from_dict(value["object_features"])
            if "object_features" in value
            else None
        )
        retrieval_path = _required_allowed_string(
            value, "retrieval_path", ALLOWED_RETRIEVAL_PATHS
        )
        max_results = value["max_results"]
        if isinstance(max_results, bool) or not isinstance(max_results, int):
            raise SearchIntentError("max_results must be an integer")
        if max_results < 1 or max_results > MAX_RESULTS:
            raise SearchIntentError(f"max_results must be between 1 and {MAX_RESULTS}")

        has_structured = any(
            (statement_types, binding_statuses, document_ids, document_types)
        ) or object_features is not None
        if not terms and not has_structured:
            raise SearchIntentError("SearchIntent must contain terms or a structured filter")
        if retrieval_path == "structured" and terms:
            raise SearchIntentError("structured retrieval does not accept lexical terms")
        if retrieval_path == "structured" and not has_structured:
            raise SearchIntentError("structured retrieval requires a structured filter")
        if retrieval_path == "fts5" and (not terms or has_structured):
            raise SearchIntentError("fts5 retrieval requires terms and no structured filter")
        if retrieval_path == "hybrid" and (not terms or not has_structured):
            raise SearchIntentError("hybrid retrieval requires terms and a structured filter")
        if retrieval_path.startswith("fts5_") and not terms:
            raise SearchIntentError(f"{retrieval_path} requires lexical terms")

        return cls(
            terms=terms,
            statement_types=statement_types,
            binding_statuses=binding_statuses,
            document_ids=document_ids,
            document_types=document_types,
            object_features=object_features,
            retrieval_path=retrieval_path,
            max_results=max_results,
            schema_version=schema_version,
            query_text=query_text,
            facets=facets,
        )

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "schema_version": self.schema_version,
            "terms": list(self.terms),
            "statement_types": list(self.statement_types),
            "binding_statuses": list(self.binding_statuses),
            "document_ids": list(self.document_ids),
            "document_types": list(self.document_types),
            "object_features": (
                self.object_features.to_dict()
                if self.object_features is not None
                else None
            ),
            "retrieval_path": self.retrieval_path,
            "max_results": self.max_results,
        }
        if self.query_text is not None:
            value["query_text"] = self.query_text
        if self.facets or self.schema_version == "2.0":
            value["facets"] = [facet.to_dict() for facet in self.facets]
        return value


@dataclass(frozen=True)
class EvidenceItem:
    candidate_id: str
    statement_id: str
    passage_order: int
    exact_evidence_text: str
    document_id: str
    document_label: str
    filename: str
    document_type: str
    document_version_id: str
    document_version_label: str | None
    document_node_id: str
    locator: str | None
    page_start: int | None
    page_end: int | None
    char_start_in_node: int | None
    char_end_in_node: int | None
    source_role: str
    statement_type: str
    binding_status: str
    normalized_statement: str
    review_status: str
    review_status_label: str
    value_origin: str
    retrieval_method: str
    rank: int
    score: float | None
    active_relation_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["active_relation_ids"] = list(self.active_relation_ids)
        return value


@dataclass(frozen=True)
class ActiveRelation:
    relation_id: str
    relation_type: str
    source_candidate_id: str
    target_candidate_id: str
    acceptance_mode: str
    technical_activation_status: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class TraceIteration:
    iteration: int
    method: str
    parameters: dict[str, object]
    candidate_hit_count: int
    evidence_hit_count: int
    candidate_ids: list[str]
    ranks: list[int]
    locators: list[str]
    tool_names: list[str]
    ranked_hits: list[dict[str, object]]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class FacetRetrievalTrace:
    facet_type: str
    required: bool
    terms: tuple[str, ...]
    statement_types: tuple[str, ...]
    executed_search_intent: dict[str, object] | None
    coverage_status: str
    ranked_hits: tuple[dict[str, object], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "facet_type": self.facet_type,
            "required": self.required,
            "terms": list(self.terms),
            "statement_types": list(self.statement_types),
            "executed_search_intent": self.executed_search_intent,
            "coverage_status": self.coverage_status,
            "ranked_hits": list(self.ranked_hits),
        }


@dataclass(frozen=True)
class FacetCoverage:
    facet_type: str
    required: bool
    status: str
    hit_count: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RetrievalTrace:
    search_intent: dict[str, object]
    iterations: tuple[TraceIteration, ...]
    fts5_projection: dict[str, object] | None
    fusion: dict[str, object] | None = None
    persistent_logging: bool = False
    facet_runs: tuple[FacetRetrievalTrace, ...] = ()

    @property
    def iteration_count(self) -> int:
        return len(self.iterations)

    def to_dict(self) -> dict[str, object]:
        return {
            "search_intent": self.search_intent,
            "iteration_count": self.iteration_count,
            "iterations": [iteration.to_dict() for iteration in self.iterations],
            "fts5_projection": self.fts5_projection,
            "fusion": self.fusion,
            "persistent_logging": self.persistent_logging,
            "facet_runs": [run.to_dict() for run in self.facet_runs],
        }


@dataclass(frozen=True)
class _RankedVariant:
    method: str
    match_expression: str
    rows: list[sqlite3.Row]
    scores: dict[str, float]
    fusion_contributions: dict[tuple[str, int], float]


@dataclass(frozen=True)
class _PrimaryRetrievalResult:
    rows: list[sqlite3.Row]
    scores: dict[str, float]
    projection: dict[str, object] | None
    method: str
    methods: dict[tuple[str, int], str]
    variants: list[_RankedVariant]
    fusion: dict[str, object] | None


@dataclass(frozen=True)
class EvidencePackage:
    schema_version: str
    status: str
    insufficient_evidence: bool
    conflicting_evidence: bool
    conflict_relation_ids: list[str]
    evidence: list[EvidenceItem]
    relations: list[ActiveRelation]
    trace: RetrievalTrace
    assessment_boundary: str = (
        "retrieval_only; no compliance, conformity, legal, or fulfilment assessment"
    )
    facet_coverage: tuple[FacetCoverage, ...] = ()
    missing_required_facets: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "insufficient_evidence": self.insufficient_evidence,
            "conflicting_evidence": self.conflicting_evidence,
            "conflict_relation_ids": self.conflict_relation_ids,
            "evidence": [item.to_dict() for item in self.evidence],
            "relations": [relation.to_dict() for relation in self.relations],
            "trace": self.trace.to_dict(),
            "assessment_boundary": self.assessment_boundary,
            "facet_coverage": [item.to_dict() for item in self.facet_coverage],
            "missing_required_facets": list(self.missing_required_facets),
        }


def find_evidence(
    db_path: str | Path,
    search_intent: SearchIntent,
    *,
    tool_text_dir: str | Path | None = None,
    project_root: str | Path | None = None,
    prefer_rg: bool = True,
) -> EvidencePackage:
    """Retrieve evidence, optionally orchestrating validated question facets."""
    if not isinstance(search_intent, SearchIntent):
        raise EvidenceRetrievalError("search_intent must be a validated SearchIntent")
    if not search_intent.facets:
        return _find_evidence_single(
            db_path,
            search_intent,
            tool_text_dir=tool_text_dir,
            project_root=project_root,
            prefer_rg=prefer_rg,
        )
    return _find_evidence_faceted(
        db_path,
        search_intent,
        tool_text_dir=tool_text_dir,
        project_root=project_root,
        prefer_rg=prefer_rg,
    )


def _find_evidence_single(
    db_path: str | Path,
    search_intent: SearchIntent,
    *,
    tool_text_dir: str | Path | None = None,
    project_root: str | Path | None = None,
    prefer_rg: bool = True,
) -> EvidencePackage:
    """Retrieve a deterministic package from the inclusive schema-v4 evidence set."""
    if not isinstance(search_intent, SearchIntent):
        raise EvidenceRetrievalError("search_intent must be a validated SearchIntent")
    path = Path(db_path).resolve()
    if not path.is_file():
        raise EvidenceRetrievalError(f"database does not exist: {path}")

    try:
        with _connect_read_only(path) as connection:
            _validate_clean_set_database(connection)
            structured_rows = _structured_rows(connection, search_intent)
            primary = _primary_retrieval(
                connection,
                structured_rows=structured_rows,
                intent=search_intent,
            )
            selected_rows = primary.rows
            scores = primary.scores
            projection = primary.projection
            first_method = primary.method
            ranked_variants = primary.variants
            selected_rows = selected_rows[: search_intent.max_results]
            if ranked_variants and search_intent.retrieval_path in {"fts5", "hybrid"}:
                final_ranks = {
                    _row_key(row): rank
                    for rank, row in enumerate(selected_rows, start=1)
                }
                iterations = [
                    _trace_iteration(
                        iteration=index,
                        method=variant.method,
                        parameters={
                            **_trace_parameters(search_intent),
                            "match_expression": variant.match_expression,
                        },
                        rows=variant.rows[: search_intent.max_results],
                        scores=variant.scores,
                        fusion_contributions=variant.fusion_contributions,
                        final_ranks=final_ranks,
                    )
                    for index, variant in enumerate(ranked_variants, start=1)
                ]
            else:
                iterations = [
                    _trace_iteration(
                        iteration=1,
                        method=first_method,
                        parameters=_trace_parameters(search_intent),
                        rows=selected_rows,
                        scores=scores,
                        final_ranks={
                            _row_key(row): rank
                            for rank, row in enumerate(selected_rows, start=1)
                        },
                    )
                ]
            methods = {
                _row_key(row): primary.methods.get(_row_key(row), first_method)
                for row in selected_rows
            }

            if search_intent.retrieval_path in {
                "fts5_with_rg_control",
                "fts5_then_rg_fallback",
            }:
                use_tool = (
                    search_intent.retrieval_path == "fts5_with_rg_control"
                    or not selected_rows
                )
                if use_tool:
                    tool_hits, mapped_keys = _tool_iteration(
                        structured_rows,
                        intent=search_intent,
                        tool_text_dir=tool_text_dir,
                        project_root=project_root,
                        prefer_rg=prefer_rg,
                    )
                    if not selected_rows:
                        selected_rows = [
                            row for row in structured_rows if _row_key(row) in mapped_keys
                        ]
                        selected_rows = _rank_rows(selected_rows, scores)[
                            : search_intent.max_results
                        ]
                        fallback_method = (
                            "sql+rg_fallback"
                            if _has_structured_filters(search_intent)
                            else "rg_fallback"
                        )
                        methods.update(
                            {_row_key(row): fallback_method for row in selected_rows}
                        )
                    else:
                        for row in selected_rows:
                            if _row_key(row) in mapped_keys:
                                methods[_row_key(row)] = (
                                    f"{methods[_row_key(row)]}+rg_control"
                                )
                    iterations.append(
                        _tool_trace_iteration(
                            iteration=2,
                            mode=(
                                "rg_control"
                                if search_intent.retrieval_path == "fts5_with_rg_control"
                                else "rg_fallback"
                            ),
                            hits=tool_hits,
                            rows=selected_rows,
                            mapped_keys=mapped_keys,
                            pattern=" ".join(search_intent.terms),
                            max_results=search_intent.max_results,
                        )
                    )

            selected_rows = selected_rows[: search_intent.max_results]
            candidate_ids = sorted({row["candidate_id"] for row in selected_rows})
            relations = _active_relations(connection, candidate_ids)
    except (sqlite3.Error, ToolSearchError) as error:
        raise EvidenceRetrievalError(str(error)) from error

    relation_ids_by_candidate: dict[str, list[str]] = {}
    for relation in relations:
        relation_ids_by_candidate.setdefault(relation.source_candidate_id, []).append(
            relation.relation_id
        )
        relation_ids_by_candidate.setdefault(relation.target_candidate_id, []).append(
            relation.relation_id
        )
    evidence = [
        _evidence_item(
            row,
            rank=rank,
            score=scores.get(row["candidate_id"]),
            method=methods.get(_row_key(row), first_method),
            relation_ids=tuple(
                sorted(relation_ids_by_candidate.get(row["candidate_id"], []))
            ),
        )
        for rank, row in enumerate(selected_rows, start=1)
    ]
    returned_candidates = {item.candidate_id for item in evidence}
    conflict_ids = sorted(
        relation.relation_id
        for relation in relations
        if relation.relation_type == "conflicts_with"
        and relation.source_candidate_id in returned_candidates
        and relation.target_candidate_id in returned_candidates
    )
    status = (
        "insufficient_evidence"
        if not evidence
        else "conflicting_evidence"
        if conflict_ids
        else "sufficient"
    )
    trace = RetrievalTrace(
        search_intent=search_intent.to_dict(),
        iterations=tuple(iterations[:MAX_SEARCH_ITERATIONS]),
        fts5_projection=projection,
        fusion=primary.fusion,
    )
    return EvidencePackage(
        schema_version="evidence_package_v2",
        status=status,
        insufficient_evidence=not evidence,
        conflicting_evidence=bool(conflict_ids),
        conflict_relation_ids=conflict_ids,
        evidence=evidence,
        relations=relations,
        trace=trace,
    )


def _find_evidence_faceted(
    db_path: str | Path,
    search_intent: SearchIntent,
    *,
    tool_text_dir: str | Path | None,
    project_root: str | Path | None,
    prefer_rg: bool,
) -> EvidencePackage:
    baseline_intent = replace(search_intent, facets=())
    baseline = _find_evidence_single(
        db_path,
        baseline_intent,
        tool_text_dir=tool_text_dir,
        project_root=project_root,
        prefer_rg=prefer_rg,
    )
    if not baseline.evidence:
        facet_runs = tuple(
            FacetRetrievalTrace(
                facet_type=facet.facet_type,
                required=facet.required,
                terms=facet.terms,
                statement_types=facet.statement_types,
                executed_search_intent=None,
                coverage_status="not_run",
            )
            for facet in search_intent.facets
        )
        coverage = tuple(
            FacetCoverage(
                facet_type=facet.facet_type,
                required=facet.required,
                status="not_run",
                hit_count=0,
            )
            for facet in search_intent.facets
        )
        return _faceted_fallback_package(
            baseline,
            search_intent=search_intent,
            facet_runs=facet_runs,
            coverage=coverage,
            fallback_reason="baseline_empty",
            negative_guard_activated=True,
        )

    facet_results: list[tuple[SearchFacet, SearchIntent, EvidencePackage]] = []
    for facet in search_intent.facets:
        facet_intent = _facet_search_intent(search_intent, facet)
        facet_results.append(
            (
                facet,
                facet_intent,
                _find_evidence_single(
                    db_path,
                    facet_intent,
                    tool_text_dir=tool_text_dir,
                    project_root=project_root,
                    prefer_rg=prefer_rg,
                ),
            )
        )

    baseline_keys = {_evidence_key(item) for item in baseline.evidence}
    independent_keys = {
        _evidence_key(item)
        for _, _, package in facet_results
        for item in package.evidence
        if _evidence_key(item) not in baseline_keys
    }
    coverage = tuple(
        FacetCoverage(
            facet_type=facet.facet_type,
            required=facet.required,
            status="hits" if package.evidence else "missing",
            hit_count=len(package.evidence),
        )
        for facet, _, package in facet_results
    )
    if not independent_keys:
        facet_runs = _facet_retrieval_traces(
            facet_results,
            final_ranks={},
            contributions={},
        )
        return _faceted_fallback_package(
            baseline,
            search_intent=search_intent,
            facet_runs=facet_runs,
            coverage=coverage,
            fallback_reason="no_independent_facet_hits",
            negative_guard_activated=False,
        )

    evidence, contributions = _baseline_protected_facet_interleave(
        baseline.evidence,
        [(facet, package.evidence) for facet, _, package in facet_results],
        max_results=search_intent.max_results,
    )
    if len(evidence) == len(baseline.evidence):
        facet_runs = _facet_retrieval_traces(
            facet_results,
            final_ranks={
                _evidence_key(item): item.rank for item in baseline.evidence
            },
            contributions=contributions,
        )
        return _faceted_fallback_package(
            baseline,
            search_intent=search_intent,
            facet_runs=facet_runs,
            coverage=coverage,
            fallback_reason="baseline_capacity_full",
            negative_guard_activated=False,
        )
    final_ranks = {
        _evidence_key(item): item.rank for item in evidence
    }
    facet_runs = _facet_retrieval_traces(
        facet_results,
        final_ranks=final_ranks,
        contributions=contributions,
    )
    relations_by_id = {
        relation.relation_id: relation
        for package in [baseline, *(item[2] for item in facet_results)]
        for relation in package.relations
    }
    returned_candidates = {item.candidate_id for item in evidence}
    relations = sorted(relations_by_id.values(), key=lambda item: item.relation_id)
    conflict_ids = sorted(
        relation.relation_id
        for relation in relations
        if relation.relation_type == "conflicts_with"
        and relation.source_candidate_id in returned_candidates
        and relation.target_candidate_id in returned_candidates
    )
    missing_required = [
        item.facet_type
        for item in coverage
        if item.required and item.status != "hits"
    ]
    return EvidencePackage(
        schema_version=baseline.schema_version,
        status="conflicting_evidence" if conflict_ids else "sufficient",
        insufficient_evidence=False,
        conflicting_evidence=bool(conflict_ids),
        conflict_relation_ids=conflict_ids,
        evidence=evidence,
        relations=relations,
        trace=RetrievalTrace(
            search_intent=search_intent.to_dict(),
            iterations=baseline.trace.iterations,
            fts5_projection=baseline.trace.fts5_projection,
            fusion=_facet_fusion_trace(
                baseline.trace.fusion,
                activated=True,
                fallback_reason=None,
                negative_guard_activated=False,
            ),
            persistent_logging=baseline.trace.persistent_logging,
            facet_runs=facet_runs,
        ),
        assessment_boundary=baseline.assessment_boundary,
        facet_coverage=coverage,
        missing_required_facets=missing_required,
    )


def _facet_search_intent(parent: SearchIntent, facet: SearchFacet) -> SearchIntent:
    statement_types = facet.statement_types or parent.statement_types
    has_structured = any(
        (
            statement_types,
            parent.binding_statuses,
            parent.document_ids,
            parent.document_types,
        )
    ) or parent.object_features is not None
    return SearchIntent(
        terms=facet.terms,
        statement_types=statement_types,
        binding_statuses=parent.binding_statuses,
        document_ids=parent.document_ids,
        document_types=parent.document_types,
        object_features=parent.object_features,
        retrieval_path="hybrid" if has_structured else "fts5",
        max_results=parent.max_results,
    )


def _baseline_protected_facet_interleave(
    baseline: list[EvidenceItem],
    facet_lists: list[tuple[SearchFacet, list[EvidenceItem]]],
    *,
    max_results: int,
) -> tuple[
    list[EvidenceItem],
    dict[tuple[int, tuple[str, int]], float],
]:
    evidence = list(baseline)
    seen = {_evidence_key(item) for item in baseline}
    contributions: dict[tuple[int, tuple[str, int]], float] = {}
    priority = {value: index for index, value in enumerate(FACET_TYPE_PRIORITY)}
    ordered_indexes = sorted(
        range(len(facet_lists)),
        key=lambda index: (
            priority[facet_lists[index][0].facet_type],
            index,
        ),
    )
    for facet_index in ordered_indexes:
        _, items = facet_lists[facet_index]
        added = 0
        for component_rank, item in enumerate(items, start=1):
            key = _evidence_key(item)
            if key in seen:
                continue
            contribution = 1.0 / (
                RECIPROCAL_RANK_FUSION_K + component_rank
            )
            contributions[(facet_index + 1, key)] = contribution
            evidence.append(
                replace(
                    item,
                    retrieval_method=f"{item.retrieval_method}+facet_interleave",
                    rank=len(evidence) + 1,
                    score=contribution,
                )
            )
            seen.add(key)
            added += 1
            if (
                added >= MAX_NEW_HITS_PER_FACET
                or len(evidence) >= max_results
            ):
                break
        if len(evidence) >= max_results:
            break
    return evidence, contributions


def _facet_retrieval_traces(
    facet_results: list[tuple[SearchFacet, SearchIntent, EvidencePackage]],
    *,
    final_ranks: Mapping[tuple[str, int], int],
    contributions: Mapping[tuple[int, tuple[str, int]], float],
) -> tuple[FacetRetrievalTrace, ...]:
    traces: list[FacetRetrievalTrace] = []
    for list_index, (facet, intent, package) in enumerate(facet_results, start=1):
        traces.append(
            FacetRetrievalTrace(
                facet_type=facet.facet_type,
                required=facet.required,
                terms=facet.terms,
                statement_types=facet.statement_types,
                executed_search_intent=intent.to_dict(),
                coverage_status="hits" if package.evidence else "missing",
                ranked_hits=tuple(
                    {
                        "candidate_id": item.candidate_id,
                        "passage_order": item.passage_order,
                        "component_rank": item.rank,
                        "component_score": item.score,
                        "fusion_contribution": contributions.get(
                            (list_index, _evidence_key(item)), 0.0
                        ),
                        "final_rank": final_ranks.get(_evidence_key(item)),
                        "document_id": item.document_id,
                        "document_version_id": item.document_version_id,
                        "locator": item.locator,
                        "review_status": item.review_status,
                    }
                    for item in package.evidence
                ),
            )
        )
    return tuple(traces)


def _faceted_fallback_package(
    baseline: EvidencePackage,
    *,
    search_intent: SearchIntent,
    facet_runs: tuple[FacetRetrievalTrace, ...],
    coverage: tuple[FacetCoverage, ...],
    fallback_reason: str,
    negative_guard_activated: bool,
) -> EvidencePackage:
    missing_required = [
        item.facet_type
        for item in coverage
        if item.required and item.status != "hits"
    ]
    return replace(
        baseline,
        trace=RetrievalTrace(
            search_intent=search_intent.to_dict(),
            iterations=baseline.trace.iterations,
            fts5_projection=baseline.trace.fts5_projection,
            fusion=_facet_fusion_trace(
                baseline.trace.fusion,
                activated=False,
                fallback_reason=fallback_reason,
                negative_guard_activated=negative_guard_activated,
            ),
            persistent_logging=baseline.trace.persistent_logging,
            facet_runs=facet_runs,
        ),
        facet_coverage=coverage,
        missing_required_facets=missing_required,
    )


def _facet_fusion_trace(
    stage_one_fusion: dict[str, object] | None,
    *,
    activated: bool,
    fallback_reason: str | None,
    negative_guard_activated: bool,
) -> dict[str, object]:
    return {
        **(stage_one_fusion or {}),
        "facets": {
            "strategy": "baseline_protected_capacity_interleaving",
            "max_new_hits_per_facet": MAX_NEW_HITS_PER_FACET,
            "facet_type_priority": list(FACET_TYPE_PRIORITY),
            "deduplication_key": "candidate_id+passage_order",
            "activated": activated,
            "negative_guard_activated": negative_guard_activated,
            "fallback_reason": fallback_reason,
        },
    }


def _evidence_key(item: EvidenceItem) -> tuple[str, int]:
    return item.candidate_id, item.passage_order


def _connect_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


def _validate_clean_set_database(connection: sqlite3.Connection) -> None:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version != 4:
        raise EvidenceRetrievalError(
            f"find_evidence requires schema version 4, found {version}"
        )
    names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'view'"
        )
    }
    required = {
        "clean_reviewed_candidates",
        "clean_reviewed_candidate_evidence",
        "clean_reviewed_relations",
    }
    if not required.issubset(names):
        raise EvidenceRetrievalError("schema-v4 Clean Set views are incomplete")


def _structured_rows(
    connection: sqlite3.Connection,
    intent: SearchIntent,
) -> list[sqlite3.Row]:
    clauses: list[str] = []
    parameters: list[object] = []
    _add_in_filter(clauses, parameters, "candidates.statement_type", intent.statement_types)
    _add_in_filter(clauses, parameters, "candidates.binding_status", intent.binding_statuses)
    _add_in_filter(clauses, parameters, "documents.document_id", intent.document_ids)
    _add_in_filter(clauses, parameters, "documents.file_type", intent.document_types)
    if intent.object_features is not None:
        object_clauses = ["statement_entities.statement_pk = candidates.statement_pk"]
        features = intent.object_features
        if features.entity_type is not None:
            object_clauses.append("entities.entity_type = ?")
            parameters.append(features.entity_type)
        if features.entity_role is not None:
            object_clauses.append("statement_entities.role = ?")
            parameters.append(features.entity_role)
        if features.entity_label is not None:
            object_clauses.append(
                """
                (
                    entities.preferred_label = ? COLLATE NOCASE
                    OR EXISTS (
                        SELECT 1 FROM entity_aliases
                        WHERE entity_aliases.entity_pk = entities.entity_pk
                        AND entity_aliases.alias = ? COLLATE NOCASE
                    )
                )
                """
            )
            parameters.extend([features.entity_label, features.entity_label])
        clauses.append(
            """
            EXISTS (
                SELECT 1
                FROM statement_entities
                JOIN entities USING (entity_pk)
                WHERE %s
            )
            """ % " AND ".join(object_clauses)
        )
    where_sql = " AND ".join(clauses) if clauses else "1 = 1"
    return connection.execute(
        f"""
        WITH operative_candidates AS (
            SELECT
                raw_candidates.candidate_id,
                raw_candidates.statement_candidate_v3_pk,
                raw_candidates.statement_pk,
                raw_candidates.document_node_pk AS raw_document_node_pk,
                raw_candidates.evidence_source_text AS raw_exact_text,
                raw_candidates.char_start AS raw_char_start,
                raw_candidates.char_end AS raw_char_end,
                raw_candidates.source_role AS raw_source_role,
                statements.statement_id,
                CASE
                    WHEN current_reviews.review_status = 'confirmed'
                    THEN current_reviews.reviewed_statement_type
                    ELSE statements.statement_type
                END AS statement_type,
                CASE
                    WHEN current_reviews.review_status = 'confirmed'
                    THEN current_reviews.reviewed_binding_status
                    ELSE statements.binding_status
                END AS binding_status,
                CASE
                    WHEN current_reviews.review_status = 'confirmed'
                    THEN current_reviews.reviewed_normalized_text
                    ELSE statements.text
                END AS normalized_text,
                CASE
                    WHEN current_reviews.review_status = 'confirmed'
                    THEN 'confirmed'
                    ELSE 'needs_review'
                END AS review_status,
                CASE
                    WHEN current_reviews.review_status = 'confirmed'
                    THEN 'fachlich_geprüft'
                    ELSE 'automatisch_extrahiert_ungeprüft'
                END AS review_status_label,
                CASE
                    WHEN current_reviews.review_status = 'confirmed'
                    THEN 'review_overlay'
                    ELSE 'raw_candidate'
                END AS value_origin,
                CASE
                    WHEN current_reviews.review_status = 'confirmed'
                    THEN current_reviews.candidate_review_pk
                    ELSE NULL
                END AS confirmed_review_pk
            FROM statement_candidates_v3 AS raw_candidates
            JOIN statements
              ON statements.statement_pk = raw_candidates.statement_pk
            LEFT JOIN candidate_review_overlays AS current_reviews
              ON current_reviews.statement_candidate_v3_pk =
                 raw_candidates.statement_candidate_v3_pk
             AND current_reviews.is_current = 1
            WHERE (
                current_reviews.candidate_review_pk IS NULL
                AND statements.review_status <> 'rejected'
            ) OR (
                current_reviews.candidate_review_pk IS NOT NULL
                AND current_reviews.review_status <> 'rejected'
                AND current_reviews.review_decision <> 'verwerfen'
            )
        )
        SELECT
            candidates.candidate_id,
            candidates.statement_id,
            COALESCE(passages.passage_order, 1) AS passage_order,
            COALESCE(passages.exact_text, candidates.raw_exact_text) AS exact_text,
            documents.document_id,
            COALESCE(documents.title, documents.filename) AS document_label,
            documents.filename,
            documents.file_type AS document_type,
            versions.document_version_id,
            versions.version_label AS document_version_label,
            nodes.document_node_id,
            COALESCE(passages.locator, nodes.locator, nodes.node_path) AS locator,
            nodes.page_start,
            nodes.page_end,
            CASE
                WHEN passages.candidate_review_evidence_passage_pk IS NOT NULL
                THEN passages.char_start_in_node
                ELSE candidates.raw_char_start
            END AS char_start_in_node,
            CASE
                WHEN passages.candidate_review_evidence_passage_pk IS NOT NULL
                THEN passages.char_end_in_node
                ELSE candidates.raw_char_end
            END AS char_end_in_node,
            COALESCE(passages.source_role, candidates.raw_source_role) AS source_role,
            candidates.statement_type,
            candidates.binding_status,
            candidates.normalized_text,
            candidates.review_status,
            candidates.review_status_label,
            candidates.value_origin,
            COALESCE(nodes.heading, '') AS section_heading,
            COALESCE(parent_nodes.heading, '') AS parent_heading,
            COALESCE(documents.title, documents.filename, '') AS document_title,
            documents.file_type AS context_document_type,
            COALESCE((
                SELECT group_concat(entity_context.context_text, ' ')
                FROM (
                    SELECT
                        entities.entity_type || ' ' ||
                        statement_entities.role || ' ' ||
                        entities.preferred_label || ' ' ||
                        COALESCE((
                            SELECT group_concat(ordered_aliases.alias, ' ')
                            FROM (
                                SELECT entity_aliases.alias
                                FROM entity_aliases
                                WHERE entity_aliases.entity_pk = entities.entity_pk
                                ORDER BY entity_aliases.alias
                            ) AS ordered_aliases
                        ), '') AS context_text
                    FROM statement_entities
                    JOIN entities USING (entity_pk)
                    WHERE statement_entities.statement_pk = candidates.statement_pk
                    ORDER BY
                        statement_entities.role,
                        entities.entity_type,
                        entities.preferred_label
                ) AS entity_context
            ), '') AS object_context,
            COALESCE((
                SELECT group_concat(value_context.context_text, ' ')
                FROM (
                    SELECT
                        statement_values.value_name || ' ' ||
                        statement_values.raw_value || ' ' ||
                        COALESCE(statement_values.text_value, '') || ' ' ||
                        COALESCE(CAST(statement_values.numeric_value AS TEXT), '') || ' ' ||
                        COALESCE(statement_values.unit, '') AS context_text
                    FROM statement_values
                    WHERE statement_values.statement_pk = candidates.statement_pk
                    ORDER BY statement_values.statement_value_pk
                ) AS value_context
            ), '') AS value_context
        FROM operative_candidates AS candidates
        LEFT JOIN candidate_review_evidence_passages AS passages
          ON passages.candidate_review_pk = candidates.confirmed_review_pk
        JOIN document_nodes AS nodes
          ON nodes.document_node_pk = COALESCE(
              passages.document_node_pk,
              candidates.raw_document_node_pk
          )
        LEFT JOIN document_nodes AS parent_nodes
          ON parent_nodes.document_node_pk = nodes.parent_document_node_pk
        JOIN document_versions AS versions
          ON versions.document_version_pk = nodes.document_version_pk
        JOIN documents ON documents.document_pk = versions.document_pk
        WHERE {where_sql}
        ORDER BY
            CASE
                WHEN candidates.statement_type = 'requirement'
                 AND candidates.binding_status = 'binding' THEN 0
                WHEN candidates.statement_type = 'requirement' THEN 1
                WHEN candidates.statement_type = 'planning_measure' THEN 2
                WHEN candidates.statement_type = 'finding' THEN 3
                ELSE 4
            END,
            candidates.candidate_id,
            COALESCE(passages.passage_order, 1)
        """,
        parameters,
    ).fetchall()


def _primary_retrieval(
    connection: sqlite3.Connection,
    *,
    structured_rows: list[sqlite3.Row],
    intent: SearchIntent,
) -> _PrimaryRetrievalResult:
    if intent.retrieval_path == "structured":
        rows = _rank_rows(structured_rows, {})
        return _PrimaryRetrievalResult(
            rows=rows,
            scores={},
            projection=None,
            method="sql",
            methods={_row_key(row): "sql" for row in rows},
            variants=[],
            fusion=None,
        )
    fts_connection, row_count = _clean_set_fts5_projection(structured_rows)
    effective_term_groups = _fts5_term_groups(intent.terms)
    exact_query = _build_clean_set_fts5_query(effective_term_groups)
    prefix_query = _build_clean_set_fts5_prefix_query(effective_term_groups)
    context_enabled = intent.retrieval_path in {"fts5", "hybrid"}
    try:
        if exact_query is None:
            return _PrimaryRetrievalResult(
                rows=[],
                scores={},
                projection=_projection_trace(
                    row_count,
                    effective_term_groups=effective_term_groups,
                    exact_match_expression=None,
                    prefix_match_expression=None,
                    context_match_expression=None,
                ),
                method="fts5",
                methods={},
                variants=[],
                fusion=None,
            )
        exact_scores = _fts5_candidate_scores(
            fts_connection,
            "clean_set_fts",
            exact_query,
            (0.0, 0.0, 5.0, 1.0),
        )
        prefix_scores = (
            _fts5_candidate_scores(
                fts_connection,
                "clean_set_fts",
                prefix_query,
                (0.0, 0.0, 5.0, 1.0),
            )
            if prefix_query is not None
            else {}
        )
        context_scores = (
            _fts5_candidate_scores(
                fts_connection,
                "context_fts",
                exact_query,
                (0.0, 0.0, *CONTEXT_FIELD_WEIGHTS.values()),
            )
            if context_enabled
            else {}
        )
    finally:
        fts_connection.close()
    exact_rows = _rank_rows(
        [row for row in structured_rows if row["candidate_id"] in exact_scores],
        exact_scores,
    )
    prefix_rows = _rank_rows(
        [row for row in structured_rows if row["candidate_id"] in prefix_scores],
        prefix_scores,
    )
    selected = list(exact_rows)
    seen = {_row_key(row) for row in selected}
    selected.extend(row for row in prefix_rows if _row_key(row) not in seen)
    baseline_rows = selected
    baseline_scores = dict(prefix_scores)
    baseline_scores.update(exact_scores)
    context_rows = _rank_rows_lexically(
        [row for row in structured_rows if row["candidate_id"] in context_scores],
        context_scores,
    )
    baseline_top = baseline_rows[: intent.max_results]
    context_top = context_rows[: intent.max_results]
    complementary = _context_is_complementary(
        [_row_key(row) for row in baseline_top],
        [_row_key(row) for row in context_top],
    )
    fusion_activated = context_enabled and complementary
    baseline_contributions: dict[tuple[str, int], float] = {}
    context_contributions: dict[tuple[str, int], float] = {}
    if fusion_activated:
        selected, scores, baseline_contributions, context_contributions = (
            _reciprocal_rank_fusion(baseline_top, context_top)
        )
    else:
        selected = baseline_rows
        scores = baseline_scores
    method_prefix = "sql+" if _has_structured_filters(intent) else ""
    variants = (
        [
            _RankedVariant(
                method=f"{method_prefix}fts5_exact",
                match_expression=exact_query,
                rows=exact_rows,
                scores=exact_scores,
                fusion_contributions=baseline_contributions,
            ),
            _RankedVariant(
                method=f"{method_prefix}fts5_prefix",
                match_expression=prefix_query or "",
                rows=prefix_rows,
                scores=prefix_scores,
                fusion_contributions=baseline_contributions,
            ),
            _RankedVariant(
                method=f"{method_prefix}fts5_context",
                match_expression=exact_query,
                rows=context_rows,
                scores=context_scores,
                fusion_contributions=context_contributions,
            ),
        ]
        if context_enabled
        else []
    )
    baseline_keys = {_row_key(row) for row in baseline_top}
    context_keys = {_row_key(row) for row in context_top}
    methods = {}
    for row in selected:
        key = _row_key(row)
        if not fusion_activated:
            methods[key] = f"{method_prefix}fts5"
        elif key in baseline_keys and key in context_keys:
            methods[key] = f"{method_prefix}fts5_exact_prefix+context_fusion"
        elif key in context_keys:
            methods[key] = f"{method_prefix}fts5_context_fusion"
        else:
            methods[key] = f"{method_prefix}fts5_exact_prefix_fusion"
    return _PrimaryRetrievalResult(
        rows=selected,
        scores=scores,
        projection=_projection_trace(
            row_count,
            effective_term_groups=effective_term_groups,
            exact_match_expression=exact_query,
            prefix_match_expression=prefix_query,
            context_match_expression=exact_query if context_enabled else None,
        ),
        method=f"{method_prefix}fts5",
        methods=methods,
        variants=variants,
        fusion=(
            {
                "method": "reciprocal_rank_fusion",
                "k": RECIPROCAL_RANK_FUSION_K,
                "complementarity_criterion": CONTEXT_COMPLEMENTARITY_CRITERION,
                "complementary": complementary,
                "activated": fusion_activated,
            }
            if context_enabled
            else None
        ),
    )


def _fts5_candidate_scores(
    connection: sqlite3.Connection,
    table_name: str,
    query: str,
    weights: tuple[float, ...],
) -> dict[str, float]:
    if table_name not in {"clean_set_fts", "context_fts"}:
        raise EvidenceRetrievalError("unknown in-memory FTS5 projection")
    placeholders = ", ".join("?" for _ in weights)
    matches = connection.execute(
        f"""
        SELECT candidate_id, bm25({table_name}, {placeholders}) AS score
        FROM {table_name}
        WHERE {table_name} MATCH ?
        ORDER BY score, candidate_id, CAST(passage_order AS INTEGER)
        """,
        (*weights, query),
    ).fetchall()
    scores: dict[str, float] = {}
    for candidate_id, score in matches:
        parsed_score = float(score)
        scores[candidate_id] = min(parsed_score, scores.get(candidate_id, parsed_score))
    return scores


def _clean_set_fts5_projection(
    rows: list[sqlite3.Row],
) -> tuple[sqlite3.Connection, int]:
    projection = sqlite3.connect(":memory:")
    projection.execute(
        """
        CREATE VIRTUAL TABLE clean_set_fts USING fts5(
            candidate_id UNINDEXED,
            passage_order UNINDEXED,
            normalized_text,
            exact_text,
            tokenize = 'unicode61 remove_diacritics 2'
        )
        """
    )
    projection.execute(
        """
        CREATE VIRTUAL TABLE context_fts USING fts5(
            candidate_id UNINDEXED,
            passage_order UNINDEXED,
            normalized_statement,
            original_passage,
            section_heading,
            parent_heading,
            document_title,
            document_type,
            object_context,
            value_context,
            tokenize = 'unicode61 remove_diacritics 2'
        )
        """
    )
    projection.executemany(
        "INSERT INTO clean_set_fts VALUES (?, ?, ?, ?)",
        [
            (
                row["candidate_id"],
                row["passage_order"],
                row["normalized_text"],
                row["exact_text"],
            )
            for row in rows
        ],
    )
    projection.executemany(
        "INSERT INTO context_fts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                row["candidate_id"],
                row["passage_order"],
                row["normalized_text"],
                row["exact_text"],
                row["section_heading"],
                row["parent_heading"],
                row["document_title"],
                row["context_document_type"],
                row["object_context"],
                row["value_context"],
            )
            for row in rows
        ],
    )
    return projection, len(rows)


def _tool_iteration(
    structured_rows: list[sqlite3.Row],
    *,
    intent: SearchIntent,
    tool_text_dir: str | Path | None,
    project_root: str | Path | None,
    prefer_rg: bool,
) -> tuple[list[Any], set[tuple[str, int]]]:
    if tool_text_dir is None:
        raise EvidenceRetrievalError(
            f"{intent.retrieval_path} requires a configured tool_text_dir"
        )
    text_dir = Path(tool_text_dir).resolve()
    root = Path(project_root).resolve() if project_root is not None else text_dir.parent
    runner = ToolSearchRunner(
        allowed_paths=[text_dir],
        project_root=root,
        prefer_rg=prefer_rg,
    )
    hits = runner.search(
        " ".join(intent.terms),
        search_path=text_dir,
        max_results=intent.max_results,
    )
    mapped: set[tuple[str, int]] = set()
    for hit in hits:
        matched = hit.matched_text.strip()
        for row in structured_rows:
            exact = row["exact_text"].strip()
            if hit.document_id == row["document_id"] and (
                matched in exact or exact in matched
            ):
                mapped.add(_row_key(row))
    return hits, mapped


def _active_relations(
    connection: sqlite3.Connection,
    candidate_ids: list[str],
) -> list[ActiveRelation]:
    if not candidate_ids:
        return []
    placeholders = ",".join("?" for _ in candidate_ids)
    rows = connection.execute(
        f"""
        SELECT relation_id, relation_type, source_candidate_id,
               target_candidate_id, acceptance_mode, technical_activation_status
        FROM clean_reviewed_relations
        WHERE source_candidate_id IN ({placeholders})
           OR target_candidate_id IN ({placeholders})
        ORDER BY relation_id
        """,
        [*candidate_ids, *candidate_ids],
    ).fetchall()
    return [ActiveRelation(**dict(row)) for row in rows]


def _rank_rows(
    rows: list[sqlite3.Row],
    scores: Mapping[str, float],
) -> list[sqlite3.Row]:
    return sorted(
        rows,
        key=lambda row: (
            _evidence_priority(row["statement_type"], row["binding_status"]),
            scores.get(row["candidate_id"], 0.0),
            row["candidate_id"],
            row["passage_order"],
        ),
    )


def _rank_rows_lexically(
    rows: list[sqlite3.Row],
    scores: Mapping[str, float],
) -> list[sqlite3.Row]:
    return sorted(
        rows,
        key=lambda row: (
            scores.get(row["candidate_id"], 0.0),
            row["candidate_id"],
            row["passage_order"],
        ),
    )


def _context_is_complementary(
    baseline_keys: list[tuple[str, int]],
    context_keys: list[tuple[str, int]],
) -> bool:
    baseline = set(baseline_keys)
    return any(key not in baseline for key in context_keys)


def _reciprocal_rank_fusion(
    baseline_rows: list[sqlite3.Row],
    context_rows: list[sqlite3.Row],
) -> tuple[
    list[sqlite3.Row],
    dict[str, float],
    dict[tuple[str, int], float],
    dict[tuple[str, int], float],
]:
    rows_by_key = {
        _row_key(row): row for row in [*baseline_rows, *context_rows]
    }
    baseline_ranks = {
        _row_key(row): rank for rank, row in enumerate(baseline_rows, start=1)
    }
    context_ranks = {
        _row_key(row): rank for rank, row in enumerate(context_rows, start=1)
    }
    baseline_contributions = {
        key: 1.0 / (RECIPROCAL_RANK_FUSION_K + rank)
        for key, rank in baseline_ranks.items()
    }
    context_contributions = {
        key: 1.0 / (RECIPROCAL_RANK_FUSION_K + rank)
        for key, rank in context_ranks.items()
    }
    totals = {
        key: baseline_contributions.get(key, 0.0)
        + context_contributions.get(key, 0.0)
        for key in rows_by_key
    }
    missing_rank = len(rows_by_key) + 1
    ranked_keys = sorted(
        rows_by_key,
        key=lambda key: (
            -totals[key],
            baseline_ranks.get(key, missing_rank),
            context_ranks.get(key, missing_rank),
            key,
        ),
    )
    scores: dict[str, float] = {}
    for key, total in totals.items():
        candidate_id = key[0]
        scores[candidate_id] = max(total, scores.get(candidate_id, total))
    return (
        [rows_by_key[key] for key in ranked_keys],
        scores,
        baseline_contributions,
        context_contributions,
    )


def _evidence_item(
    row: sqlite3.Row,
    *,
    rank: int,
    score: float | None,
    method: str,
    relation_ids: tuple[str, ...],
) -> EvidenceItem:
    return EvidenceItem(
        candidate_id=row["candidate_id"],
        statement_id=row["statement_id"],
        passage_order=row["passage_order"],
        exact_evidence_text=row["exact_text"],
        document_id=row["document_id"],
        document_label=row["document_label"],
        filename=row["filename"],
        document_type=row["document_type"],
        document_version_id=row["document_version_id"],
        document_version_label=row["document_version_label"],
        document_node_id=row["document_node_id"],
        locator=row["locator"],
        page_start=row["page_start"],
        page_end=row["page_end"],
        char_start_in_node=row["char_start_in_node"],
        char_end_in_node=row["char_end_in_node"],
        source_role=row["source_role"],
        statement_type=row["statement_type"],
        binding_status=row["binding_status"],
        normalized_statement=row["normalized_text"],
        review_status=row["review_status"],
        review_status_label=row["review_status_label"],
        value_origin=row["value_origin"],
        retrieval_method=method,
        rank=rank,
        score=score,
        active_relation_ids=relation_ids,
    )


def _trace_iteration(
    *,
    iteration: int,
    method: str,
    parameters: dict[str, object],
    rows: list[sqlite3.Row],
    scores: Mapping[str, float] | None = None,
    fusion_contributions: Mapping[tuple[str, int], float] | None = None,
    final_ranks: Mapping[tuple[str, int], int] | None = None,
) -> TraceIteration:
    candidates = list(dict.fromkeys(row["candidate_id"] for row in rows))
    component_scores = scores or {}
    contributions = fusion_contributions or {}
    resolved_final_ranks = final_ranks or {}
    return TraceIteration(
        iteration=iteration,
        method=method,
        parameters=parameters,
        candidate_hit_count=len(candidates),
        evidence_hit_count=len(rows),
        candidate_ids=candidates,
        ranks=list(range(1, len(rows) + 1)),
        locators=[row["locator"] for row in rows if row["locator"] is not None],
        tool_names=[],
        ranked_hits=[
            {
                "candidate_id": row["candidate_id"],
                "passage_order": row["passage_order"],
                "component_rank": rank,
                "component_score": component_scores.get(row["candidate_id"]),
                "fusion_contribution": contributions.get(_row_key(row), 0.0),
                "final_rank": resolved_final_ranks.get(_row_key(row)),
            }
            for rank, row in enumerate(rows, start=1)
        ],
    )


def _tool_trace_iteration(
    *,
    iteration: int,
    mode: str,
    hits: list[Any],
    rows: list[sqlite3.Row],
    mapped_keys: set[tuple[str, int]],
    pattern: str,
    max_results: int,
) -> TraceIteration:
    mapped_rows = [row for row in rows if _row_key(row) in mapped_keys]
    return TraceIteration(
        iteration=iteration,
        method=mode,
        parameters={"pattern": pattern, "max_results": max_results},
        candidate_hit_count=len({row["candidate_id"] for row in mapped_rows}),
        evidence_hit_count=len(mapped_rows),
        candidate_ids=sorted({row["candidate_id"] for row in mapped_rows}),
        ranks=list(range(1, len(mapped_rows) + 1)),
        locators=[row["locator"] for row in mapped_rows if row["locator"] is not None],
        tool_names=sorted({hit.tool_name for hit in hits}),
        ranked_hits=[
            {
                "candidate_id": row["candidate_id"],
                "passage_order": row["passage_order"],
                "component_rank": rank,
                "component_score": None,
                "fusion_contribution": 0.0,
                "final_rank": rank,
            }
            for rank, row in enumerate(mapped_rows, start=1)
        ],
    )


def _trace_parameters(intent: SearchIntent) -> dict[str, object]:
    return {
        "terms": list(intent.terms),
        "statement_types": list(intent.statement_types),
        "binding_statuses": list(intent.binding_statuses),
        "document_ids": list(intent.document_ids),
        "document_types": list(intent.document_types),
        "object_features": (
            intent.object_features.to_dict()
            if intent.object_features is not None
            else None
        ),
        "max_results": intent.max_results,
    }


def _has_structured_filters(intent: SearchIntent) -> bool:
    return any(
        (
            intent.statement_types,
            intent.binding_statuses,
            intent.document_ids,
            intent.document_types,
        )
    ) or intent.object_features is not None


def _projection_trace(
    row_count: int,
    *,
    effective_term_groups: tuple[tuple[str, ...], ...],
    exact_match_expression: str | None,
    prefix_match_expression: str | None,
    context_match_expression: str | None,
) -> dict[str, object]:
    return {
        "authoritative": False,
        "row_count": row_count,
        "source_tables": [
            "statement_candidates_v3",
            "candidate_review_overlays",
            "candidate_review_evidence_passages",
        ],
        "storage": "in_memory",
        "effective_term_groups": [list(group) for group in effective_term_groups],
        "match_expression": exact_match_expression,
        "exact_match_expression": exact_match_expression,
        "prefix_match_expression": prefix_match_expression,
        "context": {
            "match_expression": context_match_expression,
            "field_weights": dict(CONTEXT_FIELD_WEIGHTS),
            "source_columns": dict(_CONTEXT_SOURCE_COLUMNS),
            "metadata_is_evidence": False,
        },
    }


def _fts5_term_groups(terms: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
    groups: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()
    for term in terms:
        variants = (term, term.translate(_GERMAN_QUERY_TRANSLITERATION))
        for variant in variants:
            tokens = tuple(re.findall(r"\w+", variant, flags=re.UNICODE))
            if not tokens:
                continue
            normalized = tuple(token.casefold() for token in tokens)
            if normalized in seen:
                continue
            seen.add(normalized)
            groups.append(tokens)
    return tuple(groups)


def _build_clean_set_fts5_query(
    term_groups: tuple[tuple[str, ...], ...],
) -> str | None:
    expressions: list[str] = []
    for group in term_groups:
        quoted = [f'"{token}"' for token in group]
        expression = " AND ".join(quoted)
        expressions.append(f"({expression})" if len(quoted) > 1 else expression)
    return " OR ".join(expressions) if expressions else None


def _build_clean_set_fts5_prefix_query(
    term_groups: tuple[tuple[str, ...], ...],
) -> str | None:
    expressions: list[str] = []
    for group in term_groups:
        if not any(_is_safe_fts5_prefix_token(token) for token in group):
            continue
        quoted = [
            f'"{token}"*' if _is_safe_fts5_prefix_token(token) else f'"{token}"'
            for token in group
        ]
        expression = " AND ".join(quoted)
        expressions.append(f"({expression})" if len(quoted) > 1 else expression)
    return " OR ".join(expressions) if expressions else None


def _is_safe_fts5_prefix_token(token: str) -> bool:
    return len(token) >= MIN_FTS5_PREFIX_LENGTH and token.isalpha()


def _evidence_priority(statement_type: str, binding_status: str) -> int:
    if statement_type == "requirement" and binding_status == "binding":
        return 0
    if statement_type == "requirement":
        return 1
    if statement_type == "planning_measure":
        return 2
    if statement_type == "finding":
        return 3
    return 4


def _row_key(row: sqlite3.Row) -> tuple[str, int]:
    return row["candidate_id"], row["passage_order"]


def _add_in_filter(
    clauses: list[str],
    parameters: list[object],
    column: str,
    values: tuple[str, ...],
) -> None:
    if not values:
        return
    placeholders = ",".join("?" for _ in values)
    clauses.append(f"{column} IN ({placeholders})")
    parameters.extend(values)


def _facets_from_value(
    value: Mapping[str, object],
    *,
    schema_version: str,
    query_text: str | None,
) -> tuple[SearchFacet, ...]:
    if "facets" not in value:
        if schema_version == "2.0" and query_text is None:
            raise SearchIntentError("SearchIntent 2.0 requires query_text")
        return ()
    items = value["facets"]
    if not isinstance(items, list) or len(items) > MAX_FACETS:
        raise SearchIntentError("facets must contain at most 3 values")
    if schema_version != "2.0":
        raise SearchIntentError("facets require schema_version 2.0")
    if query_text is None:
        raise SearchIntentError("facets require query_text")
    facets = tuple(
        SearchFacet.from_dict(item, query_text=query_text) for item in items
    )
    signatures = [
        tuple(_normalize_facet_text(term) for term in facet.terms)
        for facet in facets
    ]
    if len(signatures) != len(set(signatures)):
        raise SearchIntentError("facets contain duplicate term sets")
    return facets


def _optional_question_text(
    value: Mapping[str, object], key: str
) -> str | None:
    if key not in value:
        return None
    item = value[key]
    if not isinstance(item, str):
        raise SearchIntentError(f"{key} must be a string")
    stripped = item.strip()
    if (
        not stripped
        or len(stripped) > 2_000
        or re.search(r"[\x00-\x1f]", stripped)
    ):
        raise SearchIntentError(f"{key} contains unsafe or empty text")
    return stripped


def _validate_facet_terms(
    terms: tuple[str, ...], *, query_text: str
) -> None:
    query_tokens = _facet_tokens(query_text)
    content_tokens: set[str] = set()
    for term in terms:
        if _FACET_FORBIDDEN_TEXT.search(term):
            raise SearchIntentError("facet contains forbidden free-form instruction")
        term_tokens = _facet_tokens(term)
        if not _contains_token_sequence(query_tokens, term_tokens):
            raise SearchIntentError("facet terms must occur in query_text")
        content_tokens.update(
            token
            for token in term_tokens
            if token not in _FACET_STOP_TOKENS
            and token not in _FACET_GENERIC_TOKENS
            and len(token) >= 3
        )
    if not content_tokens:
        raise SearchIntentError("facet requires a question-derived professional anchor")


def _contains_token_sequence(
    source: tuple[str, ...], candidate: tuple[str, ...]
) -> bool:
    if not candidate or len(candidate) > len(source):
        return False
    width = len(candidate)
    return any(source[index : index + width] == candidate for index in range(len(source) - width + 1))


def _facet_tokens(value: str) -> tuple[str, ...]:
    return tuple(_normalize_facet_text(value).split())


def _normalize_facet_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).translate(
        _GERMAN_QUERY_TRANSLITERATION
    ).casefold()
    return " ".join(re.findall(r"\w+", normalized, flags=re.UNICODE))


def _allowed_list(
    value: Mapping[str, object],
    key: str,
    allowed: tuple[str, ...],
) -> tuple[str, ...]:
    if key not in value:
        return ()
    items = value[key]
    if not isinstance(items, list) or not items:
        raise SearchIntentError(f"{key} must be a non-empty list")
    if not all(isinstance(item, str) and item in allowed for item in items):
        raise SearchIntentError(f"{key} contains an unsupported value")
    return _unique_tuple(items, key)


def _identifier_list(value: Mapping[str, object], key: str) -> tuple[str, ...]:
    if key not in value:
        return ()
    items = value[key]
    if not isinstance(items, list) or not items:
        raise SearchIntentError(f"{key} must be a non-empty list")
    if not all(isinstance(item, str) and _IDENTIFIER.fullmatch(item) for item in items):
        raise SearchIntentError(f"{key} contains an invalid identifier")
    return _unique_tuple(items, key)


def _safe_text_list(
    value: Mapping[str, object],
    key: str,
    *,
    max_items: int,
    max_length: int,
) -> tuple[str, ...]:
    if key not in value:
        return ()
    items = value[key]
    if not isinstance(items, list) or not items or len(items) > max_items:
        raise SearchIntentError(f"{key} must contain 1 to {max_items} values")
    parsed: list[str] = []
    for item in items:
        if not isinstance(item, str):
            raise SearchIntentError(f"{key} values must be strings")
        stripped = item.strip()
        if (
            not stripped
            or len(stripped) > max_length
            or _UNSAFE_TEXT.search(stripped)
            or re.search(r"\w", stripped, flags=re.UNICODE) is None
        ):
            raise SearchIntentError(f"{key} contains unsafe or empty text")
        parsed.append(stripped)
    return _unique_tuple(parsed, key, case_insensitive=True)


def _required_allowed_string(
    value: Mapping[str, object], key: str, allowed: tuple[str, ...]
) -> str:
    item = value.get(key)
    if not isinstance(item, str) or item not in allowed:
        raise SearchIntentError(f"{key} contains an unsupported value")
    return item


def _optional_allowed_string(
    value: Mapping[str, object], key: str, allowed: tuple[str, ...]
) -> str | None:
    if key not in value:
        return None
    return _required_allowed_string(value, key, allowed)


def _optional_safe_text(
    value: Mapping[str, object], key: str, *, max_length: int
) -> str | None:
    if key not in value:
        return None
    item = value[key]
    if not isinstance(item, str):
        raise SearchIntentError(f"{key} must be a string")
    stripped = item.strip()
    if not stripped or len(stripped) > max_length or _UNSAFE_TEXT.search(stripped):
        raise SearchIntentError(f"{key} contains unsafe or empty text")
    return stripped


def _unique_tuple(
    values: list[str],
    key: str,
    *,
    case_insensitive: bool = False,
) -> tuple[str, ...]:
    comparable = [value.casefold() if case_insensitive else value for value in values]
    if len(set(comparable)) != len(comparable):
        raise SearchIntentError(f"{key} contains duplicate values")
    return tuple(values)
