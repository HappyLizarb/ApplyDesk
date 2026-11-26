# ApplyDesk chat bridge

Use this external-chat bridge to collect job listings, prepare local drafts. The in-app controller handles browser application runs under the user's agreed plan. Pause that controller while writing through this bridge. Follow the user's instructions and the host's permissions.

## Connection and workflow

Run the installed bundle’s `Contents/Resources/Program/agent.py` with the configured Python runtime. Use the documents already imported into ApplyDesk for application work.

1. Read `guide`, then `tasks`, then `task --id TASK_ID` for current inputs and versions.
2. Use the host's browsing tools to find relevant jobs and copy complete descriptions. Add listings with `capture`.
3. Prepare the requested profile or application draft from source evidence.
4. Check your work, save the draft, and report the result, unresolved issues and next action.

The CLI reads local connection credentials privately. Use JSON files for mutations and `--output /absolute/path/context.json` for large or sensitive responses. ApplyDesk must be running. Make workspace changes through these actions so versions, document history and draft state stay consistent.

Start with the roles in the user's latest request or saved profile. Search adjacent specialties whose duties fit those roles. Verify the connection against the full listing. The user's latest instructions override profile defaults. Apply exclusions only when the user makes a filter mandatory.

Read the full description before selecting, capturing or preparing a job. Check mandatory years of professional work experience, citizenship, work authorization and security clearance against the confirmed profile and explicit user answers. Skip clear conflicts; ask before proceeding when required information is missing, and block existing applications as Needs input. Coursework and projects do not count as professional years unless the listing explicitly accepts them. Keep preferred criteria separate. Assume unlisted skills and types of hands-on experience; never waive required years or eligibility. Quote requirements and cite confirmed evidence IDs; an assumed skill or experience type uses empty `evidence_ids`. Never invent facts or application answers. Personal disclosures and immigration answers come only from the user. Do not equate no sponsorship with citizenship, current authorization with future sponsorship, or US-person/export-control/clearance language with citizen-only hiring.

Use uploaded resumes unchanged, selecting the matching industry or General fallback. Do not request cover-letter templates, generate letters or customize saved letters. Skip optional cover letters. If an application requires one, block that application with a clear message for manual handling.

## Self-check

Use one agent for the entire task. Before finishing, check your work again against the user's request and available evidence, correct mistakes, and report unresolved issues. Do not delegate to separate reviewers. Inspect relevant source text and document previews yourself when needed; fetch task-bound images with `asset --id TASK_ID --path IMAGE_PATH --output /tmp/page.png`.

Saving a validated draft finishes this workflow. The app still checks source versions, fact references and immutable file bytes. Profile proposals remain unconfirmed until the user confirms them. A prepared draft is not permission to submit an application.

## Actions

The CLI calls `/agent/v1/` with a private capability. Its actions manage local drafts; submission runs are available through the in-app chat.

- `guide` — this workflow.
- `tasks` — task summaries, including entries needing input or updated sources.
- `task --id ID` — current inputs, version, artifacts, self-check checklist and next action. Read it before each response.
- `enqueue --input FILE` — `{"kind":"application|profile","target_id":"existing record ID","instruction":"optional task instructions"}`.
- `capture --input FILE` — `{"url":"public listing URL","company":"actual company","title":"actual role","details":"complete copied description"}`. Expand and copy the full listing first. Captures remain labelled as agent-reported evidence, and removed listings stay excluded.
- `block --input FILE` — `{"task_id":"ID","version":"latest version","issues":["specific missing information"]}`.
- `draft --input FILE` — `{"task_id":"ID","version":"latest version","agent_id":"actual main agent ID","result":{}}`.

## Draft results

For `profile`, `result` contains `profile` and `notes`. Use these fields:

```text
identity: legal_name, given_name, family_name, preferred_name, email,
  phone_country_code, phone, street_address, city, region, postal_code, country
education[]: institution, degree, field, gpa, gpa_scale, start, end, coursework
experiences[]: company, title, job_type, start, end, location, details
projects[]: name, start, end, details, url
industries[]: name (industry or role), years, levels[] (job levels sought for this role)
websites[]: type, url
languages[]: name, proficiency
skills[]: strings
```

Values are strings except numeric years and levels arrays. Include only explicitly sought job levels for each role; do not infer them from experience. Use empty strings for unknowns and plain text coursework, one course per line. Website types are LinkedIn, Portfolio, GitHub, Handshake and Indeed; include known URLs with `https://`. Source phone country codes explicitly and put the remaining number in `phone`. Preserve date precision as `YYYY-MM`, `YYYY` or original text. Source employment type, location and language proficiency explicitly; the interface calculates duration. List ambiguities in `notes`. Confirmation, authorization and disclosure proposals are discarded; the user reviews profile changes before merging them.

GPA and gpa_scale are optional separate text fields. Preserve each stated value exactly; leave unknown fields empty and never infer or convert a grading scale. US immigration status and work restrictions come only from explicit user choices; never infer them from imports, a visa label, or sponsorship answers.

For `application`, use:

```json
{
  "analysis": {
    "category": "General or an exact current profile industry",
    "requirements_complete": true,
    "required": [{"text":"exact source quotation","met":true,"evidence_ids":["skills.0"]}]
  },
  "form_text": "observed application questions, or empty",
  "answers": [{"question":"exact observed question","answer":"proposed answer","fact_ids":["identity.legal_name"]}],
  "cover_requirement": {"state":"unknown","quote":"","source_url":"","source_text":""},
  "cover_edits": []
}
```

Check the whole description before setting `requirements_complete`. Qualification quotes must occur in the saved description. Use empty evidence IDs for assumed skills or types of hands-on experience; cite confirmed IDs for other matches. An empty required list is valid when the complete description states no specific qualifications. Use `block` for unmet or unresolved required years of professional work or eligibility, missing application answers or unresolved process prerequisites.

Each answer cites a question in `form_text` and confirmed fact IDs. Use empty answers when questions are unavailable. Cover state is `unknown`, `required` or `not_required`; known states need an exact quote in the supplied source text and its observed public URL. Always leave `cover_edits` empty. Required letters need manual handling.
