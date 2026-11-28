"""Confirmed facts are the only source of reusable application answers."""

import copy
import ipaddress
import re
import socket
from urllib.parse import urlparse

AUTH_STATUSES = {"authorized_no_sponsor", "authorized_need_sponsor", "not_authorized"}
IMMIGRATION_STATUSES = {"", "us_citizen", "us_national", "permanent_resident", "f1_opt", "f1_stem_opt", "f1_cpt", "other_work_authorized", "other_unsure"}
WEBSITE_TYPES = {"LinkedIn", "Portfolio", "GitHub", "Handshake", "Indeed"}
DISCLOSURES = {"yes", "no", "decline", "unknown"}
DISCLOSURE_FIELDS = ("disability", "veteran", "veteran_service", "race", "hispanic", "gender", "disciplinary_action")
# The browser checks this loaded schema before sending fields an older server would discard.
PROFILE_SCHEMA_VERSION = 11
ROW_FIELDS = {
    "education": ["institution", "degree", "field", "gpa", "gpa_scale", "start", "end", "coursework"],
    "experiences": ["company", "title", "job_type", "start", "end", "location", "details"],
    "projects": ["name", "start", "end", "details", "url"],
    "languages": ["name", "proficiency"],
    "industries": ["name", "years", "levels"],
    "websites": ["type", "url"],
    "authorizations": ["country", "status", "sponsorship_required", "immigration_status", "restrictions"],
}


def empty_profile():
    """Default sensitive answers to unknown rather than an assumed no."""
    return {"identity": {key: "" for key in ["legal_name", "given_name", "family_name", "preferred_name", "email", "phone_country_code", "phone", "phone_type", "street_address", "city", "region", "postal_code", "country"]},
            **{key: [] for key in ROW_FIELDS}, "skills": [], "facts": [], "chat_evidence": [],
            "preferences": {"levels": [], "disability": "unknown", "veteran": "unknown", "veteran_service": "unknown",
                            "race": "", "hispanic": "unknown", "gender": "", "age_18_or_older": "unknown",
                            "relative_companies": [], "disciplinary_action": "unknown"},
            "confirmed": False, "revision": 0, "transcript_id": None}


def profile_with_defaults(profile):
    """Add newly introduced fields on read without rewriting or reconfirming saved facts."""
    defaults = empty_profile()
    result = {**defaults, **_current_fields(profile)}
    result["identity"] = {**defaults["identity"], **result["identity"]}
    result["preferences"] = {**defaults["preferences"], **result["preferences"]}
    for section, fields in ROW_FIELDS.items():
        if section != "industries":
            result[section] = [{**dict.fromkeys(fields, ""), **row} for row in result[section]]
    for row, source in zip(result["education"], profile.get("education", [])):
        _read_legacy_gpa(row, source)
    for row in result["authorizations"]:
        _normalize_immigration(row)
    result["industries"] = role_preferences(result)
    return result


def _current_fields(profile):
    """Copy saved data without reusing retired free-text answers or their evidence IDs."""
    result = copy.deepcopy(profile)
    result.get("preferences", {}).pop("application_answers", None)
    if "facts" in result:
        result["facts"] = [fact for fact in result["facts"]
                           if not fact["id"].startswith("preferences.application_answers.")]
    return result


def _read_legacy_gpa(row, source):
    """Split only an explicitly written numeric ratio when no separate scale was saved."""
    if "gpa_scale" in source or not isinstance(source.get("gpa"), str):
        return
    number = r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))"
    match = re.fullmatch(r"\s*" + number + r"\s*/\s*" + number + r"\s*", source["gpa"])
    if match:
        row["gpa"], row["gpa_scale"] = match.groups()


def role_preferences(profile):
    """Keep each role's sought levels attached while reading legacy global preferences losslessly."""
    legacy = profile.get("preferences", {}).get("levels", [])
    rows = profile.get("industries", [])
    if not rows and legacy:
        rows = [{"name": "", "years": "", "levels": legacy}]
    return [{"name": row.get("name", ""), "years": row.get("years", ""),
             "levels": copy.deepcopy(row.get("levels", legacy))} for row in rows]


def text(value, limit=10000):
    """Reject structured or excessive input at the local API boundary."""
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        raise ValueError("Profile fields must contain plain text.")
    result = str(value).strip()
    if len(result) > limit:
        raise ValueError(f"A profile field exceeds {limit} characters.")
    return result


