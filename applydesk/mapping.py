"""Conservative field mapping: unknown or conflicting wording never becomes a guess."""

import re

ALIASES = {
    "legal_name": {"name", "full name", "legal name", "legal full name"},
    "given_name": {"first name", "legal first name", "given name"},
    "family_name": {"last name", "legal last name", "family name", "surname"},
    "preferred_name": {"preferred name", "preferred first name"},
    "email": {"email", "email address", "e mail address"},
    "phone": {"phone", "phone number", "telephone", "telephone number", "mobile phone"},
    "phone_country_code": {"phone country code", "calling code", "international calling code", "dial code"},
    "street_address": {"street address", "address line 1", "address 1", "street address line 1"},
    "city": {"city", "current city"}, "region": {"state", "province", "state province"},
    "country": {"country", "country of residence", "current country"},
}
AUTH_QUESTIONS = {"are you legally authorized to work in {country}", "are you authorized to work in {country}",
                  "are you legally eligible to work in {country}"}
SPONSOR_QUESTIONS = {"do you require sponsorship", "will you require sponsorship", "do you need sponsorship",
                    "will you now or in the future require sponsorship for employment visa status",
                    "do you now or in the future require sponsorship"}


def normalize(value):
    """Remove punctuation but retain negation and question wording."""
    return re.sub(r"[^\w]+", " ", str(value).lower()).strip()


def answer_field(field, profile, country, country_name="", separate_phone_code=False):
    """Match exact approved labels; no generic input selector or fuzzy fallback."""
    label = normalize(field.get("group", "") if field.get("type") in {"radio", "checkbox"} else field.get("label", ""))
    for key, labels in ALIASES.items():
        if label in labels:
            return _identity_answer(field, profile, key, separate_phone_code)
    websites = []
    for index, website in enumerate(profile.get("websites", [])):
        labels = {normalize(website["type"]), normalize(website["type"] + " profile"), normalize(website["type"] + " url")}
        if website["type"] == "Portfolio":
            labels |= {"website", "personal website", "portfolio website", "portfolio url"}
        if label in labels:
            websites.append((website["url"], f"websites.{index}"))
    if websites:
        return _answer(field, *websites[0]) if len(websites) == 1 else None
    authorization = _authorization(field, label, profile, country, country_name)
    if authorization:
        return authorization
    disclosures = {"do you have a disability": "disability", "are you a protected veteran": "veteran"}
    if label in disclosures:
        key = disclosures[label]
        value = profile["preferences"].get(key, "unknown")
        return _answer(field, value, f"preferences.{key}") if value != "unknown" else None
    return None


def _identity_answer(field, profile, key, separate_phone_code):
    """Combine explicitly selected phone parts and cite both supporting identity facts."""
    value = profile["identity"].get(key)
    if not value:
        return None
    code = profile["identity"].get("phone_country_code")
    combined = key == "phone" and code and not value.startswith("+") and not separate_phone_code
    if combined:
        value = f"{code} {value}"
    answer = _answer(field, value, f"identity.{key}")
    if combined and answer:
        answer["fact_ids"].append("identity.phone_country_code")
    return answer


def _authorization(field, label, profile, country, country_name):
    """Use only explicit country coverage; temporal or negated variants stay unresolved."""
    for index, row in enumerate(profile["authorizations"]):
        if row["country"].casefold() != country.casefold():
            continue
        countries = {normalize(country), normalize(country_name)} - {""}
        if country.upper() == "US":
            countries |= {"the united states", "united states", "the us", "us", "usa"}
        matches = {q.format(country=name) for q in AUTH_QUESTIONS for name in countries}
        if label in matches and row.get("status") in {"authorized_no_sponsor", "authorized_need_sponsor", "not_authorized"}:
            return _answer(field, "no" if row["status"] == "not_authorized" else "yes", f"authorizations.{index}")
        sponsor = row.get("sponsorship_required", {"authorized_no_sponsor": "no", "authorized_need_sponsor": "yes"}.get(row.get("status")))
        if label in SPONSOR_QUESTIONS and sponsor in {"yes", "no"}:
            return _answer(field, sponsor, f"authorizations.{index}")
    return None


