from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from jsonschema import ValidationError

from info_system.config import Settings
from info_system.construction.document_pipeline import (
    DocumentPipelineError,
    import_document_nodes_from_pdf,
    load_manifest,
    write_export_package,
)
from info_system.construction.segmentation import document_id_from_path, segment_pages
from info_system.construction.statement_candidate_pipeline import (
    StatementCandidateError,
    export_statement_review_package,
    import_statement_candidate_package,
    validate_statement_candidate_package,
    write_statement_candidate_prompt_package,
)
from info_system.construction.text_extraction import (
    PdfExtractionError,
    extract_text_from_pdf,
)
from info_system.db import initialize_database
from info_system.operation.clean_set_retrieval import (
    EvidenceRetrievalError,
    SearchIntent,
    SearchIntentError,
    find_evidence,
)
from info_system.operation.local_llm import LocalLlmError, run_local_question


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="info-system",
        description="Local information system prototype CLI.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Show the prototype version and exit.",
    )

    subcommands = parser.add_subparsers(dest="command")
    init_db = subcommands.add_parser(
        "init-db",
        help="Create or update the local SQLite schema.",
    )
    init_db.add_argument(
        "--db",
        dest="db_path",
        help="Path to the SQLite database file.",
    )
    extract_text = subcommands.add_parser(
        "extract-text",
        help="Extract page text from a PDF without storing it.",
    )
    extract_text.add_argument("pdf_path", help="Path to the PDF file.")
    chunk = subcommands.add_parser(
        "chunk",
        help="Extract PDF text and print traceable segments as JSON Lines.",
    )
    chunk.add_argument("pdf_path", help="Path to the PDF file.")
    chunk.add_argument(
        "--document-id",
        dest="document_id",
        help="Override the generated document id.",
    )
    import_document = subcommands.add_parser(
        "import-document",
        help="Import a PDF into the v1 document/version/node tables.",
    )
    import_document.add_argument("pdf_path", help="Path to the PDF file.")
    import_document.add_argument(
        "--db",
        dest="db_path",
        help="Path to the SQLite database file.",
    )
    import_document.add_argument(
        "--manifest",
        required=True,
        help="Path to the document import manifest JSON.",
    )
    export_document_nodes = subcommands.add_parser(
        "export-document-nodes",
        help="Export deterministic v1 document nodes for later extraction.",
    )
    export_document_nodes.add_argument(
        "--db",
        dest="db_path",
        help="Path to the SQLite database file.",
    )
    export_document_nodes.add_argument(
        "--document-version-id",
        required=True,
        help="Document version id to export.",
    )
    export_document_nodes.add_argument(
        "--output",
        required=True,
        help="Path to the JSON export package.",
    )
    build_statement_prompt = subcommands.add_parser(
        "build-statement-candidate-prompt",
        help="Erzeuge ein deterministisches Frontier-Promptpaket aus Dokumentknoten.",
    )
    build_statement_prompt.add_argument("--document-nodes-export", required=True)
    build_statement_prompt.add_argument("--output-dir", required=True)
    build_statement_prompt.add_argument("--model", required=True)
    validate_statement_candidates = subcommands.add_parser(
        "validate-statement-candidates",
        help="Validiere rohe Aussagekandidaten gegen Schema und zitierfähige Quellen.",
    )
    validate_statement_candidates.add_argument("--db", dest="db_path")
    validate_statement_candidates.add_argument("--document-nodes-export", required=True)
    validate_statement_candidates.add_argument("--raw-output", required=True)
    import_statement_candidates = subcommands.add_parser(
        "import-statement-candidates",
        help="Importiere validierte Aussagekandidaten in das relationale Modell.",
    )
    import_statement_candidates.add_argument("--db", dest="db_path")
    import_statement_candidates.add_argument("--document-nodes-export", required=True)
    import_statement_candidates.add_argument("--raw-output", required=True)
    import_statement_candidates.add_argument("--model", required=True)
    import_statement_candidates.add_argument("--prompt", required=True)
    export_statement_review = subcommands.add_parser(
        "export-statement-review",
        help="Exportiere ein deterministisches Prüfpaket für Aussagekandidaten.",
    )
    export_statement_review.add_argument("--db", dest="db_path")
    export_statement_review.add_argument("--extraction-run-id", required=True)
    export_statement_review.add_argument("--output", required=True)
    find_evidence_parser = subcommands.add_parser(
        "find-evidence",
        help="Read a schema-v4 Clean Set using a validated SearchIntent JSON object.",
    )
    find_evidence_parser.add_argument(
        "intent_json",
        help="Strict SearchIntent JSON object; SQL and shell fragments are not accepted.",
    )
    find_evidence_parser.add_argument(
        "--db",
        dest="db_path",
        required=True,
        help="Path to the schema-v4 SQLite database (opened read-only).",
    )
    find_evidence_parser.add_argument(
        "--text-dir",
        dest="text_dir",
        help="Allowlisted local text artifacts for an explicit rg control/fallback path.",
    )
    find_evidence_parser.add_argument(
        "--no-rg",
        dest="no_rg",
        action="store_true",
        help="Use the controlled Python literal-search fallback instead of ripgrep.",
    )
    ask_local = subcommands.add_parser(
        "ask-local",
        help="Use local qwen3-14b to plan controlled evidence retrieval.",
    )
    ask_local.add_argument("question", help="Natural-language question.")
    ask_local.add_argument(
        "--db",
        dest="db_path",
        required=True,
        help="Path to the schema-v4 SQLite database (opened read-only).",
    )
    ask_local.add_argument(
        "--output-dir",
        dest="output_dir",
        required=True,
        help=(
            "External directory for one complete versioned JSON run protocol."
        ),
    )
    ask_local.add_argument(
        "--project-id",
        required=True,
        help="Stable project identifier stored in the evaluation context.",
    )
    ask_local.add_argument(
        "--question-id",
        required=True,
        help="Stable question identifier stored in the evaluation context.",
    )
    ask_local.add_argument(
        "--model-artifact",
        required=True,
        help="Exact local model artifact designation including quantization.",
    )
    ask_local.add_argument(
        "--model-quantization",
        required=True,
        help="Exact quantization identifier contained in --model-artifact.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = Settings.from_env()

    if args.version:
        from info_system import __version__

        print(__version__)
        return 0

    if args.command == "init-db":
        db_path = Path(args.db_path or settings.database_path)
        initialize_database(db_path)
        print(f"initialized database: {db_path}")
        return 0

    if args.command == "find-evidence":
        try:
            intent = SearchIntent.from_dict(json.loads(args.intent_json))
            text_dir = Path(args.text_dir) if args.text_dir is not None else None
            package = find_evidence(
                Path(args.db_path),
                intent,
                tool_text_dir=text_dir,
                project_root=(
                    _project_root_for_text_dir(text_dir)
                    if text_dir is not None
                    else None
                ),
                prefer_rg=not args.no_rg,
            )
        except (
            json.JSONDecodeError,
            SearchIntentError,
            EvidenceRetrievalError,
        ) as error:
            print(str(error), file=sys.stderr)
            return 1
        print(json.dumps(package.to_dict(), ensure_ascii=False, sort_keys=True))
        return 0

    if args.command == "ask-local":
        try:
            run_arguments: dict[str, object] = {
                "db_path": Path(args.db_path),
                "question": args.question,
                "output_dir": Path(args.output_dir),
                "project_id": args.project_id,
                "question_id": args.question_id,
                "model_artifact": args.model_artifact,
                "model_quantization": args.model_quantization,
            }
            result = run_local_question(
                **run_arguments,
            )
        except LocalLlmError as error:
            print(str(error), file=sys.stderr)
            return 1
        print(f"local_llm_endpoint: {result.endpoint}")
        print(f"local_llm_model: {result.model}")
        print(
            "search_intent: "
            + json.dumps(
                result.search_intent.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        print(f"evidence_package_schema: {result.evidence_package.schema_version}")
        print(result.answer_text)
        if result.run_protocol_path is not None:
            print(f"run_protocol: {result.run_protocol_path}")
        return 0

    if args.command == "extract-text":
        try:
            pages = extract_text_from_pdf(Path(args.pdf_path))
        except PdfExtractionError as error:
            print(str(error), file=sys.stderr)
            return 1
        for page in pages:
            print(f"--- page {page.page_number} ---")
            print(page.text)
        return 0

    if args.command == "chunk":
        pdf_path = Path(args.pdf_path)
        try:
            pages = extract_text_from_pdf(pdf_path)
        except PdfExtractionError as error:
            print(str(error), file=sys.stderr)
            return 1
        document_id = args.document_id or document_id_from_path(pdf_path)
        for segment in segment_pages(pages, document_id=document_id):
            print(json.dumps(segment.to_dict(), ensure_ascii=False))
        return 0

    if args.command == "import-document":
        db_path = Path(args.db_path or settings.database_path)
        try:
            manifest = load_manifest(Path(args.manifest))
            result = import_document_nodes_from_pdf(
                Path(args.pdf_path),
                db_path=db_path,
                manifest=manifest,
            )
        except (DocumentPipelineError, PdfExtractionError) as error:
            print(str(error), file=sys.stderr)
            return 1
        print(f"imported document: {result.document_id}")
        print(f"document_version_id: {result.document_version_id}")
        print(f"document_sha256: {result.document_sha256}")
        print(f"pages: {result.page_count}")
        print(f"nodes: {result.node_count}")
        print(f"citable nodes: {result.citable_node_count}")
        print(f"fts5 rows: {result.fts5_row_count}")
        print(f"database: {db_path}")
        return 0

    if args.command == "export-document-nodes":
        db_path = Path(args.db_path or settings.database_path)
        try:
            package = write_export_package(
                db_path,
                document_version_id=args.document_version_id,
                output_path=Path(args.output),
            )
        except DocumentPipelineError as error:
            print(str(error), file=sys.stderr)
            return 1
        print(f"exported document_version_id: {package['document_version_id']}")
        print(f"nodes: {len(package['nodes'])}")
        print(f"package_sha256: {package['package_sha256']}")
        print(f"output: {Path(args.output)}")
        return 0

    if args.command == "build-statement-candidate-prompt":
        try:
            document_nodes_export = _read_json_object(Path(args.document_nodes_export))
            result = write_statement_candidate_prompt_package(
                document_nodes_export,
                output_dir=Path(args.output_dir),
                model_name=args.model,
            )
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            ValidationError,
            DocumentPipelineError,
        ) as error:
            print(str(error), file=sys.stderr)
            return 1
        print(f"output_dir: {result.output_dir}")
        print(f"input_sha256: {result.input_sha256}")
        print(f"prompt_sha256: {result.prompt_sha256}")
        print(f"request_payload_sha256: {result.request_payload_sha256}")
        return 0

    if args.command == "validate-statement-candidates":
        db_path = Path(args.db_path or settings.database_path)
        try:
            document_nodes_export = _read_json_object(Path(args.document_nodes_export))
            raw_output = Path(args.raw_output).read_text(encoding="utf-8")
            candidate_package = json.loads(raw_output)
            validate_statement_candidate_package(
                candidate_package,
                db_path=db_path,
                document_nodes_export=document_nodes_export,
            )
        except (
            OSError,
            json.JSONDecodeError,
            DocumentPipelineError,
            StatementCandidateError,
            ValidationError,
        ) as error:
            print(str(error), file=sys.stderr)
            return 1
        print("valid: true")
        return 0

    if args.command == "import-statement-candidates":
        db_path = Path(args.db_path or settings.database_path)
        try:
            document_nodes_export = _read_json_object(Path(args.document_nodes_export))
            raw_output = Path(args.raw_output).read_text(encoding="utf-8")
            prompt_text = Path(args.prompt).read_text(encoding="utf-8")
            result = import_statement_candidate_package(
                db_path,
                raw_output_json=raw_output,
                document_nodes_export=document_nodes_export,
                model_name=args.model,
                prompt_text=prompt_text,
            )
        except (
            OSError,
            json.JSONDecodeError,
            DocumentPipelineError,
            StatementCandidateError,
            ValidationError,
        ) as error:
            print(str(error), file=sys.stderr)
            return 1
        print(f"extraction_run_id: {result.extraction_run_id}")
        print(f"raw_output_sha256: {result.raw_output_sha256}")
        print(f"candidates: {result.candidate_count}")
        print(f"statements: {result.imported_statement_count}")
        print(f"status: {result.status}")
        return 0

    if args.command == "export-statement-review":
        db_path = Path(args.db_path or settings.database_path)
        try:
            package = export_statement_review_package(
                db_path,
                extraction_run_id=args.extraction_run_id,
            )
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                json.dumps(
                    package,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
        except (OSError, StatementCandidateError) as error:
            print(str(error), file=sys.stderr)
            return 1
        print(f"extraction_run_id: {package['extraction_run_id']}")
        print(f"candidates: {len(package['candidates'])}")
        print(f"package_sha256: {package['package_sha256']}")
        print(f"output: {output_path}")
        return 0

    parser.print_help()
    return 0


def _project_root_for_text_dir(text_dir: Path) -> Path:
    resolved = text_dir.resolve()
    if (
        resolved.name == "text"
        and resolved.parent.name == "processed"
        and resolved.parent.parent.name == "data"
    ):
        return resolved.parent.parent.parent
    return resolved.parent


def _read_json_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value
