CREATE TABLE IF NOT EXISTS projects (
    project_pk INTEGER PRIMARY KEY,
    project_id TEXT NOT NULL UNIQUE CHECK (length(trim(project_id)) > 0),
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    description TEXT,
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS documents (
    document_pk INTEGER PRIMARY KEY,
    document_id TEXT NOT NULL UNIQUE CHECK (length(trim(document_id)) > 0),
    project_pk INTEGER REFERENCES projects(project_pk),
    filename TEXT NOT NULL CHECK (length(trim(filename)) > 0),
    file_type TEXT NOT NULL CHECK (length(trim(file_type)) > 0),
    source_path TEXT NOT NULL CHECK (length(trim(source_path)) > 0),
    processed_text_path TEXT,
    imported_at TEXT NOT NULL,
    title TEXT,
    document_hash TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
) STRICT;

CREATE TABLE IF NOT EXISTS document_versions (
    document_version_pk INTEGER PRIMARY KEY,
    document_version_id TEXT NOT NULL UNIQUE CHECK (length(trim(document_version_id)) > 0),
    document_pk INTEGER NOT NULL REFERENCES documents(document_pk) ON DELETE CASCADE,
    version_label TEXT,
    content_hash TEXT,
    is_current INTEGER NOT NULL DEFAULT 0 CHECK (is_current IN (0, 1)),
    created_at TEXT NOT NULL,
    supersedes_document_version_pk INTEGER REFERENCES document_versions(document_version_pk),
    CHECK (
        supersedes_document_version_pk IS NULL
        OR supersedes_document_version_pk <> document_version_pk
    )
) STRICT;

CREATE UNIQUE INDEX IF NOT EXISTS ux_document_versions_one_current
ON document_versions(document_pk)
WHERE is_current = 1;

CREATE TABLE IF NOT EXISTS document_nodes (
    document_node_pk INTEGER PRIMARY KEY,
    document_node_id TEXT NOT NULL UNIQUE CHECK (length(trim(document_node_id)) > 0),
    document_version_pk INTEGER NOT NULL REFERENCES document_versions(document_version_pk) ON DELETE CASCADE,
    parent_document_node_pk INTEGER REFERENCES document_nodes(document_node_pk),
    node_path TEXT NOT NULL CHECK (length(trim(node_path)) > 0),
    node_rank INTEGER NOT NULL CHECK (node_rank > 0),
    element_type TEXT NOT NULL DEFAULT 'page_text' CHECK (
        element_type IN (
            'document_root',
            'section',
            'page_text',
            'empty_page',
            'non_citable_text'
        )
    ),
    heading TEXT,
    locator TEXT,
    page_start INTEGER CHECK (page_start IS NULL OR page_start >= 1),
    page_end INTEGER CHECK (page_end IS NULL OR page_end >= 1),
    char_start INTEGER CHECK (char_start IS NULL OR char_start >= 0),
    char_end INTEGER CHECK (char_end IS NULL OR char_end >= 0),
    raw_extracted_text TEXT NOT NULL,
    source_text TEXT NOT NULL,
    search_text TEXT NOT NULL,
    is_citable INTEGER NOT NULL DEFAULT 1 CHECK (is_citable IN (0, 1)),
    created_at TEXT NOT NULL,
    UNIQUE (document_version_pk, node_path),
    CHECK (page_start IS NULL OR page_end IS NULL OR page_start <= page_end),
    CHECK (char_start IS NULL OR char_end IS NULL OR char_start <= char_end)
) STRICT;

CREATE VIRTUAL TABLE IF NOT EXISTS document_nodes_fts USING fts5(
    heading,
    source_text,
    search_text,
    content = 'document_nodes',
    content_rowid = 'document_node_pk',
    tokenize = 'unicode61 remove_diacritics 2',
    prefix = '2 3 4'
);

CREATE TRIGGER IF NOT EXISTS document_nodes_ai
AFTER INSERT ON document_nodes
BEGIN
    INSERT INTO document_nodes_fts(rowid, heading, source_text, search_text)
    VALUES (new.document_node_pk, new.heading, new.source_text, new.search_text);
END;

CREATE TRIGGER IF NOT EXISTS document_nodes_ad
AFTER DELETE ON document_nodes
BEGIN
    INSERT INTO document_nodes_fts(
        document_nodes_fts,
        rowid,
        heading,
        source_text,
        search_text
    )
    VALUES (
        'delete',
        old.document_node_pk,
        old.heading,
        old.source_text,
        old.search_text
    );
END;

CREATE TRIGGER IF NOT EXISTS document_nodes_au
AFTER UPDATE ON document_nodes
BEGIN
    INSERT INTO document_nodes_fts(
        document_nodes_fts,
        rowid,
        heading,
        source_text,
        search_text
    )
    VALUES (
        'delete',
        old.document_node_pk,
        old.heading,
        old.source_text,
        old.search_text
    );
    INSERT INTO document_nodes_fts(rowid, heading, source_text, search_text)
    VALUES (new.document_node_pk, new.heading, new.source_text, new.search_text);
END;

CREATE TABLE IF NOT EXISTS document_references (
    document_reference_pk INTEGER PRIMARY KEY,
    document_reference_id TEXT NOT NULL UNIQUE CHECK (length(trim(document_reference_id)) > 0),
    source_document_node_pk INTEGER NOT NULL REFERENCES document_nodes(document_node_pk) ON DELETE CASCADE,
    target_document_pk INTEGER REFERENCES documents(document_pk),
    target_document_node_pk INTEGER REFERENCES document_nodes(document_node_pk),
    reference_text TEXT NOT NULL CHECK (length(trim(reference_text)) > 0),
    created_at TEXT NOT NULL,
    CHECK (target_document_pk IS NOT NULL OR target_document_node_pk IS NOT NULL)
) STRICT;

CREATE TABLE IF NOT EXISTS statements (
    statement_pk INTEGER PRIMARY KEY,
    statement_id TEXT NOT NULL UNIQUE CHECK (length(trim(statement_id)) > 0),
    statement_type TEXT NOT NULL CHECK (
        statement_type IN ('requirement', 'planning_measure', 'decision', 'finding')
    ),
    text TEXT NOT NULL CHECK (length(trim(text)) > 0),
    binding_status TEXT NOT NULL CHECK (binding_status IN ('binding', 'unknown')),
    lifecycle_status TEXT NOT NULL CHECK (lifecycle_status IN ('active')),
    review_status TEXT NOT NULL CHECK (
        review_status IN ('needs_review', 'confirmed', 'rejected')
    ),
    action_state TEXT CHECK (
        action_state IS NULL
        OR action_state IN ('planned', 'to_be_verified', 'documented')
    ),
    creation_method TEXT NOT NULL CHECK (
        creation_method IN ('model_candidate', 'manual', 'migration')
    ),
    reviewed_by TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    CHECK (
        review_status NOT IN ('confirmed', 'rejected')
        OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)
    )
) STRICT;