def _answer(field, value, fact_id):
    """Select one exact option and never use a dropdown's position."""
    if field.get("multiple") or field.get("disabled"):
        return None
    if field.get("type") == "radio":
        return {"field_id": field["field_id"], "value": True, "fact_ids": [fact_id]} if _option_matches(field, value) else None
    options = field.get("options") or []
    if options:
        choices = [option for option in options if _option_matches(option, value)]
        if len(choices) != 1:
            return None
        value = choices[0].get("value")
        if value is None or sum(option.get("value") == value for option in options) != 1:
            return None
    if field.get("type") == "checkbox":
        return None  # Consent, certifications, and multi-choice boxes require explicit user action.
    return {"field_id": field["field_id"], "value": value, "fact_ids": [fact_id]}


def _option_matches(option, value):
    """Allow only fixed equivalent disclosure labels, with no substring matches."""
    if option.get("disabled"):
        return False
    candidates = {normalize(option[key]) for key in ("label", "text") if option.get(key)}
    variants = {normalize(value)}
    if value == "decline":
        variants |= {"prefer not to answer", "i do not wish to answer", "decline to self identify"}
    return bool(candidates) and candidates <= variants


def plan_fields(snapshot, profile, country, country_name="", ignored_ids=None):
    """Return grounded answers plus precise unresolved required fields."""
    answers, unresolved, radios = [], [], {}
    split_phone_forms = {field.get("form_id") for field in snapshot.get("fields", [])
                         if normalize(field.get("label", "")) in ALIASES["phone_country_code"]}
    identifiers = [field.get("field_id") for field in snapshot.get("fields", [])]
    if None in identifiers or len(set(identifiers)) != len(identifiers):
        return [], ["Field identities are missing or duplicated. Inspect the form again."]
    for field in snapshot.get("fields", []):
        if field.get("field_id") in (ignored_ids or ()) or field.get("disabled") or field.get("type") in {"file", "password", "hidden"}:
            continue
        if field.get("type") == "radio":
            key = (field.get("form_id"), field.get("name"))
            radios.setdefault(key, []).append(field)
            continue
        answer = answer_field(field, profile, country, country_name, field.get("form_id") in split_phone_forms) if field.get("supported", True) else None
        value = field.get("checked") if field.get("type") == "checkbox" else field.get("value")
        if field.get("readonly") and (not answer or value != answer["value"]):
            unresolved.append("Review readonly field: " + (field.get("label") or field.get("name") or "unlabelled"))
            continue
        if answer:
            answers.append(answer)
        elif field.get("required") or value not in (None, "", False, []):
            unresolved.append("Review field: " + (field.get("label") or field.get("name") or "unlabelled"))
    for group in radios.values():
        choices, issues = _radio_plan(group, profile, country, country_name)
        answers.extend(choices)
        unresolved.extend(issues)
    return answers, unresolved


def _radio_plan(fields, profile, country, country_name):
    """Native name/form membership and one visible answer must agree for the whole group."""
    questions = {normalize(field.get("group", "")) for field in fields}
    groups = {field.get("group_id") for field in fields}
    first = fields[0]
    valid = (first.get("name") and len(fields) >= 2 and len(questions) == 1 and "" not in questions
             and len(groups) == 1 and None not in groups
             and all(field.get("supported", True) and not field.get("readonly") for field in fields))
    choices = [answer_field(field, profile, country, country_name) for field in fields] if valid else []
    choices = [answer for answer in choices if answer]
    if len(choices) != 1:
        return [], ["Review radio group: " + (first.get("group") or first.get("name") or "unlabelled")]
    return choices, []


def document_kind(field):
    """Resolve attachment identity by question text, never by file input order."""
    label = normalize(field.get("label", ""))
    kinds = {kind for kind, pattern in {"cover": r"\bcover letter\b", "transcript": r"\btranscripts?\b",
             "resume": r"\b(resume|cv|curriculum vitae)\b"}.items() if re.search(pattern, label)}
    return kinds.pop() if len(kinds) == 1 and not re.search(r"\b(not|no|without|except)\b", label) else None
