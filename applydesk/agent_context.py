"""Versioned inputs and draft artifacts for the single-agent workflow."""

import hashlib
import json
from pathlib import Path

from .profile import model_profile


def screening_profile(workspace, profile, resume_choice):
    """Give screening the confirmed profile and the resume originals available for this request."""
    context = model_profile(profile)
    context["available_resume_categories"] = workspace.resumes.categories()
    if resume_choice is None:
        context["resumes"] = resume_sources(workspace)
    else:
        selected = workspace.resumes.selected(resume_choice["id"], resume_choice["sha256"])
        context["resumes"] = [source_record(workspace, selected["id"])]
    return context


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
    if not job or job.get("removed_at") or job["status"] == "submitted" or job.get("manual_status") in {"submitted", "skipped"}:
        raise ValueError("Choose an active, unsubmitted application.")
    template_id = workspace.settings().get("cover_template_id")
    template = source_record(workspace, template_id) if template_id else None
    if template and (template["kind"] != "cover_template" or not template.get("confirmed")):
        template = None
    return {"profile": profile, "job": {key: job.get(key, "") for key in ("id", "company", "title", "url", "details")},
            "resume_categories": workspace.resumes.categories(), "resumes": resume_sources(workspace), "template": template}


def resume_sources(workspace):
    """Give the agent verified contents of active uploaded resumes."""
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
    return {"documents": documents, "draft": draft, "expected_checks": {"layout": layout, "facts": audit}}


def task_context(workspace, task):
    """Changed input or artifact bytes invalidate a prior draft and all its reviews."""
    inputs = inputs_for(workspace, task)
    material = review_material(workspace, task)
    template = inputs.get("template")
    if template and (task["kind"] == "document" or (task.get("result") or {}).get("cover_requirement", {}).get("state") == "required"):
        material["expected_checks"]["layout"] += [f"template:page:{i + 1}" for i, _ in enumerate(template["image_paths"])]
    current = digest(inputs)
    draft_version = digest({"inputs": inputs, "material": material, "draft_revision": task["draft_revision"]})
    version = draft_version
    stale = draft_changed(task, inputs, material, current, version)
    if task["kind"] == "application" and task.get("result"):
        job = workspace.store.get("job", task["target_id"])
        attached = [identity for identity in (job.get("resume_id"), job.get("cover_id")) if identity]
        if task.get("draft_version"):
            attached = [job.get("resume_id")]
        if attached != task["document_ids"]:
            version = digest({"version": draft_version, "attachments": attached})
            stale = True
    if task["status"] == "needs_input" and task.get("blocked_version"):
        # A failed rework belongs to the inputs it tried, independently of the last saved draft.
        stale = task["blocked_version"] != version
    action = "await_user"
    if task["status"] == "queued" or not task.get("result") or stale:
        action = "draft"
    if task["status"] == "needs_input" and not stale:
        action = "resolve_input"
    if task["kind"] == "document":
        task = {**task, "status": "needs_input", "issues": ["Letter customization is unavailable."]}
        action, stale = "resolve_input", False
    status = "draft_ready" if task["status"] in {"awaiting_review", "reviewed_draft"} else task["status"]
    return {"task": {**task, "status": "stale" if stale and status != "queued" else status}, "inputs": inputs,
            "input_version": current, "version": version, "draft_version": draft_version, "review": material,
            "next_action": action, "draft_only": True}


def draft_changed(task, inputs, material, current, version):
    """Keep legacy draft validity checks without scheduling retired reviewer agents."""
    if task.get("input_version") and current != task["input_version"]:
        return True
    if task.get("draft_version"):
        return task["draft_version"] != version
    checks = material["expected_checks"]
    legacy = {**material, "expected_checks": {"document_reviewer": checks["layout"], "application_auditor": checks["facts"]}}
    previous = digest({"inputs": inputs, "material": legacy, "draft_revision": task["draft_revision"]})
    return any(review["version"] != previous for review in task.get("reviews", {}).values())


def guide():
    """The bundled skill is inspectable guidance, not an override of host instructions."""
    return {"protocol": "applydesk.chat.v1", "draft_only": True,
            "instructions": (Path(__file__).parent / "CHAT_SKILL.md").read_text()}
