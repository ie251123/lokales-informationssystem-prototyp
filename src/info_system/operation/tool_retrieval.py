from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class ToolSearchHit:
    tool_name: str
    search_pattern: str
    document_id: str | None
    segment_id: str | None
    source_path: str
    page_number: int | None
    line_number: int
    context_before: str | None
    matched_text: str
    context_after: str | None


class ToolSearchError(RuntimeError):
    pass


class ToolSearchRunner:
    def __init__(
        self,
        *,
        allowed_paths: list[str | Path],
        project_root: str | Path,
        prefer_rg: bool = True,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.allowed_paths = [_resolve_path(path) for path in allowed_paths]
        self.project_root = _resolve_path(project_root)
        self.prefer_rg = prefer_rg
        self.timeout_seconds = timeout_seconds

    def search(
        self,
        pattern: str,
        *,
        search_path: str | Path,
        max_results: int,
    ) -> list[ToolSearchHit]:
        checked_path = self._validated_search_path(search_path)
        if max_results <= 0 or not pattern:
            return []

        if self.prefer_rg and shutil.which("rg"):
            rg_hits = self._search_with_rg(
                pattern,
                search_path=checked_path,
                max_results=max_results,
            )
            if rg_hits is not None:
                return rg_hits

        return self._search_with_python(
            pattern,
            search_path=checked_path,
            max_results=max_results,
        )

    def _validated_search_path(self, search_path: str | Path) -> Path:
        resolved = _resolve_path(search_path)
        if not any(_is_relative_to(resolved, allowed) for allowed in self.allowed_paths):
            raise ToolSearchError(f"search path is not in allowed search paths: {resolved}")
        return resolved

    def _search_with_rg(
        self,
        pattern: str,
        *,
        search_path: Path,
        max_results: int,
    ) -> list[ToolSearchHit] | None:
        command = [
            "rg",
            "--fixed-strings",
            "--ignore-case",
            "--line-number",
            "--json",
            "--glob",
            "*.txt",
            "--",
            pattern,
            str(search_path),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
                timeout=self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None

        if completed.returncode not in {0, 1}:
            return None

        hits: list[ToolSearchHit] = []
        for output_line in completed.stdout.splitlines():
            parsed = _parse_rg_json_line(output_line)
            if parsed is None:
                continue
            file_path, line_number, matched_text = parsed
            try:
                hit = self._hit_from_line(
                    file_path,
                    line_number=line_number,
                    matched_text=matched_text,
                    pattern=pattern,
                    tool_name="rg",
                )
            except ToolSearchError:
                continue
            hits.append(hit)
            if len(hits) >= max_results:
                break
        return hits

    def _search_with_python(
        self,
        pattern: str,
        *,
        search_path: Path,
        max_results: int,
    ) -> list[ToolSearchHit]:
        matcher = re.compile(re.escape(pattern), flags=re.IGNORECASE)
        hits: list[ToolSearchHit] = []
        for file_path in sorted(search_path.rglob("*.txt")):
            try:
                lines = file_path.read_text(
                    encoding="utf-8-sig",
                    errors="strict",
                ).splitlines()
            except UnicodeDecodeError:
                continue
            for index, line in enumerate(lines, start=1):
                if matcher.search(line) is None:
                    continue
                hits.append(
                    self._hit_from_lines(
                        file_path,
                        lines=lines,
                        line_number=index,
                        matched_text=line,
                        pattern=pattern,
                        tool_name="python_regex",
                    )
                )
                if len(hits) >= max_results:
                    return hits
        return hits

    def _hit_from_line(
        self,
        file_path: Path,
        *,
        line_number: int,
        matched_text: str,
        pattern: str,
        tool_name: str,
    ) -> ToolSearchHit:
        try:
            lines = file_path.read_text(
                encoding="utf-8-sig",
                errors="strict",
            ).splitlines()
        except UnicodeDecodeError as error:
            raise ToolSearchError(f"could not decode text artifact: {file_path}") from error
        return self._hit_from_lines(
            file_path,
            lines=lines,
            line_number=line_number,
            matched_text=matched_text,
            pattern=pattern,
            tool_name=tool_name,
        )

    def _hit_from_lines(
        self,
        file_path: Path,
        *,
        lines: list[str],
        line_number: int,
        matched_text: str,
        pattern: str,
        tool_name: str,
    ) -> ToolSearchHit:
        segment_id, page_number = _segment_for_line(lines, line_number)
        document_id = _document_id_for_file(lines, segment_id)
        return ToolSearchHit(
            tool_name=tool_name,
            search_pattern=pattern,
            document_id=document_id,
            segment_id=segment_id,
            source_path=_relative_posix(file_path, self.project_root),
            page_number=page_number,
            line_number=line_number,
            context_before=_line_at(lines, line_number - 1),
            matched_text=matched_text,
            context_after=_line_at(lines, line_number + 1),
        )


def _parse_rg_json_line(output_line: str) -> tuple[Path, int, str] | None:
    try:
        payload = json.loads(output_line)
    except json.JSONDecodeError:
        return None
    if payload.get("type") != "match":
        return None
    data = payload.get("data", {})
    path_text = data.get("path", {}).get("text")
    line_text = data.get("lines", {}).get("text")
    line_number = data.get("line_number")
    if not path_text or line_text is None:
        return None
    matched_text = line_text.rstrip("\r\n")
    try:
        parsed_line_number = int(line_number)
    except (TypeError, ValueError):
        return None
    return Path(path_text), parsed_line_number, matched_text


def _segment_for_line(lines: list[str], line_number: int) -> tuple[str | None, int | None]:
    for index in range(min(line_number - 1, len(lines) - 1), -1, -1):
        marker = _parse_segment_marker(lines[index])
        if marker is not None:
            return marker
    return None, None


def _parse_segment_marker(line: str) -> tuple[str, int | None] | None:
    if not line.startswith("[") or not line.endswith("]"):
        return None
    segment_match = re.search(r"\bsegment_id=([^\s\]]+)", line)
    if segment_match is None:
        return None
    page_match = re.search(r"\bpage=(\d+)", line)
    page_number = int(page_match.group(1)) if page_match else None
    return segment_match.group(1), page_number


def _document_id_for_file(lines: list[str], segment_id: str | None) -> str | None:
    for line in lines:
        if line.startswith("# document_id:"):
            return line.split(":", 1)[1].strip() or None
    if segment_id and ":" in segment_id:
        return segment_id.split(":", 1)[0]
    return None


def _line_at(lines: list[str], one_based_line_number: int) -> str | None:
    if one_based_line_number < 1 or one_based_line_number > len(lines):
        return None
    value = lines[one_based_line_number - 1]
    return value or None


def _relative_posix(path: Path, project_root: Path) -> str:
    resolved = _resolve_path(path)
    try:
        return resolved.relative_to(project_root).as_posix()
    except ValueError:
        return resolved.as_posix()


def _resolve_path(path: str | Path) -> Path:
    return Path(path).resolve()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True
