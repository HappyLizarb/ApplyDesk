"""Versioned inputs and review coverage for the local chat-only experiment."""

import hashlib
import json
from pathlib import Path

from .profile import model_profile


def digest(value):
    """Fingerprint canonical JSON so results bind to exactly the inputs the agent saw."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def source_record(workspace, identity):
    """Verify original bytes and expose only document content needed for this task."""
    record = workspace.docs.get(identity)
    if record["kind"] in {"document", "resume"}:
        workspace.docs.verify(identity)
    else:
        actual = hashlib.sha256(workspace.docs.asset(record["source_path"]).read_bytes()).hexdigest()
        if actual != record["source_sha256"]:
            raise ValueError("Source file changed; upload it again.")
        if record["kind"].endswith("_template"):
            workspace.docs._verify_template(record)
    allowed = ["id", "kind", "filename", "title", "document_type", "blocks", "text", "source_sha256",
               "sha256", "docx_sha256", "template_id", "image_paths", "source_image_paths", "confirmed", "warnings"]
    result = {key: record[key] for key in allowed if key in record}
    paths = record.get("image_paths", []) + record.get("source_image_paths", [])
    result["image_sha256"] = {path: hashlib.sha256(workspace.docs.asset(path).read_bytes()).hexdigest() for path in paths}
    return result


def inputs_for(workspace, task):
    """Keep passwords and voluntary disclosures outside every agent task context."""
    profile = model_profile(workspace.profile())
    if task["kind"] == "profile":
        record = source_record(workspace, task["target_id"])
        readable = record.get("text", "").strip() or record.get("blocks") or record.get("image_paths")
        if record["kind"] not in {"profile", "resume"} or not readable:
            raise ValueError("Choose an uploaded profile or resume with readable text or page previews.")
        return {"source": record, "profile_revision": profile["revision"]}
    if task["kind"] == "document":
        record = source_record(workspace, task["target_id"])
        if record.get("document_type") != "cover" or record["kind"] != "document":
            raise ValueError("Only a generated cover letter can receive chat edits.")
        return {"profile": profile, "document": record, "instruction": task["instruction"],
                "template": source_record(workspace, record["template_id"])}
    job = workspace.store.get("job", task["target_id"])
    if not job or job.get("removed_at") or job["status"] == "submitted":
        raise ValueError("Choose an active, unsubmitted application.")
    template_id = workspace.settings().get("cover_template_id")
    template = source_record(workspace, template_id) if template_id else None
    if template and (template["kind"] != "cover_template" or not template.get("confirmed")):
        template = None
    return {"profile": profile, "job": {key: job.get(key, "") for key in ("id", "company", "title", "url", "details")},
            "resume_categories": workspace.resumes.categories(), "resumes": resume_sources(workspace), "template": template}


def resume_sources(workspace):
    """Give drafting and screening agents verified contents of active uploaded resumes."""
    records = []
    for row in workspace.resumes.list():
        if not row.get("removed_from_library_at"):
            records.append({**source_record(workspace, row["id"]), "category": row["category"]})
    return records


def review_material(workspace, task):
    """Review current immutable artifacts, with coverage lists supplied by the app."""
    documents = [source_record(workspace, identity) for identity in task.get("document_ids", [])]
    draft = task.get("result") or {}
    audit = [f"requirement:{index}" for index, _ in enumerate(draft.get("analysis", {}).get("required", []))]
    audit += [f"answer:{index}" for index, _ in enumerate(draft.get("answers", []))]
    audit += [f"{doc['id']}:block:{block['id']}" for doc in documents for block in doc.get("blocks", []) if block.get("text", "").strip()]
    audit += ["profile:proposal"] if task["kind"] == "profile" else ["claims:completeness"]
    if task["kind"] == "application":
        audit += ["requirements:completeness", "cover:requirement"]
    layout = [f"{doc['id']}:page:{index + 1}" for doc in documents for index, _ in enumerate(doc["image_paths"])]
    return {"documents": documents, "draft": draft, "expected_checks": {"document_reviewer": layout, "application_auditor": audit}}


def task_context(workspace, task):
    """Changed input or artifact bytes invalidate a prior draft and all its reviews."""
    inputs = inputs_for(workspace, task)
    material = review_material(workspace, task)
    template = inputs.get("template")
    if template and (task["kind"] == "document" or (task.get("result") or {}).get("cover_requirement", {}).get("state") == "required"):
        material["expected_checks"]["document_reviewer"] += [f"template:page:{i + 1}" for i, _ in enumerate(template["image_paths"])]
    current = digest(inputs)
    stale = bool(task.get("input_version") and current != task["input_version"])
    version = digest({"inputs": inputs, "material": material, "draft_revision": task["draft_revision"]})
    stale = stale or any(review["version"] != version for review in task.get("reviews", {}).values())
    if task["kind"] == "application" and task.get("result"):
        job = workspace.store.get("job", task["target_id"])
        attached = [identity for identity in (job.get("resume_id"), job.get("cover_id")) if identity]
        stale = stale or attached != task["document_ids"]
    roles = ["application_auditor"] if task["kind"] == "profile" else ["document_reviewer", "application_auditor"]
    pending = [role for role in roles if not task.get("reviews", {}).get(role, {}).get("passed")]
    action = "review" if pending else "await_user"
    if not task.get("result") or stale:
        action = "draft"
    if task["status"] == "needs_input" and not stale:
        action = "resolve_input"
    return {"task": {**task, "status": "stale" if stale else task["status"]}, "inputs": inputs,
            "input_version": current, "version": version, "review": material,
            "next_action": action, "pending_review_roles": roles if stale else pending,
            "draft_only": True, "review_identity": "App-controlled runs are recorded locally; external reviewer IDs are self-reported."}


def guide():
    """The bundled skill is inspectable guidance, not an override of host instructions."""
    return {"protocol": "applydesk.chat.v1", "draft_only": True,
            "instructions": (Path(__file__).parent / "CHAT_SKILL.md").read_text()}