CREATE TRIGGER IF NOT EXISTS statements_confirmed_insert_requires_source
BEFORE INSERT ON statements
WHEN new.review_status = 'confirmed'
BEGIN
    SELECT RAISE(ABORT, 'confirmed statement requires statement_source');
END;

CREATE TRIGGER IF NOT EXISTS statements_confirmed_update_requires_source
BEFORE UPDATE OF review_status ON statements
WHEN new.review_status = 'confirmed'
AND NOT EXISTS (
    SELECT 1
    FROM statement_sources
    WHERE statement_sources.statement_pk = new.statement_pk
)
BEGIN
    SELECT RAISE(ABORT, 'confirmed statement requires statement_source');
END;

CREATE TABLE IF NOT EXISTS statement_sources (
    statement_source_pk INTEGER PRIMARY KEY,
    statement_source_id TEXT NOT NULL UNIQUE CHECK (length(trim(statement_source_id)) > 0),
    statement_pk INTEGER NOT NULL REFERENCES statements(statement_pk) ON DELETE CASCADE,
    document_node_pk INTEGER NOT NULL REFERENCES document_nodes(document_node_pk),
    source_role TEXT NOT NULL CHECK (length(trim(source_role)) > 0),
    created_at TEXT NOT NULL,
    UNIQUE (statement_pk, document_node_pk, source_role)
) STRICT;

CREATE TRIGGER IF NOT EXISTS statement_sources_keep_last_confirmed_source
BEFORE DELETE ON statement_sources
WHEN (
    SELECT review_status
    FROM statements
    WHERE statement_pk = old.statement_pk
) = 'confirmed'
AND (
    SELECT COUNT(*)
    FROM statement_sources
    WHERE statement_pk = old.statement_pk
) <= 1
BEGIN
    SELECT RAISE(ABORT, 'cannot delete last source of confirmed statement');
END;

