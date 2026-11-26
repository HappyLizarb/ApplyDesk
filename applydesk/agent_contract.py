"""Validate chat-supplied drafts without granting application or profile approval."""

from urllib.parse import urlsplit

from .intelligence import validate_eligibility
from .mapping import COVER_LETTER_REQUIRED
from .profile import empty_profile, public_job_url, validate_profile


def object_fields(value, required, optional=()):
    """Reject unknown fields so callers cannot smuggle status or confirmation writes."""
    if not isinstance(value, dict) or not set(required) <= set(value) <= set(required) | set(optional):
        raise ValueError("Expected fields: " + ", ".join(required))
    return value


def string(value, limit=10000, empty=False):
    """Bound plain-text payloads before they enter persistent records or rendered files."""
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        raise ValueError("Expected nonempty bounded text.")
    return value.strip()


def strings(value, limit=200):
    """Accept a bounded list of readable findings or evidence identifiers."""
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError("Expected a bounded list.")
    return [string(item) for item in value]


def profile_draft(result):
    """Extracted facts never set confirmation, work authorization, or sensitive disclosures."""
    object_fields(result, ["profile", "notes"])
    raw = object_fields(result["profile"], [], empty_profile().keys())
    raw = {**raw, "confirmed": False, "authorizations": [], "preferences": {}}
    education = raw.get("education", [])
    if isinstance(education, list):
        rows = []
        for row in education:
            if isinstance(row, dict) and isinstance(row.get("coursework"), list):
                row = {**row, "coursework": "\n".join(strings(row["coursework"]))}
            rows.append(row)
        raw["education"] = rows
    websites = raw.get("websites", [])
    if isinstance(websites, list):
        rows = []
        for row in websites:
            url = row.get("url", "") if isinstance(row, dict) else ""
            if isinstance(url, str) and url and ":" not in url and not any(char.isspace() for char in url):
                parsed = urlsplit("//" + url)
                if parsed.hostname and "." in parsed.hostname and not parsed.username and not url.startswith("/"):
                    row = {**row, "url": "https://" + url}
            rows.append(row)
        raw["websites"] = rows
    profile = validate_profile(raw, empty_profile())
    return {"profile": profile, "notes": strings(result["notes"])}


def application_draft(result, inputs):
    """Qualifications are assumed; observed requirements and specific personal claims stay grounded."""
    object_fields(result, ["analysis", "answers", "form_text", "cover_requirement", "cover_edits"])
    analysis = object_fields(result["analysis"], ["category", "required", "requirements_complete"], ["no_required_quote", "source_text"])
    if analysis["category"] not in inputs["resume_categories"]:
        raise ValueError("Choose a profile industry or General.")
    string(result["form_text"], 100000, empty=True)
    analysis = {**analysis, "source_text": inputs["job"]["details"]}
    check = validate_eligibility(analysis, inputs["profile"])
    if not check["eligible"]:
        raise ValueError("; ".join(check["reasons"]))
    for item in analysis["required"]:
        object_fields(item, ["text", "met", "evidence_ids"])
        if string(item["text"]) not in analysis["source_text"]:
            raise ValueError("Each required qualification must quote the saved description exactly.")
    answers = validate_answers(result["answers"], result["form_text"], inputs["profile"])
    cover = cover_requirement(result["cover_requirement"])
    if cover["state"] == "required":
        raise ValueError(COVER_LETTER_REQUIRED)
    if result["cover_edits"] != []:
        raise ValueError("Letter customization is unavailable. Leave cover_edits empty.")
    return {**result, "analysis": analysis, "answers": answers, "cover_requirement": cover}


def validate_answers(answers, source, profile):
    """Answers cite known facts and questions observed on a read-only form page."""
    if not isinstance(answers, list) or len(answers) > 200:
        raise ValueError("Expected at most 200 proposed answers.")
    facts = {fact["id"] for fact in profile["facts"] if fact.get("confirmed")}
    seen = set()
    for answer in answers:
        object_fields(answer, ["question", "answer", "fact_ids"])
        question = string(answer["question"])
        string(answer["answer"])
        ids = strings(answer["fact_ids"])
        if question not in source or question in seen or not ids or not set(ids) <= facts:
            raise ValueError("Answers require distinct observed questions and confirmed supporting facts.")
        seen.add(question)
    return answers


def cover_requirement(value):
    """Missing or ambiguous evidence remains unknown, never an automatic optional letter."""
    object_fields(value, ["state", "quote", "source_url", "source_text"])
    if value["state"] not in {"unknown", "required", "not_required"}:
        raise ValueError("Cover requirement must be unknown, required, or not_required.")
    quote = string(value["quote"], 5000, empty=True)
    source = string(value["source_text"], 100000, empty=True)
    string(value["source_url"], 2000, empty=True)
    if value["state"] != "unknown":
        public_job_url(value["source_url"])
        if not quote or quote not in source:
            raise ValueError("A cover-letter decision requires an exact observed source quote.")
    return value