def validate_profile(raw, previous):
    """Normalize user input and discard client-supplied claims of fact confirmation."""
    if not isinstance(raw, dict) or not isinstance(raw.get("identity", {}), dict) or not isinstance(raw.get("preferences", {}), dict):
        raise ValueError("Profile identity and preferences must be objects.")
    result = empty_profile()
    if not isinstance(raw.get("identity", {}).get("postal_code", ""), str):
        raise ValueError("Postal code must be text, preserving any letters and leading zeros.")
    for key in result["identity"]:
        result["identity"][key] = text(raw.get("identity", {}).get(key, ""), 500)
    for section, fields in ROW_FIELDS.items():
        rows = raw.get(section, [])
        if not isinstance(rows, list) or len(rows) > 100 or any(not isinstance(row, dict) for row in rows):
            raise ValueError(f"Invalid {section} rows.")
        result[section] = [{key: _string_list(row.get(key, raw.get("preferences", {}).get("levels", [])))
                           if key == "levels" else text(row.get(key, "")) for key in fields} for row in rows]
    result["skills"] = _string_list(raw.get("skills", []))
    preferences = {**{key: previous.get("preferences", {}).get(key, default) for key, default in
                      (("veteran_service", "unknown"), ("age_18_or_older", "unknown"),
                       ("disciplinary_action", "unknown"), ("relative_companies", []))},
                   **raw.get("preferences", {})}
    result["preferences"] = _profile_preferences(preferences, result["industries"])
    _normalize_phone(result["identity"])
    _normalize_authorizations(result, raw)
    _validate_rows(result)
    result["confirmed"] = raw.get("confirmed") is True
    if result["confirmed"] and not all(result["identity"][key] for key in ("legal_name", "email")):
        raise ValueError("Legal name and email are required before confirming your profile.")
    result["revision"] = previous.get("revision", 0) + 1
    result["transcript_id"] = previous.get("transcript_id")
    result["chat_evidence"] = copy.deepcopy(previous.get("chat_evidence", []))
    result["facts"] = facts_for(result) if result["confirmed"] else []
    return result


def _profile_preferences(pref, industries):
    """Preserve explicit optional disclosures without deriving any answer from other facts."""
    legacy = _string_list(pref.get("levels", []))
    result = {"levels": list(dict.fromkeys(level for row in industries for level in row["levels"])) if industries else legacy}
    for key in ("disability", "veteran", "veteran_service", "hispanic"):
        value = pref.get(key, "unknown")
        if value not in DISCLOSURES:
            raise ValueError(f"Invalid {key} answer.")
        result[key] = value
    if result["veteran_service"] == "no" and result["veteran"] == "yes":
        raise ValueError("Review veteran status and protected veteran status. These answers conflict.")
    for key in ("race", "gender"):
        result[key] = text(pref.get(key, ""), 200)
    for key, label in (("age_18_or_older", "age 18 or older"), ("disciplinary_action", "disciplinary action")):
        value = pref.get(key, "unknown")
        if value not in ("unknown", "yes", "no"):
            raise ValueError(f"Choose Yes or No for {label}, or leave it unanswered.")
        result[key] = value
    companies = pref.get("relative_companies", [])
    if not isinstance(companies, list) or any(not isinstance(name, str) for name in companies):
        raise ValueError("Enter company names as a list of text values.")
    result["relative_companies"] = _string_list(companies)
    return result


def _normalize_phone(identity):
    """Reject conflicting phone prefixes and store a selected code only once."""
    if identity.get("phone_type", "") not in {"", "mobile", "home"}:
        raise ValueError("Choose mobile or home for the phone type, or leave it unknown.")
    code, phone = identity["phone_country_code"], identity["phone"]
    if code and not re.fullmatch(r"\+[1-9]\d{0,2}", code):
        raise ValueError("Choose a valid international calling code.")
    if code and phone.startswith("+"):
        if not phone.startswith(code):
            raise ValueError("The phone number conflicts with the selected calling code.")
        identity["phone"] = phone[len(code):].lstrip()


