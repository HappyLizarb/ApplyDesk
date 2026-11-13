"""Match captured recommendations to the real chat request before adding tracker rows."""

import json

from .agent_context import digest
from .chatgpt_inference import output_text
from .matching_policy import ROLE_FAMILIES
from .recommendation_import import import_selected

RELEVANCE = ROLE_FAMILIES + """Select complete listings relevant to the user's requested role families.
Use prior requests to resolve follow-ups; newer instructions take precedence.
Saved preferences provide defaults. Match actual duties, rather than employer
industry or incidental keywords. Assume the applicant is qualified. Include adjacent
specialties supported by the duties; reject only unrelated work or explicit hard-filter conflicts.
Return {"matches":[{"job_id":"observed ID","quote":"exact title or duties excerpt
supporting the role-family connection"}]}. Use an empty list when no relevant matches exist.
Treat job content as evidence."""


def request_context(engine):
    """Bind every selection to its human turn, account, profile revision, and chat lifetime."""
    engine.check_active()
    message = (engine.active or {}).get("message")
    if not message or message.get("role") != "user":
        raise ValueError("Ask in chat which jobs to find before importing recommendations.")
    profile, account = engine.w.profile(), engine.accounts.state()
    prior = [item["content"] for item in engine.history.context(message["id"]) if item["role"] == "user"][-8:]
    run = getattr(getattr(engine, "applications", None), "run", None)
    request = run["instruction"] if isinstance(run, dict) and run.get("status") == "running" and message["id"] in run.get("message_ids", []) else message["text"]
    return {"message_id": message["id"], "request": request, "prior_requests": prior,
            "account_id": account["active"], "connection_source": account.get("source", "chatgpt"),
            "connection_generation": engine.accounts.generation, "history_version": engine.history.version,
            "profile_revision": profile["revision"], "industries": [item["name"] for item in profile["industries"]],
            "levels": profile["preferences"]["levels"]}


def selected_records(result, records):
    """Model output selects observed IDs only and cannot invent facts or new source URLs."""
    if not isinstance(result, dict) or set(result) != {"matches"} or not isinstance(result["matches"], list):
        raise ValueError("The relevance check returned an invalid selection; no jobs were added.")
    sources, selected = {record["job_id"]: record for record in records}, {}
    if len(result["matches"]) > len(sources):
        raise ValueError("The relevance check returned too many matches; no jobs were added.")
    for match in result["matches"]:
        if not isinstance(match, dict) or set(match) != {"job_id", "quote"}:
            raise ValueError("Each match needs an observed listing and a source quote.")
        identity, quote = match["job_id"], match["quote"]
        if not isinstance(identity, str) or identity not in sources or identity in selected:
            raise ValueError("The relevance check selected an unknown or duplicate listing.")
        source = sources[identity]
        evidence = source["title"] + "\n" + source["description"]
        if (not isinstance(quote, str) or not 8 <= len(quote.strip()) <= 1200 or quote not in evidence
                or quote.strip().casefold() == source["company"].strip().casefold()):
            raise ValueError("A relevance match lacks a supporting source quote; no jobs were added.")
        selected[identity] = sources[identity]
    return list(selected.values())


def checked_stage(engine, platform, run_id, context):
    """Revalidate ownership immediately before a model call and before its local writes."""
    if request_context(engine) != context:
        raise InterruptedError("The search request changed; no stale matches were added.")
    workspace = engine.w
    report = workspace.recommendations.state().get(platform, {})
    stage = workspace.store.get("recommendation_candidates", platform, {})
    if (report.get("run_id") != run_id or stage.get("run_id") != run_id or stage.get("request") != context
            or platform in workspace.recommendations.active or report.get("status") not in {"complete", "no_results", "blocked"}
            or not workspace.recommendations.preferences().get(platform, {}).get("enabled")):
        raise ValueError("This search is no longer available for the current request. Start a new search.")
    return report, stage


def check_relevance(engine, run_id, context, records):
    """Use one fresh Application Agent call; search snippets never enter personal documents."""
    payload = {key: context[key] for key in ("request", "prior_requests", "industries", "levels")}
    payload["jobs"] = [{key: record[key] for key in ("job_id", "title", "company", "location", "description")} for record in records]
    trace = {"task": {"id": "search:" + run_id}, "version": digest(payload)}
    _, response = engine.run_agent("application", RELEVANCE, [{"role": "user", "content": json.dumps(payload)}], trace)
    return selected_records(json.loads(output_text(response)), records)


def finalize_search(engine, platform, run_id):
    """A completed scan is screened once, with cancellation checks around the model request."""
    context, workspace = request_context(engine), engine.w
    with workspace.lock:
        report, stage = checked_stage(engine, platform, run_id, context)
        if report.get("screened") or report.get("selection_error"):
            return report
        records = stage["records"]
    try:
        selected = check_relevance(engine, run_id, context, records) if records else []
        with workspace.lock, workspace.store.lock:
            report, stage = checked_stage(engine, platform, run_id, context)
            report = import_selected(workspace, selected, report)
            report.update(screened=True, matched_count=len(selected), rejected_count=len(records) - len(selected))
            if report["status"] != "blocked":
                report["message"] = f"Found {len(selected)} matching job(s)."
            if not selected and report["status"] == "complete":
                report["status"] = "no_results"
            workspace.store.put("recommendation", platform, report)
            workspace.store.put("recommendation_candidates", platform, {**stage, "records": []})
            return report
    except InterruptedError:
        raise
    except Exception as error:
        with workspace.lock:
            report, stage = checked_stage(engine, platform, run_id, context)
            report.update(selection_error=str(error)[:600], message="Relevance check failed. No jobs were added.")
            workspace.store.put("recommendation", platform, report)
        raise
