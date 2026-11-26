"""Conservative field mapping: unknown or conflicting wording never becomes a guess."""

import re
from .greenhouse_answers import greenhouse_answer
from .application_answers import confirmed_application_answer, normalize
from .ashby_education import education_answer
from .workday_education import workday_education_answer

COVER_LETTER_REQUIRED = 'This application requires a cover letter. Continue it manually; letter generation is unavailable.'

ALIASES = {
    "legal_name": {"name", "full name", "legal name", "legal full name"},
    "given_name": {"first name", "legal first name", "given name"},
    "family_name": {"last name", "legal last name", "family name", "surname"},
    "preferred_name": {"preferred name", "preferred first name"},
    "email": {"email", "email address", "e mail address"},
    "phone_type": {"phone type", "phone device type"},
    "phone": {"phone", "phone number", "telephone", "telephone number", "mobile phone"},
    "phone_country_code": {"phone country code", "country phone code", "calling code", "international calling code", "dial code"},
    "street_address": {"street address", "address line 1", "address 1", "street address line 1"},
    "city": {"city", "current city"}, "region": {"state", "province", "state province"},
    "postal_code": {"postal code", "postcode", "zip", "zip code", "zip postal code"},
    "country": {"country", "country of residence", "current country"},
}
AUTH_QUESTIONS = {"are you legally authorized to work in {country}", "are you authorized to work in {country}",
                  "are you legally eligible to work in {country}"}
SPONSOR_QUESTIONS = {"do you require sponsorship", "do you need sponsorship"}


def answer_field(field, profile, country, country_name="", separate_phone_code=False):
    """Match exact approved labels; no generic input selector or fuzzy fallback."""
    if field.get("widget") == "workday_education":
        return workday_education_answer(field, profile)
    if field.get("widget") == "ashby_education":
        return education_answer(field, profile)
    label = normalize(field.get("group", "") if field.get("type") in {"radio", "checkbox"} else field.get("label", ""))
    if label in {"veteran status", "please indicate your veterans status", "are you a veteran", "are you a protected veteran"}:
        if field.get("widget") != "greenhouse_select" or field.get("options"):
            return _veteran_answer(field, label, profile)
    confirmed = confirmed_application_answer(field, profile, label, normalize(field.get("description", "")))
    if confirmed:
        value, facts = confirmed
        if field.get("widget") == "greenhouse_select" and value in {"yes", "no"}:
            value = value.capitalize()
        answer = _answer(field, value, facts[0])
        return {**answer, "fact_ids": facts} if answer else None
    custom = greenhouse_answer(field, profile, country, label)
    if custom:
        return custom
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
    return _disclosure_answer(field, label, profile)


def _veteran_answer(field, label, profile):
    """Match service and protected status independently against the employer's visible wording."""
    if not profile.get("confirmed"):
        return None
    preferences = profile["preferences"]
    service, protected = (preferences.get(key, "unknown") for key in ("veteran_service", "veteran"))
    if label.startswith("are you"):
        key = "veteran" if label == "are you a protected veteran" else "veteran_service"
        value = preferences.get(key, "unknown")
        if value != "unknown" and (answer := _answer(field, value, "preferences." + key)):
            return answer
        if label == "are you a veteran":
            protected = "unknown"
        else:
            service = "unknown"
    choices = []
    if service == "no":
        choices.append(("I am not a veteran", ["preferences.veteran_service"]))
    if service == "yes" and protected == "no":
        choices.append(("I identify as a veteran, just not a protected veteran", ["preferences.veteran_service", "preferences.veteran"]))
    if protected == "no":
        choices.append(("I am not a protected veteran", ["preferences.veteran"]))
    if protected == "yes":
        choices.append(("I identify as one or more of the classifications of protected veterans listed above", ["preferences.veteran"]))
    if protected == "decline" and service in {"decline", "unknown"}:
        choices.append(("decline", ["preferences.veteran"]))
    if field.get("type") != "radio" and not field.get("options"):
        return None
    for wording, facts in choices:
        answer = _answer(field, wording, facts[0])
        if answer:
            return {**answer, "fact_ids": facts}
    return None


