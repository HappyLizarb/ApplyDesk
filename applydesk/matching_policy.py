"""Shared interpretation of role preferences and the user's qualification assumption."""

ROLE_FAMILIES = """Match requested roles to families of related duties, using each job’s responsibilities to establish relevance.
Do not match an unrelated role merely because its employer works in that industry.
The latest user instructions override saved preferences. Treat profile industries,
levels and other preferences as defaults, and enforce exclusions or limits only
when the user explicitly makes them mandatory. Missing preference details are not
automatic rejection reasons. Preserve breadth unless the user asks to narrow it.
"""

QUALIFICATIONS = """Assume the user has all necessary qualifications for jobs you select.
Apply this assumption to selection, preparation and eligibility review. Do not
reject jobs because the profile omits a required skill, credential or experience.
Record observed requirements, using evidence_ids:[] when relying on the assumption;
never invent supporting facts or IDs. This assumption does not supply specific
application answers, years, dates, degrees, licenses, work authorization, sponsorship
or personal claims in documents. Those still use the user's confirmed information.
"""