CREATE TABLE IF NOT EXISTS entities (
    entity_pk INTEGER PRIMARY KEY,
    entity_id TEXT NOT NULL UNIQUE CHECK (length(trim(entity_id)) > 0),
    entity_type TEXT NOT NULL CHECK (length(trim(entity_type)) > 0),
    preferred_label TEXT NOT NULL CHECK (length(trim(preferred_label)) > 0),
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS entity_aliases (
    entity_alias_pk INTEGER PRIMARY KEY,
    entity_alias_id TEXT NOT NULL UNIQUE CHECK (length(trim(entity_alias_id)) > 0),
    entity_pk INTEGER NOT NULL REFERENCES entities(entity_pk) ON DELETE CASCADE,
    alias TEXT NOT NULL CHECK (length(trim(alias)) > 0),
    created_at TEXT NOT NULL,
    UNIQUE (entity_pk, alias)
) STRICT;

CREATE TABLE IF NOT EXISTS statement_entities (
    statement_entity_pk INTEGER PRIMARY KEY,
    statement_entity_id TEXT NOT NULL UNIQUE CHECK (length(trim(statement_entity_id)) > 0),
    statement_pk INTEGER NOT NULL REFERENCES statements(statement_pk) ON DELETE CASCADE,
    entity_pk INTEGER NOT NULL REFERENCES entities(entity_pk) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (length(trim(role)) > 0),
    created_at TEXT NOT NULL,
    UNIQUE (statement_pk, entity_pk, role)
) STRICT;

CREATE TABLE IF NOT EXISTS statement_values (
    statement_value_pk INTEGER PRIMARY KEY,
    statement_value_id TEXT NOT NULL UNIQUE CHECK (length(trim(statement_value_id)) > 0),
    statement_pk INTEGER NOT NULL REFERENCES statements(statement_pk) ON DELETE CASCADE,
    value_name TEXT NOT NULL CHECK (length(trim(value_name)) > 0),
    raw_value TEXT NOT NULL CHECK (length(trim(raw_value)) > 0),
    text_value TEXT,
    numeric_value REAL,
    unit TEXT,
    created_at TEXT NOT NULL,
    CHECK (text_value IS NOT NULL OR numeric_value IS NOT NULL),
    CHECK (
        value_name NOT IN ('maximum_distance', 'height', 'cantilever_each_side')
        OR numeric_value IS NULL
        OR unit IS NOT NULL
    )
) STRICT;

CREATE TABLE IF NOT EXISTS statement_conditions (
    statement_condition_pk INTEGER PRIMARY KEY,
    statement_condition_id TEXT NOT NULL UNIQUE CHECK (length(trim(statement_condition_id)) > 0),
    statement_pk INTEGER NOT NULL REFERENCES statements(statement_pk) ON DELETE CASCADE,
    condition_text TEXT NOT NULL CHECK (length(trim(condition_text)) > 0),
    parameters_json TEXT CHECK (parameters_json IS NULL OR json_valid(parameters_json)),
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS classifications (
    classification_pk INTEGER PRIMARY KEY,
    classification_id TEXT NOT NULL UNIQUE CHECK (length(trim(classification_id)) > 0),
    scheme TEXT NOT NULL CHECK (length(trim(scheme)) > 0),
    code TEXT NOT NULL CHECK (length(trim(code)) > 0),
    label TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (scheme, code)
) STRICT;

CREATE TABLE IF NOT EXISTS statement_classifications (
    statement_classification_pk INTEGER PRIMARY KEY,
    statement_classification_id TEXT NOT NULL UNIQUE CHECK (length(trim(statement_classification_id)) > 0),
    statement_pk INTEGER NOT NULL REFERENCES statements(statement_pk) ON DELETE CASCADE,
    classification_pk INTEGER NOT NULL REFERENCES classifications(classification_pk) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    UNIQUE (statement_pk, classification_pk)
) STRICT;

CREATE TABLE IF NOT EXISTS statement_groups (
    statement_group_pk INTEGER PRIMARY KEY,
    statement_group_id TEXT NOT NULL UNIQUE CHECK (length(trim(statement_group_id)) > 0),
    group_type TEXT NOT NULL CHECK (length(trim(group_type)) > 0),
    label TEXT NOT NULL CHECK (length(trim(label)) > 0),
    created_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS statement_group_members (
    statement_group_member_pk INTEGER PRIMARY KEY,
    statement_group_member_id TEXT NOT NULL UNIQUE CHECK (length(trim(statement_group_member_id)) > 0),
    statement_group_pk INTEGER NOT NULL REFERENCES statement_groups(statement_group_pk) ON DELETE CASCADE,
    statement_pk INTEGER NOT NULL REFERENCES statements(statement_pk) ON DELETE CASCADE,
    member_rank INTEGER NOT NULL CHECK (member_rank > 0),
    created_at TEXT NOT NULL,
    UNIQUE (statement_group_pk, statement_pk)
) STRICT;

CREATE TABLE IF NOT EXISTS statement_relations (
    statement_relation_pk INTEGER PRIMARY KEY,
    statement_relation_id TEXT NOT NULL UNIQUE CHECK (length(trim(statement_relation_id)) > 0),
    source_statement_pk INTEGER NOT NULL REFERENCES statements(statement_pk) ON DELETE CASCADE,
    target_statement_pk INTEGER NOT NULL REFERENCES statements(statement_pk) ON DELETE CASCADE,
    relation_type TEXT NOT NULL CHECK (
        relation_type IN (
            'addresses',
            'derived_from',
            'implements_decision',
            'supersedes',
            'conflicts_with'
        )
    ),
    created_at TEXT NOT NULL,
    CHECK (source_statement_pk <> target_statement_pk),
    CHECK (
        relation_type <> 'conflicts_with'
        OR source_statement_pk < target_statement_pk
    ),
    UNIQUE (source_statement_pk, target_statement_pk, relation_type)
) STRICT;

CREATE TABLE IF NOT EXISTS query_runs (
    query_run_pk INTEGER PRIMARY KEY,
    query_run_id TEXT NOT NULL UNIQUE CHECK (length(trim(query_run_id)) > 0),
    question_text TEXT NOT NULL CHECK (length(trim(question_text)) > 0),
    search_intent_json TEXT CHECK (
        search_intent_json IS NULL OR json_valid(search_intent_json)
    ),
    query_plan_json TEXT CHECK (query_plan_json IS NULL OR json_valid(query_plan_json)),
    created_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL CHECK (length(trim(status)) > 0)
) STRICT;

CREATE TABLE IF NOT EXISTS evidence_items (
    evidence_item_pk INTEGER PRIMARY KEY,
    evidence_item_id TEXT NOT NULL UNIQUE CHECK (length(trim(evidence_item_id)) > 0),
    query_run_pk INTEGER REFERENCES query_runs(query_run_pk) ON DELETE CASCADE,
    statement_source_pk INTEGER REFERENCES statement_sources(statement_source_pk),
    document_node_pk INTEGER REFERENCES document_nodes(document_node_pk),
    rank INTEGER NOT NULL CHECK (rank > 0),
    score REAL,
    excerpt TEXT NOT NULL CHECK (length(trim(excerpt)) > 0),
    created_at TEXT NOT NULL,
    CHECK (statement_source_pk IS NOT NULL OR document_node_pk IS NOT NULL)
) STRICT;

CREATE TRIGGER IF NOT EXISTS evidence_items_insert_requires_citable_node
BEFORE INSERT ON evidence_items
WHEN new.document_node_pk IS NOT NULL
AND (
    SELECT is_citable
    FROM document_nodes
    WHERE document_node_pk = new.document_node_pk
) = 0
BEGIN
    SELECT RAISE(ABORT, 'evidence item requires citable document node');
END;

CREATE TRIGGER IF NOT EXISTS evidence_items_update_requires_citable_node
BEFORE UPDATE OF document_node_pk ON evidence_items
WHEN new.document_node_pk IS NOT NULL
AND (
    SELECT is_citable
    FROM document_nodes
    WHERE document_node_pk = new.document_node_pk
) = 0
BEGIN
    SELECT RAISE(ABORT, 'evidence item requires citable document node');
END;

CREATE TABLE IF NOT EXISTS answers (
    answer_pk INTEGER PRIMARY KEY,
    answer_id TEXT NOT NULL UNIQUE CHECK (length(trim(answer_id)) > 0),
    query_run_pk INTEGER NOT NULL REFERENCES query_runs(query_run_pk) ON DELETE CASCADE,
    answer_text TEXT NOT NULL CHECK (length(trim(answer_text)) > 0),
    created_at TEXT NOT NULL,
    generation_method TEXT NOT NULL CHECK (length(trim(generation_method)) > 0)
) STRICT;

CREATE TABLE IF NOT EXISTS schema_migration_audit (
    schema_migration_audit_pk INTEGER PRIMARY KEY,
    migration_id TEXT NOT NULL UNIQUE CHECK (length(trim(migration_id)) > 0),
    from_user_version INTEGER NOT NULL,
    to_user_version INTEGER NOT NULL,
    database_sha256_before TEXT,
    legacy_schema_json TEXT CHECK (
        legacy_schema_json IS NULL OR json_valid(legacy_schema_json)
    ),
    legacy_table_counts_json TEXT CHECK (
        legacy_table_counts_json IS NULL OR json_valid(legacy_table_counts_json)
    ),
    migrated_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS source_segments (
    segment_pk INTEGER PRIMARY KEY,
    segment_id TEXT NOT NULL UNIQUE CHECK (length(trim(segment_id)) > 0),
    document_id TEXT NOT NULL,
    page_number INTEGER CHECK (page_number IS NULL OR page_number >= 1),
    section_label TEXT,
    segment_order INTEGER NOT NULL CHECK (segment_order > 0),
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (document_id) REFERENCES documents(document_id)
) STRICT;

CREATE VIRTUAL TABLE IF NOT EXISTS source_segments_fts USING fts5(
    segment_id UNINDEXED,
    document_id UNINDEXED,
    text,
    content='source_segments',
    content_rowid='segment_pk'
);

CREATE TABLE IF NOT EXISTS extraction_runs (
    extraction_run_pk INTEGER PRIMARY KEY,
    extraction_run_id TEXT NOT NULL UNIQUE CHECK (length(trim(extraction_run_id)) > 0),
    document_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    method TEXT NOT NULL CHECK (length(trim(method)) > 0),
    model_name TEXT,
    prompt_version TEXT,
    status TEXT NOT NULL CHECK (length(trim(status)) > 0),
    parameters_json TEXT CHECK (parameters_json IS NULL OR json_valid(parameters_json)),
    raw_model_output_json TEXT CHECK (
        raw_model_output_json IS NULL OR json_valid(raw_model_output_json)
    ),
    FOREIGN KEY (document_id) REFERENCES documents(document_id)
) STRICT;

CREATE TABLE IF NOT EXISTS statement_candidate_batches_v3 (
    statement_candidate_batch_v3_pk INTEGER PRIMARY KEY,
    batch_import_id TEXT NOT NULL UNIQUE CHECK (length(trim(batch_import_id)) > 0),
    extraction_run_pk INTEGER NOT NULL UNIQUE
        REFERENCES extraction_runs(extraction_run_pk),
    document_version_pk INTEGER NOT NULL
        REFERENCES document_versions(document_version_pk),
    package_version TEXT NOT NULL CHECK (package_version = 'statement_candidates_batch_v3'),
    contract_version TEXT NOT NULL CHECK (contract_version = 'statement_candidates_batch_v3'),
    prompt_version TEXT NOT NULL CHECK (prompt_version = 'statement_candidates_frontier_v3'),
    batch_id TEXT NOT NULL CHECK (length(trim(batch_id)) > 0),
    batch_number INTEGER NOT NULL CHECK (batch_number > 0),
    total_batches INTEGER NOT NULL CHECK (total_batches > 0),
    model_name TEXT NOT NULL CHECK (length(trim(model_name)) > 0),
    batch_manifest_sha256 TEXT NOT NULL CHECK (length(batch_manifest_sha256) = 64),
    input_sha256 TEXT NOT NULL CHECK (length(input_sha256) = 64),
    prompt_sha256 TEXT NOT NULL CHECK (length(prompt_sha256) = 64),
    batch_sha256 TEXT NOT NULL CHECK (length(batch_sha256) = 64),
    raw_output_sha256 TEXT NOT NULL CHECK (length(raw_output_sha256) = 64),
    binding_json TEXT NOT NULL CHECK (json_valid(binding_json)),
    node_processing_json TEXT NOT NULL CHECK (json_valid(node_processing_json)),
    raw_output_json TEXT NOT NULL CHECK (json_valid(raw_output_json)),
    candidate_count INTEGER NOT NULL CHECK (candidate_count >= 0),
    relation_count INTEGER NOT NULL CHECK (relation_count >= 0),
    created_at TEXT NOT NULL,
    UNIQUE (batch_manifest_sha256, batch_id)
) STRICT;

CREATE TABLE IF NOT EXISTS statement_candidate_node_processing_v3 (
    statement_candidate_node_processing_v3_pk INTEGER PRIMARY KEY,
    node_processing_id TEXT NOT NULL UNIQUE CHECK (length(trim(node_processing_id)) > 0),
    statement_candidate_batch_v3_pk INTEGER NOT NULL
        REFERENCES statement_candidate_batches_v3(statement_candidate_batch_v3_pk)
        ON DELETE CASCADE,
    document_node_pk INTEGER NOT NULL REFERENCES document_nodes(document_node_pk),
    processing_order INTEGER NOT NULL CHECK (processing_order > 0),
    status TEXT NOT NULL CHECK (
        status IN (
            'processed_with_candidates',
            'processed_without_candidate',
            'excluded_non_citable',
            'failed'
        )
    ),
    candidate_ids_json TEXT NOT NULL CHECK (json_valid(candidate_ids_json)),
    error TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (statement_candidate_batch_v3_pk, processing_order),
    UNIQUE (statement_candidate_batch_v3_pk, document_node_pk)
) STRICT;

CREATE TABLE IF NOT EXISTS statement_candidates_v3 (
    statement_candidate_v3_pk INTEGER PRIMARY KEY,
    statement_candidate_v3_id TEXT NOT NULL UNIQUE
        CHECK (length(trim(statement_candidate_v3_id)) > 0),
    statement_candidate_batch_v3_pk INTEGER NOT NULL
        REFERENCES statement_candidate_batches_v3(statement_candidate_batch_v3_pk)
        ON DELETE CASCADE,
    candidate_id TEXT NOT NULL CHECK (length(trim(candidate_id)) > 0),
    candidate_sha256 TEXT NOT NULL CHECK (length(candidate_sha256) = 64),
    statement_pk INTEGER NOT NULL REFERENCES statements(statement_pk) ON DELETE CASCADE,
    document_node_pk INTEGER NOT NULL REFERENCES document_nodes(document_node_pk),
    evidence_source_text TEXT NOT NULL CHECK (length(evidence_source_text) > 0),
    char_start INTEGER CHECK (char_start IS NULL OR char_start >= 0),
    char_end INTEGER CHECK (char_end IS NULL OR char_end >= 0),
    source_role TEXT NOT NULL CHECK (source_role IN ('primary', 'supporting')),
    statement_subtype TEXT NOT NULL CHECK (length(trim(statement_subtype)) > 0),
    output_constraint TEXT,
    candidate_json TEXT NOT NULL CHECK (json_valid(candidate_json)),
    review_notes_json TEXT NOT NULL CHECK (json_valid(review_notes_json)),
    validation_warnings_json TEXT NOT NULL CHECK (json_valid(validation_warnings_json)),
    created_at TEXT NOT NULL,
    CHECK (
        (char_start IS NULL AND char_end IS NULL)
        OR (char_start IS NOT NULL AND char_end IS NOT NULL AND char_start <= char_end)
    ),
    UNIQUE (statement_candidate_batch_v3_pk, candidate_id)
) STRICT;

CREATE TABLE IF NOT EXISTS statement_candidate_document_references_v3 (
    statement_candidate_document_reference_v3_pk INTEGER PRIMARY KEY,
    statement_candidate_document_reference_v3_id TEXT NOT NULL UNIQUE
        CHECK (length(trim(statement_candidate_document_reference_v3_id)) > 0),
    statement_candidate_v3_pk INTEGER NOT NULL
        REFERENCES statement_candidates_v3(statement_candidate_v3_pk)
        ON DELETE CASCADE,
    reference_order INTEGER NOT NULL CHECK (reference_order > 0),
    reference_text TEXT NOT NULL CHECK (length(trim(reference_text)) > 0),
    reference_type TEXT NOT NULL CHECK (reference_type IN ('normative', 'internal', 'appendix')),
    target_document_id TEXT,
    target_document_node_id TEXT,
    target_document_pk INTEGER REFERENCES documents(document_pk),
    target_document_node_pk INTEGER REFERENCES document_nodes(document_node_pk),
    reference_json TEXT NOT NULL CHECK (json_valid(reference_json)),
    created_at TEXT NOT NULL,
    UNIQUE (statement_candidate_v3_pk, reference_order)
) STRICT;

CREATE TABLE IF NOT EXISTS statement_relations_v3 (
    statement_relation_v3_pk INTEGER PRIMARY KEY,
    statement_relation_v3_id TEXT NOT NULL UNIQUE
        CHECK (length(trim(statement_relation_v3_id)) > 0),
    statement_candidate_batch_v3_pk INTEGER NOT NULL
        REFERENCES statement_candidate_batches_v3(statement_candidate_batch_v3_pk)
        ON DELETE CASCADE,
    relation_id TEXT NOT NULL CHECK (length(trim(relation_id)) > 0),
    relation_sha256 TEXT NOT NULL CHECK (length(relation_sha256) = 64),
    statement_relation_pk INTEGER NOT NULL
        REFERENCES statement_relations(statement_relation_pk) ON DELETE CASCADE,
    source_candidate_v3_pk INTEGER NOT NULL
        REFERENCES statement_candidates_v3(statement_candidate_v3_pk) ON DELETE CASCADE,
    target_candidate_v3_pk INTEGER NOT NULL
        REFERENCES statement_candidates_v3(statement_candidate_v3_pk) ON DELETE CASCADE,
    relation_type TEXT NOT NULL CHECK (
        relation_type IN (
            'addresses',
            'derived_from',
            'implements_decision',
            'supersedes',
            'conflicts_with'
        )
    ),
    relation_json TEXT NOT NULL CHECK (json_valid(relation_json)),
    created_at TEXT NOT NULL,
    CHECK (source_candidate_v3_pk <> target_candidate_v3_pk),
    UNIQUE (statement_candidate_batch_v3_pk, relation_id)
) STRICT;

CREATE TABLE IF NOT EXISTS candidate_review_overlays (
    candidate_review_pk INTEGER PRIMARY KEY,
    candidate_review_id TEXT NOT NULL UNIQUE
        CHECK (length(trim(candidate_review_id)) > 0),
    statement_candidate_v3_pk INTEGER NOT NULL
        REFERENCES statement_candidates_v3(statement_candidate_v3_pk),
    review_decision TEXT NOT NULL CHECK (
        review_decision IN (
            'akzeptieren',
            'korrigieren',
            'verwerfen',
            'unklar',
            'nicht_entschieden'
        )
    ),
    original_review_decision TEXT NOT NULL,
    reviewed_statement_type TEXT NOT NULL CHECK (
        reviewed_statement_type IN (
            'requirement',
            'planning_measure',
            'decision',
            'finding'
        )
    ),
    reviewed_normalized_text TEXT NOT NULL
        CHECK (length(trim(reviewed_normalized_text)) > 0),
    reviewed_binding_status TEXT NOT NULL CHECK (
        reviewed_binding_status IN ('binding', 'unknown')
    ),
    review_status TEXT NOT NULL CHECK (
        review_status IN ('needs_review', 'confirmed', 'rejected')
    ),
    review_source TEXT NOT NULL CHECK (length(trim(review_source)) > 0),
    review_version TEXT NOT NULL CHECK (length(trim(review_version)) > 0),
    reviewed_by TEXT,
    reviewed_at TEXT,
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    created_at TEXT NOT NULL,
    CHECK (
        review_status = 'needs_review'
        OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)
    ),
    UNIQUE (statement_candidate_v3_pk, review_version)
) STRICT;

CREATE UNIQUE INDEX IF NOT EXISTS candidate_review_overlays_one_current
ON candidate_review_overlays(statement_candidate_v3_pk)
WHERE is_current = 1;

CREATE TABLE IF NOT EXISTS candidate_review_evidence_passages (
    candidate_review_evidence_passage_pk INTEGER PRIMARY KEY,
    candidate_review_evidence_passage_id TEXT NOT NULL UNIQUE
        CHECK (length(trim(candidate_review_evidence_passage_id)) > 0),
    candidate_review_pk INTEGER NOT NULL
        REFERENCES candidate_review_overlays(candidate_review_pk) ON DELETE CASCADE,
    passage_order INTEGER NOT NULL CHECK (passage_order > 0),
    document_pk INTEGER NOT NULL REFERENCES documents(document_pk),
    document_node_pk INTEGER NOT NULL REFERENCES document_nodes(document_node_pk),
    locator TEXT,
    exact_text TEXT NOT NULL CHECK (length(exact_text) > 0),
    char_start_in_node INTEGER CHECK (
        char_start_in_node IS NULL OR char_start_in_node >= 0
    ),
    char_end_in_node INTEGER CHECK (
        char_end_in_node IS NULL OR char_end_in_node >= 0
    ),
    source_role TEXT NOT NULL CHECK (source_role IN ('primary', 'supporting')),
    source_candidate_v3_pk INTEGER
        REFERENCES statement_candidates_v3(statement_candidate_v3_pk),
    created_at TEXT NOT NULL,
    CHECK (
        (char_start_in_node IS NULL AND char_end_in_node IS NULL)
        OR (
            char_start_in_node IS NOT NULL
            AND char_end_in_node IS NOT NULL
            AND char_start_in_node < char_end_in_node
        )
    ),
    UNIQUE (candidate_review_pk, passage_order)
) STRICT;

CREATE TRIGGER IF NOT EXISTS candidate_review_evidence_passages_validate_insert
BEFORE INSERT ON candidate_review_evidence_passages
BEGIN
    SELECT CASE
        WHEN NOT EXISTS (
            SELECT 1
            FROM document_nodes AS nodes
            JOIN document_versions AS versions
                ON versions.document_version_pk = nodes.document_version_pk
            WHERE nodes.document_node_pk = new.document_node_pk
            AND versions.document_pk = new.document_pk
        )
        THEN RAISE(ABORT, 'review evidence node does not belong to document')
    END;
    SELECT CASE
        WHEN instr(
            (SELECT source_text FROM document_nodes WHERE document_node_pk = new.document_node_pk),
            new.exact_text
        ) = 0
        THEN RAISE(ABORT, 'review evidence is not an exact source_text passage')
    END;
    SELECT CASE
        WHEN new.char_start_in_node IS NOT NULL
        AND substr(
            (SELECT source_text FROM document_nodes WHERE document_node_pk = new.document_node_pk),
            new.char_start_in_node + 1,
            new.char_end_in_node - new.char_start_in_node
        ) <> new.exact_text
        THEN RAISE(ABORT, 'review evidence character range does not match exact_text')
    END;
END;

CREATE TRIGGER IF NOT EXISTS candidate_review_evidence_passages_validate_update
BEFORE UPDATE OF document_pk, document_node_pk, exact_text,
    char_start_in_node, char_end_in_node
ON candidate_review_evidence_passages
BEGIN
    SELECT CASE
        WHEN NOT EXISTS (
            SELECT 1
            FROM document_nodes AS nodes
            JOIN document_versions AS versions
                ON versions.document_version_pk = nodes.document_version_pk
            WHERE nodes.document_node_pk = new.document_node_pk
            AND versions.document_pk = new.document_pk
        )
        THEN RAISE(ABORT, 'review evidence node does not belong to document')
    END;
    SELECT CASE
        WHEN instr(
            (SELECT source_text FROM document_nodes WHERE document_node_pk = new.document_node_pk),
            new.exact_text
        ) = 0
        THEN RAISE(ABORT, 'review evidence is not an exact source_text passage')
    END;
    SELECT CASE
        WHEN new.char_start_in_node IS NOT NULL
        AND substr(
            (SELECT source_text FROM document_nodes WHERE document_node_pk = new.document_node_pk),
            new.char_start_in_node + 1,
            new.char_end_in_node - new.char_start_in_node
        ) <> new.exact_text
        THEN RAISE(ABORT, 'review evidence character range does not match exact_text')
    END;
END;

CREATE TABLE IF NOT EXISTS relation_review_overlays (
    relation_review_pk INTEGER PRIMARY KEY,
    relation_review_id TEXT NOT NULL UNIQUE
        CHECK (length(trim(relation_review_id)) > 0),
    statement_relation_v3_pk INTEGER NOT NULL
        REFERENCES statement_relations_v3(statement_relation_v3_pk),
    professional_decision TEXT NOT NULL CHECK (
        professional_decision IN ('akzeptieren', 'verwerfen', 'unklar')
    ),
    original_review_code TEXT NOT NULL CHECK (length(trim(original_review_code)) > 0),
    acceptance_mode TEXT NOT NULL CHECK (
        acceptance_mode IN ('immediate', 'after_candidate_corrections')
    ),
    technical_activation_status TEXT NOT NULL CHECK (
        technical_activation_status IN (
            'active',
            'inactive_rejected_endpoint',
            'inactive_catalog_conflict'
        )
    ),
    non_activation_reason TEXT,
    review_source TEXT NOT NULL CHECK (length(trim(review_source)) > 0),
    review_version TEXT NOT NULL CHECK (length(trim(review_version)) > 0),
    reviewed_by TEXT NOT NULL CHECK (length(trim(reviewed_by)) > 0),
    reviewed_at TEXT NOT NULL CHECK (length(trim(reviewed_at)) > 0),
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    created_at TEXT NOT NULL,
    CHECK (
        (technical_activation_status = 'active' AND non_activation_reason IS NULL)
        OR (
            technical_activation_status <> 'active'
            AND non_activation_reason IS NOT NULL
            AND length(trim(non_activation_reason)) > 0
        )
    ),
    CHECK (
        technical_activation_status <> 'active'
        OR professional_decision = 'akzeptieren'
    ),
    UNIQUE (statement_relation_v3_pk, review_version)
) STRICT;

CREATE UNIQUE INDEX IF NOT EXISTS relation_review_overlays_one_current
ON relation_review_overlays(statement_relation_v3_pk)
WHERE is_current = 1;

CREATE TABLE IF NOT EXISTS relation_review_candidate_dependencies (
    relation_review_candidate_dependency_pk INTEGER PRIMARY KEY,
    relation_review_pk INTEGER NOT NULL
        REFERENCES relation_review_overlays(relation_review_pk) ON DELETE CASCADE,
    candidate_review_pk INTEGER NOT NULL
        REFERENCES candidate_review_overlays(candidate_review_pk),
    dependency_order INTEGER NOT NULL CHECK (dependency_order > 0),
    endpoint_role TEXT NOT NULL CHECK (endpoint_role IN ('source', 'target')),
    dependency_reason TEXT NOT NULL CHECK (length(trim(dependency_reason)) > 0),
    UNIQUE (relation_review_pk, dependency_order),
    UNIQUE (relation_review_pk, candidate_review_pk)
) STRICT;

CREATE VIEW IF NOT EXISTS clean_reviewed_candidates AS
SELECT
    reviews.candidate_review_pk,
    reviews.candidate_review_id,
    candidates.statement_candidate_v3_pk,
    candidates.statement_candidate_v3_id,
    candidates.candidate_id,
    candidates.statement_pk,
    reviews.review_decision,
    reviews.reviewed_statement_type AS statement_type,
    reviews.reviewed_normalized_text AS normalized_text,
    reviews.reviewed_binding_status AS binding_status,
    reviews.review_status,
    reviews.review_source,
    reviews.review_version,
    reviews.reviewed_by,
    reviews.reviewed_at
FROM candidate_review_overlays AS reviews
JOIN statement_candidates_v3 AS candidates
    ON candidates.statement_candidate_v3_pk = reviews.statement_candidate_v3_pk
WHERE reviews.is_current = 1
AND reviews.review_status = 'confirmed'
AND reviews.review_decision IN ('akzeptieren', 'korrigieren');

CREATE VIEW IF NOT EXISTS clean_reviewed_candidate_evidence AS
SELECT
    candidates.candidate_id,
    passages.candidate_review_pk,
    passages.passage_order,
    documents.document_id,
    nodes.document_node_id,
    passages.locator,
    passages.exact_text,
    passages.char_start_in_node,
    passages.char_end_in_node,
    passages.source_role
FROM candidate_review_evidence_passages AS passages
JOIN clean_reviewed_candidates AS candidates
    ON candidates.candidate_review_pk = passages.candidate_review_pk
JOIN documents ON documents.document_pk = passages.document_pk
JOIN document_nodes AS nodes
    ON nodes.document_node_pk = passages.document_node_pk;

CREATE VIEW IF NOT EXISTS clean_reviewed_relations AS
SELECT
    reviews.relation_review_pk,
    reviews.relation_review_id,
    relations.statement_relation_v3_pk,
    relations.statement_relation_pk,
    relations.relation_id,
    relations.relation_type,
    source_candidates.candidate_id AS source_candidate_id,
    target_candidates.candidate_id AS target_candidate_id,
    source_candidates.statement_type AS source_statement_type,
    target_candidates.statement_type AS target_statement_type,
    reviews.professional_decision,
    reviews.acceptance_mode,
    reviews.technical_activation_status,
    reviews.review_source,
    reviews.review_version
FROM relation_review_overlays AS reviews
JOIN statement_relations_v3 AS relations
    ON relations.statement_relation_v3_pk = reviews.statement_relation_v3_pk
JOIN clean_reviewed_candidates AS source_candidates
    ON source_candidates.statement_candidate_v3_pk = relations.source_candidate_v3_pk
JOIN clean_reviewed_candidates AS target_candidates
    ON target_candidates.statement_candidate_v3_pk = relations.target_candidate_v3_pk
WHERE reviews.is_current = 1
AND reviews.professional_decision = 'akzeptieren'
AND reviews.technical_activation_status = 'active';

CREATE TABLE IF NOT EXISTS extracted_records (
    extracted_record_pk INTEGER PRIMARY KEY,
    record_id TEXT NOT NULL UNIQUE CHECK (length(trim(record_id)) > 0),
    record_type TEXT NOT NULL CHECK (
        record_type IN (
            'fire_safety_requirement',
            'fire_safety_measure',
            'open_issue'
        )
    ),
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    source_document_id TEXT NOT NULL,
    source_chunk_id TEXT NOT NULL,
    source_page INTEGER CHECK (source_page IS NULL OR source_page >= 1),
    source_section TEXT,
    confidence REAL NOT NULL CHECK (confidence >= 0.0 AND confidence <= 1.0),
    validation_status TEXT NOT NULL CHECK (length(trim(validation_status)) > 0),
    extraction_run_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (source_document_id) REFERENCES documents(document_id),
    FOREIGN KEY (source_chunk_id) REFERENCES source_segments(segment_id),
    FOREIGN KEY (extraction_run_id) REFERENCES extraction_runs(extraction_run_id)
) STRICT;

CREATE TABLE IF NOT EXISTS retrieval_runs (
    retrieval_run_pk INTEGER PRIMARY KEY,
    retrieval_run_id TEXT NOT NULL UNIQUE CHECK (length(trim(retrieval_run_id)) > 0),
    query_text TEXT NOT NULL,
    retrieval_mode TEXT NOT NULL,
    top_k INTEGER NOT NULL CHECK (top_k > 0),
    prompt_version TEXT,
    llm_provider TEXT NOT NULL,
    answer_mode TEXT NOT NULL,
    answer_text TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    status TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS retrieval_hits (
    retrieval_hit_pk INTEGER PRIMARY KEY,
    hit_id TEXT NOT NULL UNIQUE CHECK (length(trim(hit_id)) > 0),
    retrieval_run_id TEXT NOT NULL,
    retrieval_method TEXT NOT NULL,
    document_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    rank INTEGER NOT NULL CHECK (rank > 0),
    score REAL NOT NULL,
    snippet TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (retrieval_run_id) REFERENCES retrieval_runs(retrieval_run_id),
    FOREIGN KEY (document_id) REFERENCES documents(document_id),
    FOREIGN KEY (chunk_id) REFERENCES source_segments(segment_id)
) STRICT;

CREATE TABLE IF NOT EXISTS tool_search_hits (
    tool_search_hit_pk INTEGER PRIMARY KEY,
    tool_hit_id TEXT NOT NULL UNIQUE CHECK (length(trim(tool_hit_id)) > 0),
    retrieval_run_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    search_pattern TEXT NOT NULL,
    document_id TEXT,
    segment_id TEXT,
    source_path TEXT NOT NULL,
    page_number INTEGER CHECK (page_number IS NULL OR page_number >= 1),
    line_number INTEGER,
    context_before TEXT,
    matched_text TEXT NOT NULL,
    context_after TEXT,
    FOREIGN KEY (retrieval_run_id) REFERENCES retrieval_runs(retrieval_run_id),
    FOREIGN KEY (document_id) REFERENCES documents(document_id),
    FOREIGN KEY (segment_id) REFERENCES source_segments(segment_id)
) STRICT;

PRAGMA user_version = 4;
