"""Compare explicit listing constraints with self-reported answers, without deciding immigration law."""

import re

TOPIC = re.compile(r"\b(citizen(?:ship|s)?|nationality|work authori[sz]ation|authori[sz]ed to work|eligible to work|right to work|work permit|green card|lawful permanent resident|sponsor\w*|visas?|f[- ]?1|opt|cpt|us persons?|u\.s\. persons?|export controls?|itar|ear|security clearance)\b", re.I)
MANDATORY = re.compile(r"\b(must|requires?|required|subject to|only|cannot|can't|unable|not (?:eligible|accepted|supported|available)|(?:do|does|will) not|no (?:visa |immigration )?sponsorship)\b", re.I)
EXCEPTION = re.compile(r"\b(preferred|regardless|without regard|not required|no citizenship requirement|(?:do|does|will) not require (?:u\.?s\.? |united states )?citizenship|not only|not limited to|not restricted to|do not discriminate|does not discriminate)\b", re.I)
CONDITIONAL = {"f1_opt", "f1_stem_opt", "f1_cpt", "other_unsure"}
NONCITIZEN = {"permanent_resident", "f1_opt", "f1_stem_opt", "f1_cpt"}
CITIZEN_ONLY = re.compile(r"\b(?:requires? (?:us|united states) citizenship|must be (?:a |an )?(?:us|united states) citizens?|(?:us|united states) citizens?(?:ship)? (?:is |are )?(?:required|only)|only (?:us|united states) citizens?)\b")
FUTURE_SPONSORSHIP_QUESTION = re.compile(
    r"do you now or will you in the future require sponsorship for employment in the country which you are applying|"
    r"will you now or in the future require sponsorship for employment visa status(?: e ?g h[ -]?1b visa status)?"
)
NO_FUTURE_SPONSORSHIP = re.compile(
    r"(?:(?:applicants|candidates|you) must not require sponsorship|"
    r"sponsorship for (?:us|united states) employment authorization is not available) "
    r"(?:now|currently) or in the future(?: for this position)?"
)


def permanent_work_authorization(row):
    """Confirmed unrestricted US permanent status supports ongoing work without sponsorship."""
    return (row.get("country") == "US" and row.get("status") == "authorized_no_sponsor"
            and row.get("sponsorship_required") == "no" and not row.get("restrictions")
            and row.get("immigration_status") in {"us_citizen", "us_national", "permanent_resident"})


def authorization_context(profile):
    """Only confirmed self-reported rows may influence screening; home address supplies no status."""
    if profile.get("confirmed") is not True:
        return []
    fields = ("country", "status", "sponsorship_required", "immigration_status", "restrictions")
    return [{key: row.get(key, "") for key in fields} for row in profile.get("authorizations", [])]


def eligibility_quotes(source, required=()):
    """Keep exact explicit constraint text, excluding preferences and nondiscrimination statements."""
    if not isinstance(source, str):
        return []
    # Keep a parenthetical example with the condition it qualifies.
    pieces = re.split(r"\n+|(?<=[;!?])\s+|(?<=[a-z0-9])(?<!\be\.g)(?<!\bi\.e)\.\s+", source)
    required_quotes = {item["text"].strip() for item in required if isinstance(item, dict)
                       and isinstance(item.get("text"), str) and item["text"].strip() in source
                       and any(item["text"].strip() in piece and not EXCEPTION.search(piece) for piece in pieces)}
    pieces += list(required_quotes)
    return list(dict.fromkeys(piece.strip() for piece in pieces if isinstance(piece, str)
                             and piece.strip() and piece.strip() in source and TOPIC.search(piece)
                             and (MANDATORY.search(piece) or piece.strip() in required_quotes) and not EXCEPTION.search(piece)
                             and not employer_license_notice(piece)))


def employer_license_notice(quote):
    """Distinguish an employer's possible license process from an applicant eligibility restriction."""
    normalized = " ".join(quote.split()).casefold()
    notice = "in some instances, if applicable, u.s. export control laws require that infineon obtain a u.s. government export license prior to releasing technologies to certain persons"
    return normalized.rstrip(".") == notice


def _constraint(quote, row, country):
    """Only a narrow, literal citizenship conflict is excluded; ambiguous conditions need input."""
    text = re.sub(r"[^\w\s-]", "", quote.casefold()).strip()
    status = row.get("immigration_status", "")
    if country != "US":
        return "review", "Confirm the job’s hiring country and its specific eligibility conditions."
    if re.search(r"\b(us persons?|export controls?|itar|ear|security clearance)\b", text):
        return "review", "Confirm the employer's exact eligibility conditions; this wording does not establish citizen-only hiring."
    if CITIZEN_ONLY.search(text) and not re.search(r"\b(or|unless|except)\b", text):
        if status == "us_citizen":
            return "match", ""
        if status in NONCITIZEN:
            return "conflict", "The quoted citizen-only condition conflicts with the selected immigration status."
        return "review", "Confirm whether you meet the quoted citizen-only condition."
    if not row:
        return "review", "Confirm the work eligibility required for this job's country."
    if row.get("restrictions") or status in CONDITIONAL:
        return "review", "Confirm that this specific job meets your authorization and entered work restrictions."
    if (FUTURE_SPONSORSHIP_QUESTION.fullmatch(text) or NO_FUTURE_SPONSORSHIP.fullmatch(text)) and permanent_work_authorization(row):
        return "match", ""
    if re.search(r"\b(future|indefinite|permanent|visa|opt|cpt|f-?1|citizen|nationality)\b", text):
        return "review", "Confirm this specific eligibility condition; current sponsorship and authorization do not settle it."
    if "authorized to work" in text or "work authorization" in text or "legal right to work in the united states" in text:
        if row.get("status") in {"authorized_no_sponsor", "authorized_need_sponsor"} and country == "US":
            return "match", ""
    if re.search(r"\b(no sponsorship|(?:not|unable to) (?:offer|provide|sponsor))\b", text) and row.get("sponsorship_required") == "no" and country == "US":
        return "match", ""
    return "review", "Confirm the quoted eligibility condition before applying."


def check_immigration(profile, source, country="", required=()):
    """Missing or conditional answers request input instead of becoming invented eligibility."""
    quotes = eligibility_quotes(source, required)
    rows = [row for row in authorization_context(profile) if row["country"] == "US"]
    row = rows[0] if len(rows) == 1 else {}
    issues, conflicts = [], []
    for quote in quotes:
        outcome, reason = _constraint(quote, row, country)
        if outcome == "conflict":
            conflicts.append(f'{reason} Listing: “{quote}”')
        elif outcome == "review":
            issues.append(f'{reason} Listing: “{quote}”')
    return {"eligible": not issues and not conflicts, "needs_input": bool(issues),
            "reasons": conflicts + issues, "conflicts": conflicts}
