"""Conservative employer and sign-in boundaries for saved applicant accounts."""

import hashlib
import re
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

from .workday_skill import tenant_key
from .recommendation_checks import same_workday_job


class NativeInputRequired(ValueError):
    """A missing applicant fact or an account recovery step needs user input."""


def employer_key(job):
    """Match spelling exactly except for case and whitespace; never guess company aliases."""
    company = " ".join(str(job.get("company") or "").split()).casefold()
    if company in {"", "unknown", "unknown company", "not assessed", "company", "employer", "n/a"}:
        raise ValueError("Identify the employer before saving or creating an applicant account.")
    return hashlib.sha256(company.encode()).hexdigest()


def native_company_quote(job, saved, quote, *, page_url=""):
    """A bound returning account may also use the legal name verified on the original employer listing."""
    names = [job["company"]]
    source, public = job.get("recommendation_source") or {}, job.get("public_source") or {}
    legal = public.get("company")
    if (saved.get("realm") and saved.get("id") == job.get("employer_account_id") == employer_key(job)
            and source.get("company") and employer_key(source) == saved["id"]
            and public.get("complete") and legal and legal == (job.get("analysis") or {}).get("company")
            and public.get("url") and source.get("employer_url")):
        from .discovery import canonical_url
        if canonical_url(source["employer_url"]) == canonical_url(public["url"]):
            names.append(legal)
    tenant_brand = verified_tenant_initials(job, public, page_url)
    if tenant_brand:
        names.append(tenant_brand)
    return any(re.search(r"(?<!\w)" + re.escape(name.casefold()) + r"(?!\w)", quote.casefold()) for name in names)


def bound_signin_host(job, saved, url):
    """Identify a returning sign-in by the saved account’s exact realm and verified Workday requisition."""
    source = job.get("public_source") or {}
    if (not url or not saved.get("id") or saved["id"] != job.get("employer_account_id")
            or (saved.get("owner_job_id") != job.get("id") and saved.get("reuse_confirmed") is not True) or not saved.get("realm")
            or not source.get("complete") or source.get("company") != job.get("company")
            or not same_workday_job(source.get("url", ""), url)):
        return None
    if saved["id"] != employer_key(job) or saved.get("origin") != https_origin(url) or not matches_auth_path(url, saved["realm"]):
        return None
    return urlsplit(url).hostname


def verified_tenant_initials(job, public, account_url):
    """Recognize visible initials only on the exact Workday site that published the verified job."""
    source_url = public.get("url") or ""
    if (not account_url or not public.get("complete") or public.get("company") != job.get("company")
            or not source_url or "/job/" not in urlsplit(source_url).path):
        return None
    scope = tenant_key(source_url)
    if not scope or scope.endswith("/") or tenant_key(account_url) != scope or https_origin(source_url) != https_origin(account_url):
        return None
    initials = "".join(word[0] for word in re.findall(r"[a-z0-9]+", public["company"].casefold()))
    tenant = urlsplit(source_url).hostname.split(".")[0]
    return tenant if len(initials) >= 3 and initials == tenant else None


