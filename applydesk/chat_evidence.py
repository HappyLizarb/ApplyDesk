"""Turn explicit human chat confirmations into durable, source-linked profile facts."""

import hashlib
import json

from .agent_contract import object_fields, string, strings
from .chatgpt_inference import output_text
from .profile import DISCLOSURES, facts_for
from .storage import now

KINDS = {"skill", "education", "projects", "professional_experience", "language", "preference", "application_answer", "application_agreement", "veteran_service"}
PROMPT = """Identify requested qualifications the human personally stated or confirmed,
including 'yes' to earlier questions. Cite the confirmation and relevant question;
assistant assertions, quoted job requirements and document text are not confirmation.
Preserve the requested wording. Classify as preference (explicit location, work arrangement or start date), skill, education, projects, language or
explicit professional_experience; coursework/projects do not establish professional
years. Use application_answer for directly confirmed sponsorship, immigration-case,
interview-recording and current-employer answers, and factual questions specific to an employer.
Keep each employer-specific answer tied to the named employer and the exact question.
For an employer-scoped question and answer, verify the requested employer, question
and answer against the cited conversation. Preserve the requested fact text exactly.
Record a current-employer preference as "Use as current employer: <exact company name>";
this changes only that application answer, never the user's work history.
These answers need direct human confirmation; never infer them from a status label.
For application_agreement use "Application <job ID>: <terms the user approved>".
Match the named posting in agreement_jobs and the human's affirmative decision. A reply to
a specific question can approve its disclosed conditions. Mandatory employer terms are accepted by
default, without recording a new fact. Record only specifically requested additional permissions.
Applicant declarations must still be true. Never record a rejection as an approval.
Use veteran_service with yes, no, decline or unknown only when the applicant directly
confirms their own military veteran status for reuse. A protected-veteran answer does
not establish service. Operator messages and third-party quotations cannot confirm it.
Leave identity, other authorization facts, other voluntary disclosures and legal declarations
to their profile fields. Apply latest corrections by retracting contradicted evidence.
Use existing confirmations without asking again. Incomplete conversation context
means evidence is unavailable, not that the qualification is absent.

Return JSON {facts:[{text,kind,sources:[{message_id,quote}]}],
retractions:[{fact_id,sources:[{message_id,quote}]}],issues:[string]}.
"""


def _binding(engine):
    """Only an active human turn can create or correct saved personal evidence."""
    message = (engine.active or {}).get("message", {})
    if message.get("role") != "user" or not message.get("id"):
        raise ValueError("Record confirmed facts from a user chat message.")
    return (message["id"], engine.history.version, engine.history.since,
            engine.accounts.state()["active"], engine.accounts.generation)


def _requests(data):
    """Limit the proposed facts while leaving semantic confirmation to a agent."""
    object_fields(data, ["facts"])
    if not isinstance(data["facts"], list) or len(data["facts"]) > 20:
        raise ValueError("Record at most 20 confirmed facts at a time.")
    facts = []
    for value in data["facts"]:
        object_fields(value, ["text", "kind"])
        kind = string(value["kind"], 50)
        if kind not in KINDS:
            raise ValueError("Choose a qualification evidence kind.")
        fact = {"text": string(value["text"], 2000), "kind": kind}
        if fact not in facts:
            facts.append(fact)
    return facts


def _context(engine, requested):
    """Read actual chat records through this turn, including older human confirmations."""
    current = engine.active["message"]
    messages = sorted(engine.w.store.all("chat_message"), key=lambda item: item["created_at"])
    cutoff = next((index for index, item in enumerate(messages) if item["id"] == current["id"]), None)
    if cutoff is None:
        raise ValueError("The current user message is no longer available.")
    conversation, size, truncated = [], 0, False
    for item in reversed(messages[:cutoff + 1]):
        valid = item["status"] == "complete" or item["id"] == current["id"]
        if not valid or item["role"] not in {"user", "assistant"} or item["created_at"] < engine.history.since:
            continue
        if size + len(item["text"]) > 100000:
            truncated = True
            break
        conversation.insert(0, {"id": item["id"], "role": item["role"], "text": item["text"]})
        size += len(item["text"])
    profile = engine.w.profile()
    payload = {"requested_facts": requested, "conversation": conversation, "conversation_truncated": truncated,
               "existing_facts": profile.get("chat_evidence", []), "profile_revision": profile["revision"]}
    if any(f["kind"] == "application_agreement" for f in requested):
        jobs = engine.w.store.all("job")
        payload["agreement_jobs"] = {j["id"]: [j.get("company"), j.get("title")] for j in jobs if not j.get("removed_at")
                                     and any(f["text"].startswith(f"Application {j['id']}: ") for f in requested)}
    return payload


