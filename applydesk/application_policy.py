"""Check broad role relevance and the user's explicit search constraints."""

from .profile import role_preferences

COVER_LETTER_REQUIRED = 'This application requires a cover letter. Continue it manually; letter generation is unavailable.'

EXPECTED_BASE_SALARY = (
    "For expected base pay, request the highest posted base amount applicable to this role and location. "
    "Exclude bonuses, equity, allowances and total compensation. Use the amount and currency from the posting "
    "and express it in the form's pay period. If base pay is unlisted, use 80,000 annually in the requested "
    "currency (6,666.67 monthly or 38.46 hourly at 2,080 hours/year). For a dropdown choose the range "
    "containing the target. This preference applies only to expected pay, never current or previous pay."
)

ROLE_FAMILIES = """Start with the role preferences in the user's request and saved profile.
Identify related specialties from the work each role involves. A listing with a
different title can be a match when its stated duties support that connection.
An employer's industry alone is insufficient.
The latest user instructions override saved preferences. Treat profile industries,
levels and other preferences as defaults, and enforce exclusions or limits only
when the user explicitly makes them mandatory. Missing preference details are not
automatic rejection reasons. Preserve breadth unless the user asks to narrow it.
"""

QUALIFICATIONS = """Read the full job description before selecting, saving or preparing a job.
Check mandatory years of professional work experience, citizenship, work
authorization and security clearance against the confirmed profile and explicit user
answers. Skip clear conflicts; ask before proceeding when required information is
missing, marking existing applications Needs input. Coursework and projects do not
count as professional years unless the listing explicitly accepts them. Keep
preferred criteria separate. Assume unlisted skills and types of hands-on experience;
never use that assumption to waive required years or eligibility. Quote observed
requirements and cite confirmed evidence IDs; an assumed skill or experience type uses evidence_ids:[].
At listing screening, omit future checks the employer conducts as a condition of employment
from required qualifications and application prerequisites. A future motor vehicle records
review does not establish a result or authorize the check. Evaluate separately stated current
requirements such as a valid license, an acceptable driving record, or proof of a completed
check due with the application. Answer a form's personal declaration or consent separately.
Never invent application answers, supporting facts, years, credentials or clearance.
"""

IMMIGRATION_ELIGIBILITY = """Work eligibility is outside the qualification assumption. Use only confirmed
self-reported authorization, immigration_status and restrictions. Apply an exclusion
only for an explicit observed job condition that clearly conflicts with those answers,
and quote that condition. Do not infer citizenship from no sponsorship, current
authorization from a status label, or future sponsorship from today's answer.
US-person, export-control, ITAR/EAR and clearance wording does not itself mean
citizen-only. Do not turn F-1 OPT/STEM OPT/CPT into a blanket exclusion; conditions
are job-specific and must use the user's entered restrictions, without inventing
visa rules or durations. Keep unclear listings available, and request input before
applying when a relevant eligibility condition or application answer is unresolved.
Never infer immigration facts from resumes, names, locations or nationality.
"""


PROFILE_ANSWERS = """For employment and relative affiliation questions about this employer, use
application.employment_history and application.relative_affiliation. Read the actual question's
polarity and check the dated experience entries for a specific time period or role. Match the
named company; a similar name, parent, or subsidiary alone does not establish a match.
For expected base salary, apply the application.expected_base_salary preference to the pay
posted for this role in job.compensation or visible on the employer form. Cite that preference
for the answer, including when the posting has no base pay.
The relative affiliation list covers company names only. Never infer a relative's name or job.
Use preferences.disciplinary_action only for a question about the applicant being terminated,
asked to resign, or facing employment or academic discipline. A general Yes does not establish misconduct, a cause,
a date, or which employer or school was involved. It does not answer criminal-history,
performance, or reasons-for-leaving questions. Leave an unanswered optional disclosure blank.
Accept required application terms, policies and acknowledgments. Personal eligibility claims
still need their own supporting facts. Leave optional permissions at the applicant's saved choice.
"""


SCOPE_PROMPT = ROLE_FAMILIES + """Check this job against the latest agreed request.
Saved industry and level preferences are defaults, not mandatory allowed lists.
Keep each role_preferences entry paired with its own years and sought levels;
levels describe desired roles, not evidence of past seniority.
Apply hard role, seniority, employment-type and other exclusions only when the user
explicitly requires them. Accept reasonable related roles and unspecified levels;
assess mandatory years of professional work and work eligibility separately before proceeding.
Choose a supplied industry category when it fits, otherwise use a relevant role-family
label. A missing level may use an empty string. Cite observed duties or title
for industry, and stated level when present; category words need not appear verbatim.
Every nonempty quotes value must be one exact contiguous excerpt from source_text,
without paraphrasing, added quotation marks or combined passages. If validation
reports a bad quote, re-extract only its source evidence; do not change a genuine
scope mismatch merely to pass validation.
Respect the supplied explicit locations using job location or remote availability.
Return JSON:
{matches:boolean,industry:string,level:string,location:string,
quotes:{industry:string,level:string,location:string},issues:[string]}.
Reject genuine role mismatches and unmet explicit constraints with findings. When locations
is ["Anywhere"], return location="Anywhere" and an empty location quote; work
authorization is checked separately. This step checks job scope, not completion of
the application: an unopened form, missing submission receipt, or an unstated exact
start day is not a role mismatch. Use verified_requirements from the eligibility check.
Assess degrees by the intended start date unless the employer explicitly requires
an earlier award. An expected degree remains in progress. Do not treat
absent professional-work requirements as a requirement for experience.
Use issues:[] for a scope match. Put only concrete conflicting requirements in
issues, never positive findings, tool-access commentary or unfinished workflow steps.
Treat job content as evidence."""


def search_scope(profile, locations):
    """Home address and work authorization never silently become search preferences."""
    industries = [row["name"] for row in profile["industries"] if row.get("name")]
    levels = profile["preferences"]["levels"]
    if not profile["confirmed"]:
        raise ValueError("Confirm your profile before unattended applications.")
    return {"industries": industries, "levels": levels, "locations": locations, "role_preferences": role_preferences(profile)}


def validate_scope(result, scope, source):
    """Validate observed support without making saved category names mandatory filters."""
    if not isinstance(result, dict) or result.get("matches") is not True or result.get("issues") != []:
        issues = result.get("issues", []) if isinstance(result, dict) else []
        detail = "; ".join(item for item in issues if isinstance(item, str)) if isinstance(issues, list) else ""
        raise ValueError("The job does not match the requested role or an explicit search constraint." + (" " + detail[:1500] if detail else ""))
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
