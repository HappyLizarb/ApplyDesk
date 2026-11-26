"""Reuse narrowly matched application answers explicitly confirmed in human chat."""

import re
from datetime import date
from urllib.parse import urlsplit

# Only a single unconditional, confirmed statement may supply a personal answer.
FUTURE_QUESTION = re.compile(
    r"will you need sponsorship to work in the u s now or anytime in the future|"
    r"will you now or in the future require sponsorship for employment visa status(?: e g h 1b o 1 tn etc)?")
ONSITE_QUESTION = re.compile(
    r"are you open to being in office [1-5] days? a week in [a-z]+(?: [a-z]+){0,3}|"
    r"this position is hybrid and requires [1-5] days? a week in office including "
    r"(?:mon|tues|wednes|thurs|fri)days? in our [a-z]+(?: [a-z]+){0,3} headquarters and the remaining "
    r"[1-5] days? in either [a-z]+(?: [a-z]+){0,3} or our [a-z]+(?: [a-z]+){0,3} office are you able to meet this requirement")


def normalize(value):
    """Compare form and fact wording without dropping negation or words naming an employer."""
    return re.sub(r"[^\w]+", " ", str(value).lower()).strip()


def _scope_text(value):
    """Preserve meaningful punctuation in employer names and exact questions."""
    return " ".join(value.split()).rstrip(" *?.").casefold()


def with_application_source(profile, job):
    """Supply the local signing date and known discovery source only while applying."""
    host = (urlsplit(job.get("url", "")).hostname or "").lower()
    today = date.today()
    facts = [item for item in profile.get("facts", []) if item.get("id") != "application.discovery_source"
             and not item.get("id", "").startswith("application.signing_date.")]
    facts.append({"id": f"application.signing_date.{today.isoformat()}", "kind": "application_date",
                  "text": f"Today's local date for a signature or certification: {today:%Y-%m-%d} ({today:%m/%d/%Y}).",
                  "confirmed": True})
    for domain, source in (("linkedin.com", "LinkedIn"), ("joinhandshake.com", "Handshake")):
        if host == domain or host.endswith("." + domain):
            facts.append({"id": "application.discovery_source", "kind": "application_source", "text": source, "confirmed": True})
            break
    return {**profile, "facts": facts, "application_company": job.get("company", "")}


def confirmed_application_answer(field, profile, label, description):
    """Match observed questions to specific confirmed answers, never inferred legal status."""
    if label == "how did you hear about us":
        source = [fact for fact in profile.get("facts", []) if fact.get("id") == "application.discovery_source" and fact.get("confirmed")]
        if len(source) == 1:
            return source[0]["text"], [source[0]["id"]]
    drafted = profile.get("interest_answers", {}).get(field.get("label"))
    if drafted:
        return drafted["value"], drafted["fact_ids"]
    if label in {"current company", "current employer"}:
        return current_company_answer(profile)
    question = description if label == "visa sponsorship" else label
    observed = field.get("group") if field.get("type") in {"checkbox", "radio"} else field.get("label")
    exact_question = _scope_text(observed or label)
    company = _scope_text(profile.get("application_company") or "")
    matching = {}
    for fact in profile.get("facts", []):
        if not fact.get("confirmed") or fact.get("kind") not in {"application_answer", "preference"}:
            continue
        text = fact.get("text", "")
        if not isinstance(text, str):
            continue
        value = _scoped_answer(text, exact_question, company) or _prior_answer(_scope_text(text), question, exact_question, company)
        if value:
            matching.setdefault(value, []).append(fact["id"])
    return next(iter(matching.items())) if len(matching) == 1 else None


def _scoped_answer(text, question, company):
    """Reuse a saved exact employer question and answer without generalizing to another posting's wording."""
    saved = re.fullmatch(r"Employer: ([^\n]+)\nQuestion: ([^\n]+)\nAnswer: ([^\n]+)", text)
    if saved and company and _scope_text(saved[1]) == company and _scope_text(saved[2]) == question:
        answer = saved[3].strip()
        return answer.casefold() if answer.casefold() in {"yes", "no"} else answer
    return None


def _prior_answer(text, question, exact_question, company):
    """Read earlier free-text confirmations, keeping timing, negation and employer scope intact."""
    employment = re.fullmatch(r"have you previously worked for (.+) as an employee or contractor", exact_question)
    if employment:
        claim = re.fullmatch(r"i have (not )?worked for (.+) as an employee or contractor", text)
        if claim and employment[1] == claim[2]:
            return "no" if claim[1] else "yes"
    if question == "interview recording consent" and company:
        consent = re.fullmatch(r"i (decline|accept) interview recording at (.+)", text)
        if consent and consent[2] == company:
            return "Opt out of recording" if consent[1] == "decline" else "Opt in to recording"
    if FUTURE_QUESTION.fullmatch(question):
        claim = re.fullmatch(r"i (do not )?(?:need|require) employer sponsorship (?:now or in the future|now and in the future)", text)
        if claim:
            return "no" if claim[1] else "yes"
    status = re.fullmatch(r"do you currently (have an active immigration case)(?: ex h 1b extension green card)?", question)
    if status:
        claim = re.fullmatch(r"i (do not )?(have .+)", text)
        if claim and claim[2] == status[1]:
            return "no" if claim[1] else "yes"
    if ONSITE_QUESTION.fullmatch(question):
        willing = re.fullmatch(r"i am (open|willing) to work (?:onsite or hybrid|hybrid or onsite) and relocate anywhere", text)
        if willing:
            return "yes"
    return None


def current_company_answer(profile):
    """A confirmed current-employer preference overrides history without changing any job entry."""
    preferred = []
    for fact in profile.get("facts", []):
        if not fact.get("confirmed") or fact.get("kind") not in {"application_answer", "preference", "professional_experience"}:
            continue
        match = re.fullmatch(r"(?:Use as current employer: (.+)|Current Employer/Current Company: (.+) only\.)", fact.get("text", ""))
        if match:
            company = (match.group(1) or match.group(2)).strip()
            if company:
                preferred.append((company, fact["id"]))
    if preferred:
        companies = list(dict.fromkeys(company for company, _ in preferred))
        return (companies[0], [fact_id for _, fact_id in preferred]) if len(companies) == 1 else None
    current = [(index, row) for index, row in enumerate(profile.get("experiences", []))
               if str(row.get("end", "")).strip().casefold() == "present" and row.get("company")]
    if not current:
        return None
    companies = list(dict.fromkeys(row["company"] for _, row in current))
    return "; ".join(companies), [f"experiences.{index}" for index, _ in current]