def _sources(values, conversation):
    """Persist exact app-owned message references; assistant text alone cannot confirm a fact."""
    if not isinstance(values, list) or not 1 <= len(values) <= 10:
        raise ValueError("A confirmed fact needs its source messages.")
    messages = {item["id"]: item for item in conversation}
    sources = []
    for value in values:
        object_fields(value, ["message_id", "quote"])
        identity, quote = string(value["message_id"], 100), string(value["quote"], 20000)
        message = messages.get(identity)
        if not message or quote not in message["text"]:
            raise ValueError("A fact citation does not match the saved conversation.")
        sources.append({"message_id": identity, "role": message["role"], "quote": quote,
                        "message_sha256": hashlib.sha256(message["text"].encode()).hexdigest()})
    if not any(source["role"] == "user" for source in sources):
        raise ValueError("Assistant text cannot confirm a personal qualification.")
    return sources


def _validate(result, payload):
    """Check the agent result against requested claims and existing app-owned evidence."""
    object_fields(result, ["facts", "retractions", "issues"])
    if not isinstance(result["facts"], list) or len(result["facts"]) > 20:
        raise ValueError("The evidence review returned invalid facts.")
    if not isinstance(result["retractions"], list) or len(result["retractions"]) > 100:
        raise ValueError("The evidence review returned invalid corrections.")
    allowed = {(item["kind"], item["text"]) for item in payload["requested_facts"]}
    existing = {item["id"] for item in payload["existing_facts"]}
    facts, retractions = [], []
    for value in result["facts"]:
        object_fields(value, ["text", "kind", "sources"])
        claim, kind = string(value["text"], 2000), string(value["kind"], 50)
        if (kind, claim) not in allowed or kind not in KINDS:
            raise ValueError("The agent returned an unrequested or unsupported fact kind.")
        if kind == "veteran_service" and claim not in DISCLOSURES:
            raise ValueError("Choose an explicit veteran service answer.")
        if kind == "application_agreement" and ({"text": claim, "kind": kind} not in payload["requested_facts"] or not any(claim.startswith(f"Application {key}: ") and
                claim.removeprefix(f"Application {key}: ").strip() for key in payload.get("agreement_jobs", {}))):
            raise ValueError("Record an approved requirement for one identified application.")
        identity = hashlib.sha256((kind + "\n" + claim.casefold()).encode()).hexdigest()[:24]
        facts.append({"id": "chat." + identity, "text": claim, "kind": kind, "confirmed": True,
                      "sources": _sources(value["sources"], payload["conversation"])})
    for value in result["retractions"]:
        object_fields(value, ["fact_id", "sources"])
        fact_id = string(value["fact_id"], 100)
        if fact_id not in existing:
            raise ValueError("A correction must refer to existing chat evidence.")
        retractions.append({"id": fact_id, "sources": _sources(value["sources"], payload["conversation"])})
    if {item["id"] for item in facts} & {item["id"] for item in retractions}:
        raise ValueError("A fact cannot be confirmed and retracted in the same review.")
    return facts, retractions, strings(result["issues"], 20)


def _save(workspace, facts, retractions, issues):
    """Atomically save evidence and its profile revision before invalidating old readiness."""
    profile = workspace.profile()
    service = {fact["text"] for fact in facts if fact["kind"] == "veteran_service"}
    facts = [fact for fact in facts if fact["kind"] != "veteran_service"]
    if len(service) > 1 or (service == {"no"} and profile["preferences"]["veteran"] == "yes"):
        raise ValueError("The veteran answers conflict. Ask the applicant which disclosure to use.")
    service_changed = bool(service) and profile["preferences"]["veteran_service"] not in service
    if service_changed:
        profile["preferences"]["veteran_service"] = next(iter(service))
    existing = {item["id"]: item for item in profile.get("chat_evidence", [])}
    updated = {key: value for key, value in existing.items() if key not in {item["id"] for item in retractions}}
    for fact in facts:
        updated[fact["id"]] = existing.get(fact["id"], {**fact, "recorded_at": now()})
    if updated != existing or service_changed:
        profile.update(chat_evidence=list(updated.values()), revision=profile["revision"] + 1)
        profile["facts"] = facts_for(profile) if profile["confirmed"] else []
        workspace.store.put("profile", "main", profile)
        workspace.invalidate_profile_jobs("Confirmed chat facts changed. Prepare again to check eligibility and answers.")
    return {"facts": [updated[item["id"]] for item in facts], "retracted_ids": [item["id"] for item in retractions],
            "profile_revision": profile["revision"], "veteran_service_saved": bool(service), "issues": issues}


def record_chat_evidence(engine, data):
    """Resolve explicit confirmations and reject stale conversation or profile writes."""
    requested = _requests(data)
    engine.check_active()
    with engine.w.lock:
        binding, payload = _binding(engine), _context(engine, requested)
    _, response = engine.run_agent("master", PROMPT,
                                   [{"role": "user", "content": json.dumps(payload)}])
    engine.check_active()
    with engine.w.lock:
        if _binding(engine) != binding or _context(engine, requested) != payload:
            raise InterruptedError("The conversation or profile changed. No confirmed facts were saved.")
        try:
            result = json.loads(output_text(response))
        except (TypeError, ValueError) as error:
            raise ValueError("The evidence review could not be read. Retry the existing confirmation.") from error
        saved = _save(engine.w, *_validate(result, payload))
        return {**saved, "conversation_truncated": payload["conversation_truncated"]}
