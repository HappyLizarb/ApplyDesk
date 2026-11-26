"""Prepare only the observed employer account prerequisites using confirmed profile facts."""

from .mapping import answer_field
from .recommendation_checks import same_successfactors_requisition


def prepare_successfactors_account(session, snapshot, profile):
    """Select the confirmed residence before opening the employer's required privacy text."""
    meta = snapshot.get("successfactors_auth") or {}
    entry = snapshot.get("authentication_entry") or {}
    if entry.get("provider") != "successfactors" or meta.get("kind") != "create_account":
        return None
    accepted = [button for button in snapshot.get("buttons", []) if button.get("reviewed_privacy")]
    if len(accepted) == 1:
        page = session.call("next", {"snapshot_id": snapshot["snapshot_id"], "field_id": accepted[0]["field_id"]})
        return {**page, "intervention": "authentication_privacy_accepted"}
    countries = [field for field in snapshot.get("fields", []) if field.get("name") == "fbclc_country"]
    if len(countries) != 1:
        return None
    country = countries[0]
    answer = answer_field(country, profile, "")
    if not answer:
        raise ValueError("Confirm your country of residence before creating this employer account.")
    if country.get("value") != answer["value"]:
        page = session.call("fill", {"snapshot_id": snapshot["snapshot_id"], "answers": [answer]})
        return {**page, "intervention": "authentication_country"}
    if meta.get("privacy_accepted"):
        return None
    links = [button for button in snapshot["buttons"] if button.get("navigation_kind") == "account_privacy"]
    if len(links) != 1:
        raise ValueError("The employer requires its recruiting privacy statement to be reviewed before account creation.")
    page = session.call("next", {"snapshot_id": snapshot["snapshot_id"], "field_id": links[0]["field_id"]})
    return {**page, "intervention": "authentication_privacy"}


def successfactors_auth_context(snapshot, button):
    """Accept this native shared-host account form only within its verified employer tenant."""
    from urllib.parse import urlsplit, parse_qs
    meta = snapshot.get("successfactors_auth") or {}
    entry = snapshot.get("authentication_entry") or {}
    action, page = urlsplit(button.get("form_action", "")), urlsplit(snapshot["url"])
    if (entry.get("provider") != "successfactors" or not same_successfactors_requisition(entry, meta)
            or action.scheme != "https"
            or action.netloc != page.netloc or action.netloc != "career4.successfactors.com"
            or action.path != "/career" or set(parse_qs(action.query)) - {"_s.crb"}
            or action.fragment or not button.get("auth_form") or button.get("form_method") != "POST"
            or meta.get("kind") != button.get("action")
            or meta.get("action") != button.get("form_action") or meta.get("form_id") != button.get("form_id")):
        raise ValueError("The employer account form no longer matches its verified handoff.")
    if meta.get("kind") == "create_account" and not meta.get("privacy_accepted"):
        raise ValueError("Review the employer's recruiting privacy statement before creating the account.")
    names = {"username", "password"} if meta.get("kind") == "login" else {
        "fbclc_userName", "fbclc_emailConf", "fbclc_pwd", "fbclc_pwdConf", "fbclc_fName", "fbclc_lName",
        "fbclc_country", "fbclc_emailEnabled", "fbclc_campaignEmailEnabled"}
    fields = [field for field in snapshot["fields"] if field.get("form_id") == meta["form_id"]]
    if not fields or any(field.get("name") not in names or not field.get("supported") for field in fields):
        raise ValueError("The employer account form contains an unsupported field.")
    return button, fields, "https://career4.successfactors.com"