def _disclosure_answer(field, label, profile):
    """Voluntary choices use explicit answers and the exact option's displayed wording."""
    preferences = profile.get("preferences", {})
    keys = {"do you have a disability": "disability", "do you have a disability or medical condition": "disability",
            "race": "race", "gender": "gender",
            "please select one or more of the following categories that best describe you": "race"}
    key = keys.get(label)
    value = preferences.get(key, "unknown")
    if not profile.get("confirmed") or not key or value in {"", "unknown"}:
        return None
    facts = [f"preferences.{key}"]
    if field.get("type") == "checkbox" and key == "race" and normalize(field.get("label")) == normalize(value):
        return {"field_id": field["field_id"], "value": True, "fact_ids": facts}
    if field.get("type") == "radio":
        option = normalize(field.get("label", ""))
        if key == "disability" and value == "no" and option == "no i do not have a disability":
            value = field["label"]
        if key == "race" and preferences.get("hispanic") == "no" and option == normalize(value + " (Not Hispanic or Latino)"):
            value = field["label"]
            facts.append("preferences.hispanic")
    answer = _answer(field, value, facts[0])
    if answer:
        answer["fact_ids"] = facts
    return answer


def _identity_answer(field, profile, key, separate_phone_code):
    """Format saved names and phone numbers while citing their original identity fields."""
    identity = profile["identity"]
    if key == "preferred_name" and not identity.get(key) and profile.get("confirmed"):
        key = "given_name"
    value = identity.get(key)
    name = identity.get("legal_name", "").strip()
    family = identity.get("family_name", "").strip()
    split_name = name and family and len(name.split()) == 1 and name != family and not identity.get("given_name")
    fact_key = key
    if split_name and key == "given_name":
        value, fact_key = name, "legal_name"
    if split_name and key == "legal_name":
        value = f"{name} {family}"
    if not value:
        return None
    code = profile["identity"].get("phone_country_code")
    combined = key == "phone" and code and not value.startswith("+") and not separate_phone_code
    if combined:
        value = f"{code} {value}"
    if key == "phone_type":
        return _phone_type_answer(field, value)
    answer = _answer(field, value, f"identity.{fact_key}")
    if not answer and key == "country" and value == "US" and field.get("options"):
        label = "United States of America" if field.get("widget") == "workday_select" else "United States"
        answer = _answer(field, label, "identity.country")
    if split_name and key == "legal_name" and answer:
        answer["fact_ids"].append("identity.family_name")
    if combined and answer:
        answer["fact_ids"].append("identity.phone_country_code")
    return answer


def _phone_type_answer(field, value):
    """Map confirmed contact type only when one displayed choice matches it."""
    labels = {"mobile": ["Home Cellular", "Mobile", "Cellular"], "home": ["Home", "Landline"]}
    matches = [_answer(field, label, "identity.phone_type") for label in labels.get(value, [])]
    matches = [answer for answer in matches if answer]
    return matches[0] if len(matches) == 1 else None


def _authorization(field, label, profile, country, country_name):
    """Use only explicit country coverage; temporal or negated variants stay unresolved."""
    for index, row in enumerate(profile["authorizations"]):
        if row["country"].casefold() != country.casefold():
            continue
        countries = {normalize(country), normalize(country_name)} - {""}
        if country.upper() == "US":
            countries |= {"the united states", "united states", "the us", "us", "usa"}
        matches = {q.format(country=name) for q in AUTH_QUESTIONS for name in countries}
        if country.upper() == "US":
            matches.add("are you legally authorized to work in the country where this role is based")
        if label in matches and (row.get("restrictions") or row.get("immigration_status") in {"f1_opt", "f1_stem_opt", "f1_cpt", "other_unsure"}):
            return None  # Conditional permission needs a job-specific answer, not a status inference.
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
                         if normalize(field.get("label", "")) in ALIASES["phone_country_code"]
                         or (field.get("widget") == "greenhouse_select" and field.get("group") == "Phone")}
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
    if not choices and not any(field.get("required") or field.get("checked") for field in fields):
        return [], []
    if len(choices) != 1:
        return [], ["Review radio group: " + (first.get("group") or first.get("name") or "unlabelled")]
    return choices, []


def document_kind(field):
    """Resolve attachment identity by question text, never by file input order."""
    label = normalize(field.get("label", ""))
    kinds = {kind for kind, pattern in {"cover": r"\bcover letter\b", "transcript": r"\btranscripts?\b",
             "resume": r"\b(resume|cv|curriculum vitae)\b"}.items() if re.search(pattern, label)}
    return kinds.pop() if len(kinds) == 1 and not re.search(r"\b(not|no|without|except)\b", label) else None
