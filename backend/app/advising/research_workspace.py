"""Conversation-private work products and paged archive reads, never source facts."""

import re
from uuid import UUID, uuid5

from app.db import queries
from app.util.digest import canonical_json_bytes


class ConversationWorkspace:
    def __init__(self, conn, scope, store, conversation_id):
        self.conn, self.scope, self.store = conn, scope, store
        self.conversation_id = conversation_id

    def params(self):
        return {"conversation_id": self.conversation_id,
                "workspace_key": self.scope.workspace_key,
                "collection_id": self.scope.collection_id}

    def checkpoint(self, issue_handle, *, max_bytes):
        row = self.conn.execute(queries.get("research_conversation_checkpoint"),
                                self.params()).fetchone()
        self.conn.commit()
        if row is None:
            return None
        prior = self.store.get(row["run_id"])
        records = self.store.evidence(prior.run_id)
        # Old run-local eN identifiers are never sent to the next investigation.
        # A recall handle is only a lead; reopening it acquires current evidence.
        recalls = {}
        for record in records:
            pin = self.scope.sources.get(UUID(record.source_id))
            if pin and (pin.index_revision, pin.metadata_revision, pin.source_kind) == (
                    record.source_revision, record.metadata_revision, record.source_kind):
                recalls[record.evidence_id] = issue_handle(
                    "memory", (pin.source_id, UUID(record.chunk_id)))
        old_programs = {p[0]: p for p in row["scope_manifest"]["programs"]}
        old_sources = {p[0]: p for p in row["scope_manifest"]["sources"]}
        wanted = {h for candidate in prior.state.candidates for h in candidate.program_handles}
        handles = {}
        for pid, pin in self.scope.programs.items():
            old = "h_" + uuid5(prior.run_id, f"program:{pid}").hex
            source = self.scope.sources[pin.source_id]
            if (old in wanted and old_sources.get(str(pin.source_id)) == [
                    str(pin.source_id), source.index_revision, source.source_kind, source.metadata_revision]
                    and old_programs.get(str(pid)) == [str(pid), str(pin.source_id),
                    pin.source_index_revision, pin.parsed_content_digest, pin.binding_status]):
                handles[old] = issue_handle("program", pid)
        notebook = prior.state.model_dump(mode="json")
        notebook.pop("revision")
        unavailable = 0

        def rebase(item):
            nonlocal unavailable
            ids = item.pop("evidence_ids", [])
            missing = any(eid not in recalls for eid in ids)
            unavailable += sum(eid not in recalls for eid in ids)
            item["recall_handles"] = [recalls[eid] for eid in ids if eid in recalls]
            return missing

        for name in ("criteria", "candidates", "notes", "plan"):
            kept = []
            for item in notebook[name]:
                missing = rebase(item)
                if missing and (name == "candidates" or item.get("basis") in {
                        "career_evidence", "evidence_summary"}):
                    continue
                if name == "candidates":
                    if any(h not in handles for h in item["program_handles"]):
                        unavailable += 1
                        continue
                    item["program_handles"] = [handles[h] for h in item["program_handles"]]
                    item["status"] = "investigating"
                if name == "plan" and missing:
                    item["status"] = "pending"
                kept.append(item)
            notebook[name] = kept
        notebook["recall_handles"] = [recalls[eid] for eid in
                                       notebook.pop("active_evidence_ids") if eid in recalls]
        result = {"run_id": str(prior.run_id), "notebook": notebook,
                  "unavailable_references": unavailable, "omitted_items": 0}
        # Rebound handles are longer than eN IDs. Drop whole work items if their
        # larger representation exceeds the notebook allowance; disclose omission.
        for name in ("recall_handles", "notes", "plan", "candidates", "criteria", "student_context", "open_questions"):
            while len(canonical_json_bytes(result)) > max_bytes and notebook[name]:
                notebook[name].pop()
                result["omitted_items"] += 1
        if len(canonical_json_bytes(result)) > max_bytes:
            return {"run_id": str(prior.run_id), "omitted": True}
        return result

    def references(self, query="", offset=0):
        rows = self.conn.execute(queries.get("research_conversation_references"), {
            **self.params(), "source_ids": list(self.scope.sources),
            "query": query, "offset": offset}).fetchall()
        self.conn.commit()
        return rows[:12], offset + 12 if len(rows) > 12 else None

    def turns(self, query="", *, sequence=1, offset=0):
        """Read a byte-bounded page, retaining a cursor inside an oversized turn."""
        words = list(dict.fromkeys(re.findall(r"\w+", query)))
        if query.strip() and not words:
            return [], None
        rows = self.conn.execute(queries.get("research_conversation_turns"), {
            **self.params(), "query": " OR ".join(words),
            "sequence": sequence}).fetchall()
        self.conn.commit()
        turns, next_position = [], None
        for row in rows:
            start = offset if row["sequence"] == sequence else 0
            if len(turns) == 6:
                next_position = (row["sequence"], start)
                break
            remaining_text = row["text"][start:]
            def entry(length, row=row, start=start, text=remaining_text):
                return {"sequence": row["sequence"], "role": row["role"],
                        "text": text[:length], "text_offset": start,
                        "complete": length == len(text)}
            if len(canonical_json_bytes([*turns, entry(0)])) > 6000:
                next_position = (row["sequence"], start)
                break
            # Bound serialized content too: quotes, backslashes and controls can
            # expand substantially in JSON. A character cursor preserves every
            # byte of the original report across those smaller pages.
            low, high = 0, min(len(remaining_text), 6000)
            while low < high:
                middle = (low + high + 1) // 2
                if len(canonical_json_bytes([*turns, entry(middle)])) <= 6000:
                    low = middle
                else:
                    high = middle - 1
            if low == 0 and remaining_text:
                next_position = (row["sequence"], start)
                break
            turns.append(entry(low))
            if low < len(remaining_text):
                end = start + low
                next_position = (row["sequence"], end)
                break
        return turns, next_position
