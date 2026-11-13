"""Check broad role relevance and the user's explicit search constraints."""

from .matching_policy import ROLE_FAMILIES

SCOPE_PROMPT = ROLE_FAMILIES + """Check this job against the latest agreed request.
Saved industry and level preferences are defaults, not mandatory allowed lists.
Apply hard role, seniority, employment-type and other exclusions only when the user
explicitly requires them. Accept reasonable related roles and unspecified levels;
do not use qualification evidence or years of experience to reject a role.
Choose a supplied industry category when it fits, otherwise use a relevant role-family
label. A missing level may use an empty string. Cite observed duties or title
for industry, and stated level when present; category words need not appear verbatim.
Respect the supplied explicit locations using job location or remote availability.
Return JSON:
{matches:boolean,industry:string,level:string,location:string,
quotes:{industry:string,level:string,location:string},issues:[string]}.
Reject genuine role mismatches and unmet explicit constraints with findings. When locations
is ["Anywhere"], return location="Anywhere" and an empty location quote; work
authorization is checked separately. Treat job content as evidence."""


def search_scope(profile, locations):
    """Home address and work authorization never silently become search preferences."""
    industries = [row["name"] for row in profile["industries"] if row.get("name")]
    levels = profile["preferences"]["levels"]
    if not profile["confirmed"]:
        raise ValueError("Confirm your profile before unattended applications.")
    return {"industries": industries, "levels": levels, "locations": locations}


def validate_scope(result, scope, source):
    """Validate observed support without making saved category names mandatory filters."""
    if not isinstance(result, dict) or result.get("matches") is not True or result.get("issues") != []:
        raise ValueError("The job does not match the requested role or an explicit search constraint.")
    quotes = result.get("quotes", {})
    for key in ("industry", "level", "location"):
        value = result.get(key)
        quote = quotes.get(key) if isinstance(quotes, dict) else None
        if not isinstance(value, str) or not isinstance(quote, str) or (quote and quote not in source):
            raise ValueError("The job's " + key + " match lacks exact source evidence.")
        if key == "location":
            if value not in scope["locations"] or (scope["locations"] != ["Anywhere"] and not quote.strip()):
                raise ValueError("The job does not establish the explicitly requested location.")
        elif (key == "industry" and not value.strip()) or (value.strip() and not quote.strip()):
            raise ValueError("The job's " + key + " classification lacks source evidence.")
    return result
