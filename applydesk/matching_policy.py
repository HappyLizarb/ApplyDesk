"""Shared interpretation of role preferences and the user's qualification assumption."""

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
