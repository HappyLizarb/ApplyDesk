"""Map observed Greenhouse questions to explicit profile facts and confirmed preferences."""

from .immigration_eligibility import FUTURE_SPONSORSHIP_QUESTION, permanent_work_authorization

def greenhouse_answer(field, profile, country, label):
    """Keep widget-specific display values separate from generic native field mapping."""
    if field.get("widget") != "greenhouse_select":
        return None
    identity = profile.get("identity", {})
    disclosure = greenhouse_disclosure(field, profile, label)
    if disclosure:
        return disclosure
    value, facts = None, []
    if label == "country" and field.get("group") == "Phone" and identity.get("country") == "US" and identity.get("phone_country_code") == "+1":
        value, facts = "United States +1", ["identity.country", "identity.phone_country_code"]
    if label == "location city" and all(identity.get(key) for key in ("city", "region", "country")) and identity["country"] == "US":
        value = ", ".join([identity["city"], identity["region"], "United States"])
        facts = ["identity.city", "identity.region", "identity.country"]
    for index, row in enumerate(profile.get("authorizations", [])):
        if country == "US" and permanent_work_authorization(row):
            if label == "are you authorized to work in the country in which you are applying":
                value, facts = "Yes", [f"authorizations.{index}"]
            if FUTURE_SPONSORSHIP_QUESTION.fullmatch(label):
                value, facts = "No", [f"authorizations.{index}"]
    return {"field_id": field["field_id"], "value": value, "fact_ids": facts} if value is not None else None


def greenhouse_disclosure(field, profile, label):
    """Use explicit voluntary answers only; race never determines Hispanic ethnicity or gender."""
    preferences = profile.get("preferences", {})
    choices = {"veteran status": ("veteran", {"no": "I am not a protected veteran"}),
               "are you hispanic latino": ("hispanic", {"yes": "Yes", "no": "No"}),
               "please identify your race": ("race", None), "race": ("race", None), "gender": ("gender", None)}
    if label not in choices:
        return None
    key, options = choices[label]
    saved = preferences.get(key, "unknown")
    value = options.get(saved) if options is not None else saved if saved not in {"", "unknown"} else None
    if not profile.get("confirmed") or value is None:
        return None
    return {"field_id": field["field_id"], "value": value, "fact_ids": [f"preferences.{key}"]}


def disclosure_issues(profile, snapshot):
    """A requested race answer cannot silently disappear behind an unanswered ethnicity control."""
    preferences = profile.get("preferences", {})
    labels = {field.get("label", "").strip().rstrip("*").strip().casefold() for field in snapshot.get("fields", [])}
    ethnicity_unanswered = preferences.get("hispanic", "unknown") == "unknown"
    race_requested = bool(preferences.get("race")) and preferences.get("race") != "unknown"
    if race_requested and ethnicity_unanswered and "are you hispanic/latino?" in labels and not labels & {"race", "please identify your race"}:
        return ["Confirm the separate Hispanic/Latino question before selecting your requested race answer."]
    return []