def _normalize_authorizations(profile, raw):
    """Preserve explicit legal permission while storing sponsorship independently."""
    legacy = {"authorized_no_sponsor": "no", "authorized_need_sponsor": "yes"}
    for row, source in zip(profile["authorizations"], raw.get("authorizations", [])):
        if "sponsorship_required" not in source:
            row["sponsorship_required"] = legacy.get(row["status"], "")
        if row["sponsorship_required"] not in {"", "yes", "no"}:
            raise ValueError("Choose Yes or No for sponsorship.")
        row["status"] = row["status"] or "unknown"
        if row["status"] in legacy and row["sponsorship_required"]:
            row["status"] = "authorized_need_sponsor" if row["sponsorship_required"] == "yes" else "authorized_no_sponsor"
        _normalize_immigration(row)


def _normalize_immigration(row):
    """Keep US-only restrictions active solely for an explicit no-sponsorship answer."""
    if row["country"].upper() != "US" or row["sponsorship_required"] != "no":
        row["immigration_status"], row["restrictions"] = "", ""
    elif row["immigration_status"] not in IMMIGRATION_STATUSES:
        raise ValueError("Choose a listed US immigration status or leave it blank.")


def _string_list(values):
    """Keep unique human-entered skills and seniority selections."""
    if not isinstance(values, list) or len(values) > 200:
        raise ValueError("Expected a list with at most 200 entries.")
    return list(dict.fromkeys(text(value, 500) for value in values if text(value, 500)))


def _validate_rows(profile):
    """Keep country and website semantics explicit and unambiguous."""
    countries = []
    for row in profile["authorizations"]:
        row["country"] = row["country"].upper()
        if len(row["country"]) != 2 or row["status"] not in AUTH_STATUSES | {"unknown"}:
            raise ValueError("Choose a country and a valid work authorization answer.")
        countries.append(row["country"])
    if len(countries) != len(set(countries)):
        raise ValueError("Use one authorization entry per country.")
    for row in profile["websites"]:
        if row["type"] not in WEBSITE_TYPES:
            raise ValueError("Choose LinkedIn, Portfolio, GitHub, Handshake, or Indeed.")
        parsed = urlparse(row["url"])
        if parsed.scheme != "https" or not parsed.hostname or parsed.username:
            raise ValueError("Profile website links must be HTTPS URLs.")


def facts_for(profile):
    """Give stable source IDs to confirmed facts for model citations and audit."""
    facts = []
    for key, value in profile["identity"].items():
        if value:
            facts.append({"id": f"identity.{key}", "text": value, "kind": "identity", "confirmed": True})
    for section in ["education", "experiences", "projects", "skills", "languages", "industries", "authorizations", "websites"]:
        kind = {"experiences": "professional_experience", "education": "education", "skills": "skill", "languages": "language"}.get(section, section)
        for index, row in enumerate(profile.get(section, [])):
            value = row if isinstance(row, str) else "; ".join(f"{k}: {v}" for k, v in row.items() if v and k != "levels")
            if value:
                facts.append({"id": f"{section}.{index}", "text": value, "kind": kind, "confirmed": True})
    for key in DISCLOSURE_FIELDS:
        value = profile["preferences"].get(key, "unknown")
        if value not in {"", "unknown"}:
            facts.append({"id": f"preferences.{key}", "text": value, "kind": "disclosure", "confirmed": True})
    age = profile["preferences"].get("age_18_or_older", "unknown")
    if age in ("yes", "no"):
        facts.append({"id": "preferences.age_18_or_older", "text": "18 years or older: " + age,
                      "kind": "application_answer", "confirmed": True})
    facts.extend(copy.deepcopy(profile.get("chat_evidence", [])))
    return facts


def public_job_url(url):
    """Keep remote job pages away from local services and embedded credentials."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Enter a public HTTP or HTTPS job URL without credentials.")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443)
    except (socket.gaierror, ValueError) as exc:
        raise ValueError("The job website could not be resolved.") from exc
    if any(not ipaddress.ip_address(address[4][0]).is_global for address in addresses):
        raise ValueError("Job links must point to a public website, not a local network address.")
    return url


def model_profile(profile):
    """Omit voluntary sensitive disclosures from document generation and job screening."""
    result = _current_fields(profile)
    for key in (*DISCLOSURE_FIELDS, "relative_companies"):
        result["preferences"].pop(key, None)
    result["facts"] = [fact for fact in result["facts"] if fact["kind"] != "disclosure"]
    return result