def https_origin(url):
    """Credential scope is the exact HTTPS host and port, never a hostname suffix."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Credentials require an HTTPS page and form destination without embedded credentials.")
    host = parsed.hostname.lower()
    host = f"[{host}]" if ":" in host else host
    return "https://" + host + (f":{parsed.port}" if parsed.port and parsed.port != 443 else "")


def is_registration_link(page, operation):
    """Identify a visible registration link on the current sign-in form separately from the signup submit button."""
    if operation.get("action") != "click" or operation.get("intent", "navigation") != "navigation" or "point" in operation:
        return False
    elements = page.get("elements", [])
    link = next((item for item in elements if item.get("id") == operation.get("element_id")), {})
    if (link.get("role") != "AXLink" or not link.get("web_content") or not link.get("enabled")
            or not re.fullmatch(r"create (?:an? )?account|sign[ -]?up|register", str(link.get("label", "")).strip(), re.I)
            or page.get("native_modal") or page.get("elements_truncated")
            or sum(bool(item.get("secure") and item.get("enabled")) for item in elements) != 1
            or not any(item.get("role") == "AXButton" and item.get("enabled")
                       and re.fullmatch(r"sign[ -]?in|log[ -]?in", str(item.get("label", "")).strip(), re.I)
                       for item in elements)):
        return False
    source, destination = page.get("url", ""), link.get("url") or ""
    try:
        if https_origin(source) != https_origin(destination) or source == destination:
            return False
        current_query = parse_qs(urlsplit(source).query)
        target = urlsplit(destination)
        query = parse_qs(target.query)
    except (TypeError, ValueError):
        return False
    for key in ("company", "tenant", "tenantId", "organization"):
        if current_query.get(key) != query.get(key):
            return False
    markers = {target.path.rstrip("/").rsplit("/", 1)[-1].casefold()}
    markers.update(value.casefold() for key in ("login_ns", "action", "mode") for value in query.get(key, []))
    return bool(markers & {"register", "registration", "signup", "sign-up", "create-account", "createaccount"})


def is_signin_switch(page, operation, account, job=None, *, recovery=False):
    """Recognize navigation to sign-in on an employer listing or an existing registration form."""
    if (operation.get("action") != "click" or operation.get("intent", "navigation") != "navigation"
            or "point" in operation or page.get("native_modal") or page.get("elements_truncated")
            or not account or not account.get("realm") or not page.get("url")):
        return False
    in_realm = matches_auth_path(page["url"], account["realm"])
    tenant = tenant_key(account["realm"])
    same_site = tenant and not tenant.endswith("/") and tenant == tenant_key(page["url"])
    # Recovery can reach the registration header after a redirect within the same Workday site.
    if https_origin(page["url"]) != account.get("origin") or not (in_realm or recovery and same_site):
        return False
    fields = [c for c in page.get("elements", []) if c.get("web_content") and c.get("enabled") and not c.get("clipped")]
    buttons = [c for c in fields if c.get("role") in {"AXButton", "AXLink"}]
    switches = [c for c in buttons if re.fullmatch(r"sign[ -]?in|log[ -]?in", c.get("label", "").strip(), re.I)]
    if len(switches) != 1 or switches[0]["id"] != operation.get("element_id"):
        return False
    if job and bound_signin_host(job, account, page["url"]) and "apply" not in urlsplit(page["url"]).path.split("/"):
        # Workday's listing header opens a form. A credential dialog must still use private authentication.
        form = any(c.get("web_content") and (c.get("secure") or c.get("parent_dialog_id") or
                   c.get("role") in {"AXTextField", "AXTextArea", "AXComboBox"}) for c in page.get("elements", []))
        if not form and any(c.get("label", "").strip().casefold() == "apply" for c in buttons):
            return True
    passwords = [c for c in fields if c.get("secure")]
    if len(passwords) != 2 or not any(re.search(r"confirm|verify|re.?enter|repeat", c.get("label", ""), re.I) for c in passwords):
        return False
    signup = [c for c in buttons if c.get("role") == "AXButton" and re.fullmatch(r"create (?:an? )?account|sign[ -]?up", c.get("label", "").strip(), re.I)]
    if signup and in_realm:
        return len(signup) == 1
    return bool(same_site and _registration_header(fields, switches[0], page["url"]))


def _registration_header(fields, switch, url):
    """A header above the registration heading opens sign-in when the signup button is offscreen."""
    headings = [c for c in fields if c.get("role") == "AXHeading" and c.get("label", "").strip().casefold() == "create account"]
    if len(headings) != 1:
        return False
    destination = switch.get("url")
    if destination and (tenant_key(destination) != tenant_key(url) or https_origin(destination) != https_origin(url)):
        return False
    link, title = switch.get("bounds") or {}, headings[0].get("bounds") or {}
    return "y" in link and link.get("height", 0) > 0 and "y" in title and link["y"] + link["height"] < title["y"]


def matches_auth_path(url, realm):
    """Check both the exact HTTPS tenant and the observed authentication path before private input."""
    parsed, scope = urlsplit(url), urlsplit(realm)
    path_matches = parsed.path.startswith(scope.path) if scope.path.endswith("/") else parsed.path == scope.path
    query = parse_qs(parsed.query)
    return (https_origin(url) == https_origin(realm) and path_matches
            and all(query.get(key) == values for key, values in parse_qs(scope.query).items()))


def sign_in_scope(job, snapshot, button):
    """Require visible employer evidence and retain the exact authentication path boundary."""
    employer_key(job)
    company = " ".join(job["company"].split()).casefold()
    visible = " ".join(str(snapshot.get("text") or "").split()).casefold()
    if not re.search(r"(?<!\w)" + re.escape(company) + r"(?!\w)", visible):
        raise ValueError("The sign-in page does not identify this employer. Sign in manually.")
    if button.get("auth_widget") == "workday":
        return workday_scope(snapshot, button)
    if button.get("auth_widget") == "successfactors":
        from .successfactors_auth import successfactors_auth_context
        successfactors_auth_context(snapshot, button)
        return "https://career4.successfactors.com/career?company=" + snapshot["successfactors_auth"]["tenant"]
    action = urlsplit(button["form_action"])
    if action.query or action.fragment or "%" in action.path or ".." in action.path.split("/"):
        raise ValueError("The employer's sign-in scope is ambiguous. Sign in manually.")
    parent = action.path.rstrip("/").rsplit("/", 1)[0] + "/"
    return https_origin(button["form_action"]) + parent


def workday_scope(snapshot, button):
    """Bind Workday credentials to the exact observed employer host and career site."""
    page, action = urlsplit(snapshot["url"]), urlsplit(button["form_action"])
    root = snapshot.get("workday_auth", {}).get("root", "")
    if (not re.fullmatch(r"[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com", page.hostname or "")
            or not re.fullmatch(r"/[a-z]{2}-[A-Z]{2}/[\w-]+", root)
            or not page.path.startswith(root + "/job/") or https_origin(snapshot["url"]) != https_origin(button["form_action"])):
        raise ValueError("The Workday account scope changed.")
    kind = "register" if button["action"] == "create_account" else "login"
    expected = {"redirect": [page.path + ("?" + page.query if page.query else "")]} if kind == "register" else {}
    if action.path != root + "/" + kind or parse_qs(action.query) != expected or action.fragment:
        raise ValueError("The Workday account destination changed.")
    return https_origin(snapshot["url"]) + root + "/"


def native_account_scope(workspace, job, page, decision, registration_route=None):
    """Use the actually visible HTTPS URL and existing realm, without inferring an employer's hidden endpoints."""
    url, quote = page.get("url"), decision.get("company_quote", job.get("company", ""))
    saved = workspace.store.get("employer_account", employer_key(job)) or {}
    if not isinstance(url, str) or not isinstance(quote, str):
        raise ValueError("Read the visible HTTPS address and employer identity before entering credentials.")
    host = bound_signin_host(job, saved, url) if decision.get("mode") == "sign_in" else None
    if not host or quote.casefold() != host:
        if not quote or quote not in page.get("text", ""):
            raise ValueError("Read the visible HTTPS address and employer identity before entering credentials.")
        if not native_company_quote(job, saved, quote, page_url=url):
            raise NativeInputRequired("The account page does not identify this application's employer.")
    origin = https_origin(url)
    parsed = urlsplit(url)
    if "\\" in unquote(parsed.path) or any(part in {".", ".."} for part in unquote(parsed.path).split("/")):
        raise NativeInputRequired("The employer's visible sign-in address is ambiguous.")
    realm = saved.get("realm") or urlunsplit(parsed._replace(fragment=""))
    # Only the exact signup URL followed from the saved sign-in realm can use its reserved credentials.
    linked = (decision.get("mode") == "create_account" and registration_route
              and registration_route[1] == url and matches_auth_path(registration_route[0], realm))
    if https_origin(realm) != origin or not (matches_auth_path(url, realm) or linked):
        raise NativeInputRequired("The saved account belongs to a different sign-in address. Verify its scope before continuing.")
    if saved and saved.get("origin") != origin:
        raise NativeInputRequired("The visible sign-in origin differs from the saved employer account.")
    return origin, realm
