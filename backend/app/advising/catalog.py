"""Read-only, citation-scoped course and topic context for advising turns."""

import re
from collections import defaultdict
from collections.abc import Iterable
from typing import Any
from uuid import UUID

from app.atlas.requisites import AllOf, AnyOf, Condition, CourseRef, Req
from app.db import queries
from app.retrieval.types import CitedChunk

_MAX_TREE_NODES = 2_000
_MAX_TREE_DEPTH = 64
_DESCRIPTION_REQUIREMENT_RE = re.compile(r"\b(?:pre|co)[ -]?requisites?\s*:", re.IGNORECASE)


def _restore_tree(rows: list[dict[str, Any]]) -> Req:
    """Rebuild one persisted parser tree, rejecting malformed or partial data."""
    if not rows or len(rows) > _MAX_TREE_NODES:
        raise ValueError("prerequisite tree is empty or exceeds the supported size")
    kinds = {row["kind"] for row in rows}
    if len(kinds) != 1 or not kinds.issubset({"prerequisite", "corequisite", "recommended"}):
        raise ValueError("prerequisite tree has an unknown or mixed node kind")
    by_id = {row["node_id"]: row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError("duplicate prerequisite node id")
    roots = [row for row in rows if row["parent_node_id"] is None]
    if len(roots) != 1:
        raise ValueError("prerequisite tree must have exactly one root")

    children: dict[UUID, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if (not isinstance(row["position"], int) or isinstance(row["position"], bool)
                or row["position"] < 0):
            raise ValueError("prerequisite node has an invalid position")
        parent_id = row["parent_node_id"]
        if parent_id is not None:
            parent = by_id.get(parent_id)
            if parent is None or parent["kind"] != row["kind"]:
                raise ValueError("prerequisite node has an invalid parent")
            children[parent_id].append(row)
    for parent_id, values in children.items():
        values.sort(key=lambda row: (row["position"], str(row["node_id"])))
        if [row["position"] for row in values] != list(range(len(values))):
            raise ValueError("prerequisite child positions are incomplete")

    visited: set[UUID] = set()

    def build(row: dict[str, Any], depth: int) -> Req:
        if depth > _MAX_TREE_DEPTH:
            raise ValueError("prerequisite tree exceeds the supported depth")
        node_id = row["node_id"]
        if node_id in visited:
            raise ValueError("prerequisite tree contains a cycle or repeated node")
        visited.add(node_id)
        node_type = row["node_type"]
        descendants = children.get(node_id, [])
        if node_type == "course":
            if not isinstance(row["target_code"], str) or not row["target_code"].strip():
                raise ValueError("course node has no target code")
            if row["condition_text"] is not None or descendants:
                raise ValueError("course node has invalid payload or children")
            return CourseRef(row["target_code"])
        if node_type == "condition":
            if not isinstance(row["condition_text"], str) or not row["condition_text"].strip():
                raise ValueError("condition node has no condition text")
            if row["target_code"] is not None or descendants:
                raise ValueError("condition node has invalid payload or children")
            return Condition(row["condition_text"])
        if node_type in {"all", "any"}:
            if row["target_code"] is not None or row["condition_text"] is not None:
                raise ValueError("group node has a leaf payload")
            if len(descendants) < 2:
                raise ValueError("group node needs at least two children")
            items = tuple(build(child, depth + 1) for child in descendants)
            return AllOf(items) if node_type == "all" else AnyOf(items)
        raise ValueError("unknown prerequisite node type")

    root = roots[0]
    if root["position"] != 0:
        raise ValueError("prerequisite root position is invalid")
    tree = build(root, 1)
    if len(visited) != len(rows):
        raise ValueError("prerequisite tree contains unreachable nodes")
    return tree


def _tree_rows_by_course(rows: Iterable[dict[str, Any]]) -> dict[UUID, dict[str, list[dict]]]:
    grouped: dict[UUID, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        grouped[row["course_id"]][row["kind"]].append(row)
    return grouped


def _normalized_text(text: str) -> str:
    return " ".join(text.split())


def _contains_phrase(text: str, phrase: str) -> bool:
    """Match normalized requisite text as whole words, not code prefixes."""
    start = 0
    while True:
        found = text.find(phrase, start)
        if found < 0:
            return False
        end = found + len(phrase)
        left_boundary = found == 0 or not (text[found - 1].isalnum() or text[found - 1] == "_")
        right_boundary = end == len(text) or not (text[end].isalnum() or text[end] == "_")
        if left_boundary and right_boundary:
            return True
        start = found + 1


def _merge_overlapping_texts(texts: list[str]) -> str:
    """Join adjacent chunks, removing only an exact whitespace-token overlap."""
    result: list[str] = []
    for text in texts:
        tokens = text.split()
        if not tokens:
            continue
        overlap = 0
        for size in range(min(len(result), len(tokens)), 0, -1):
            if result[-size:] == tokens[:size]:
                overlap = size
                break
        result.extend(tokens[overlap:])
    return " ".join(result)


def _requirements_are_cited(raw: str | None, chunks: list[CitedChunk]) -> bool:
    """Check requisite coverage using only cited text from this course locator.

    Runs are joined only across consecutive chunk indexes. Missing chunks and
    ambiguous duplicate indexes therefore cannot manufacture coverage.
    """
    if raw is None or not raw.strip():
        return True
    target = _normalized_text(raw)
    by_index: dict[int, set[str]] = defaultdict(set)
    for chunk in chunks:
        normalized = _normalized_text(chunk.text)
        if normalized:
            by_index[chunk.chunk_index].add(normalized)
    if not by_index:
        return False

    # Different text for one index is ambiguous evidence; retain only standalone
    # matches and do not join that index to a neighboring chunk.
    standalone = any(
        len(texts) == 1 and _contains_phrase(next(iter(texts)), target)
        for texts in by_index.values()
    )
    if standalone:
        return True
    ordered = sorted(by_index.items())
    run: list[str] = []
    previous: int | None = None
    for index, alternatives in ordered:
        if previous is not None and index != previous + 1:
            # Validate the completed run on its own before resetting it; whether
            # the following index is ambiguous must not discard valid evidence.
            if run and _contains_phrase(_merge_overlapping_texts(run), target):
                return True
            run = []
        if len(alternatives) == 1:
            run.append(next(iter(alternatives)))
        else:
            # Do not use a conflicting chunk in a stitched text run.
            run = []
        previous = index
    return bool(run) and _contains_phrase(_merge_overlapping_texts(run), target)


def load_advising_context(
    conn,
    *,
    collection_id: UUID,
    chunks: list[CitedChunk],
) -> dict[str, list[dict[str, Any]]]:
    """Load only atlas records whose exact source and locator are cited already.

    Citation numbers are one-based positions in the supplied retrieved context.
    The function only performs SELECTs; all graph-derived topic details are
    limited to the grounded courses in the supplied citation scope.
    """
    empty = {"courses": [], "topic_groups": []}
    if not chunks:
        return empty

    # Resolve each cited chunk to its locator. Retrieval relabels a cited
    # cross-listed course with all of its official codes, so the rendered display
    # label is not a stable key and matching on it dropped those courses out of
    # this snapshot. The query prefers the chunk's own locator identity and uses
    # the label only when no chunk row survives for that citation. All arrays
    # below stay parallel (they are zipped row by row).
    chunk_rows = conn.execute(
        queries.get("advising_chunk_locators"),
        {
            "chunk_ids": [chunk.chunk_id for chunk in chunks],
            "source_ids": [chunk.source_id for chunk in chunks],
            "locator_labels": [chunk.locator_label for chunk in chunks],
        },
    ).fetchall()
    chunk_locator: dict[UUID, UUID] = {
        row["chunk_id"]: row["locator_id"] for row in chunk_rows
    }
    resolved = [chunk for chunk in chunks if chunk.chunk_id in chunk_locator]
    citations_by_locator: dict[tuple[UUID, UUID], set[int]] = defaultdict(set)
    for citation_number, chunk in enumerate(chunks, start=1):
        locator_id = chunk_locator.get(chunk.chunk_id)
        if locator_id is not None:
            citations_by_locator[(chunk.source_id, locator_id)].add(citation_number)
    if not citations_by_locator:
        return empty
    params = {
        "collection_id": collection_id,
        "source_ids": [chunk.source_id for chunk in resolved],
        "locator_ids": [chunk_locator[chunk.chunk_id] for chunk in resolved],
    }

    course_rows = conn.execute(
        queries.get("list_advising_courses_for_scope"), params
    ).fetchall()
    if not course_rows:
        return empty

    node_rows = conn.execute(
        queries.get("list_advising_nodes_for_scope"), params
    ).fetchall()
    nodes_by_course = _tree_rows_by_course(node_rows)
    courses: list[dict[str, Any]] = []
    for row in course_rows:
        course_id = row["course_id"]
        stored_status = row["parse_status"]
        raw = row["requisites_raw"]
        locator_chunks = [
            chunk for chunk in chunks
            if chunk.source_id == row["source_id"]
            and chunk_locator.get(chunk.chunk_id) == row["locator_id"]
        ]
        requirements_complete = _requirements_are_cited(raw, locator_chunks)
        # Some upstream records put explicit requirements only in the description.
        # A missing structured field must not turn that into verified no requirements.
        description_requirement_unparsed = not (raw and raw.strip()) and (
            row.get("description_has_requirements", False) or any(
                _DESCRIPTION_REQUIREMENT_RE.search(chunk.text) for chunk in locator_chunks)
        )
        if description_requirement_unparsed:
            requirements_complete = False
        nodes = nodes_by_course.get(course_id, {})
        trees: dict[str, Req] = {}
        valid = True
        try:
            if stored_status == "none":
                valid = not (raw and raw.strip()) and not nodes
            elif stored_status == "parsed":
                if not raw or not raw.strip() or not nodes:
                    raise ValueError("parsed requisite record is missing its tree")
                for kind, tree_nodes in nodes.items():
                    trees[kind] = _restore_tree(tree_nodes)
                if not trees:
                    raise ValueError("parsed requisite record has no trees")
            else:
                valid = False
        except (KeyError, TypeError, ValueError):
            valid = False

        if not requirements_complete:
            status = "unparsed"
        elif stored_status in {"ambiguous", "unparsed"}:
            status = stored_status
        else:
            status = stored_status if valid else "unparsed"
        if not valid or not requirements_complete:
            trees = {}
        hold_id = row.get("requirement_hold_id")
        requirement_hold = None if hold_id is None else {
            "hold_id": hold_id,
            "reason": row["requirement_hold_reason"],
            "master_file_hash": row["requirement_hold_master_file_hash"],
            "evidence_capture_sha256": row["requirement_hold_capture_sha256"],
            "evidence_capture_path": row["requirement_hold_capture_path"],
            "evidence_source_id": row["requirement_hold_source_id"],
            "evidence_index_revision": row["requirement_hold_index_revision"],
            "evidence_capture_set_hash": row["requirement_hold_capture_set_hash"],
        }
        if requirement_hold is not None:
            # Keep the cited master wording reviewable, but do not expose its
            # parser tree as a verified requirement while source evidence conflicts.
            status = "ambiguous"
            requirements_complete = False
            trees = {}
        citations = tuple(sorted(citations_by_locator[(row["source_id"], row["locator_id"])]))
        courses.append({
            "course_id": course_id,
            "code": row["code"],
            "title": row["title"],
            "source_id": row["source_id"],
            "locator_label": row["locator_label"],
            "citation_numbers": citations,
            "prerequisites": trees.get("prerequisite"),
            "corequisites": trees.get("corequisite"),
            "parse_status": status,
            "stored_parse_status": stored_status,
            "lifecycle": row["lifecycle"],
            "trees_valid": valid and requirements_complete,
            "requirements_evidence_complete": requirements_complete,
            "blocker_reason": (
                "source_conflict" if requirement_hold is not None else
                None if requirements_complete else "requirements_evidence_missing"
            ),
            "requisites_raw": raw if requirements_complete or requirement_hold is not None else None,
            "requirement_hold": requirement_hold,
        })

    topic_rows = conn.execute(
        queries.get("list_advising_topic_paths_for_scope"), params
    ).fetchall()
    groups: dict[tuple[UUID, tuple[str, ...]], dict[str, Any]] = {}
    citations_by_course = {row["course_id"]: row["citation_numbers"] for row in courses}
    for row in topic_rows:
        path = tuple(row["path"] or ())
        if not path:
            continue
        key = (row["cluster_id"], path)
        group = groups.setdefault(key, {
            "path": path,
            "cluster_id": row["cluster_id"],
            "course_codes": [],
            "total_size": row["total_size"],
            "evidence": [],
        })
        group["course_codes"].append(row["code"])
        group["evidence"].append({
            "course_code": row["code"],
            "citation_numbers": citations_by_course[row["course_id"]],
        })

    topic_groups = sorted(groups.values(), key=lambda group: (group["path"], str(group["cluster_id"])))
    for group in topic_groups:
        group["course_codes"] = tuple(sorted(set(group["course_codes"])))
        group["evidence"] = tuple(sorted(group["evidence"], key=lambda item: item["course_code"]))
    return {"courses": courses, "topic_groups": topic_groups}
