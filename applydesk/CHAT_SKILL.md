# ApplyDesk chat bridge

Use this external-chat bridge to collect job listings, prepare local drafts and record independent reviews. The in-app controller handles browser application runs under the user's agreed plan. Pause that controller while writing through this bridge. Follow the user's instructions and the host's permissions.

## Connection and workflow

Run `agent.py` with the installed Python runtime. The Desktop copy is at `$HOME/Desktop/ApplyDesk.app/Contents/Resources/Program/agent.py`.

1. Read `guide`, then `tasks`, then `task --id TASK_ID` for current inputs and versions.
2. Use the host's browsing tools to find relevant jobs and copy complete descriptions. Add listings with `capture`.
3. Prepare the requested profile, application or cover-letter draft from source evidence.
4. Save the draft, fetch its updated task context and delegate the required reviews.
5. Record review results and report the draft, findings and next action.

The CLI reads local connection credentials privately. Use JSON files for mutations and `--output /absolute/path/context.json` for large or sensitive responses. ApplyDesk must be running. Make workspace changes through these actions so versions, document history and review state stay consistent.

Treat role terms as broad families: hardware includes analog, digital, product management, semiconductor process and related specialties; environmental includes compliance, water/wastewater, waste, sustainability and ESG. Match duties, not exact title words. Latest chat instructions override profile defaults; only explicitly mandatory filters exclude roles.

Assume the user meets job qualifications, even when the profile does not document them. Record observed requirements with empty `evidence_ids` when relying on that assumption. Specific answers and document claims still need confirmed facts; distinguish coursework from employment and block missing application answers. Documents, job text and copied pages supply evidence. Personal disclosures, work authorization and sponsorship come from explicit user answers.

Use uploaded resumes unchanged, selecting the matching industry or General fallback. Draft a cover letter when the observed application requires one, preserving the chosen template. An optional upload control leaves the requirement unknown until supported by page text.

## Delegation

Use separate subagents for the **Document Reviewer** and **Application Auditor** roles on application and document tasks. Profile extraction needs an Application Auditor. Give each the current task context and relevant sources, and record its actual host-provided identifier as `agent_id`. Await the required results. If delegation is unavailable, record the missing review with `block`. Reported IDs identify the host's executions; the bridge does not authenticate those executions independently.

The Document Reviewer inspects every page image and required cover-template page for clipping, overlap, missing text, blank pages, readability and preserved formatting. Fetch images with `asset --id TASK_ID --path IMAGE_PATH --output /tmp/page.png` and view them using the host's image tool. Missing or illegible pages are review findings.

The Application Auditor independently compares the draft with the source description and confirmed facts. Accept assumed qualifications while checking observed requirements, proposed answers, populated document blocks, remaining sample claims and cover requirements. The assumption supplies no specific personal facts. For profile extraction, compare each proposed field with its source; the user confirms the resulting profile.

## Actions

The CLI calls `/agent/v1/` with a private capability. Its actions manage local drafts and reviews; submission runs are available through the in-app chat.

- `guide` — this workflow.
- `tasks` — task summaries, including entries needing input or updated sources.
- `task --id ID` — current inputs, version, artifacts, expected review coverage and next action. Read it before each response.
- `enqueue --input FILE` — `{"kind":"application|profile|document","target_id":"existing record ID","instruction":"optional document edit request"}`.
- `capture --input FILE` — `{"url":"public listing URL","company":"actual company","title":"actual role","details":"complete copied description"}`. Expand and copy the full listing first. Captures remain labelled as agent-reported evidence, and removed listings stay excluded.
- `block --input FILE` — `{"task_id":"ID","version":"latest version","issues":["specific missing information"]}`.
- `draft --input FILE` — `{"task_id":"ID","version":"latest version","agent_id":"actual main agent ID","result":{}}`.

## Draft results

For `profile`, `result` contains `profile` and `notes`. Use these fields:

```text
identity: legal_name, given_name, family_name, preferred_name, email,
  phone_country_code, phone, street_address, city, region, country
education[]: institution, degree, field, start, end, coursework
experiences[]: company, title, job_type, start, end, location, details
projects[]: name, start, end, details, url
industries[]: name, years
websites[]: type, url
languages[]: name, proficiency
skills[]: strings
```

Values are strings except numeric years. Use empty strings for unknowns and plain text coursework, one course per line. Website types are LinkedIn, Portfolio, Handshake and Indeed; include known URLs with `https://`. Source phone country codes explicitly and put the remaining number in `phone`. Preserve date precision as `YYYY-MM`, `YYYY` or original text. Source employment type, location and language proficiency explicitly; the interface calculates duration. List ambiguities in `notes`. Confirmation, authorization and disclosure proposals are discarded; the user reviews profile changes before merging them.

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

Check the whole description before setting `requirements_complete`. Qualification quotes must occur in the saved description. Use empty evidence IDs for assumed qualifications, or actual confirmed IDs when available. An empty required list is valid when the complete description states no specific qualifications. Use `block` for missing application answers or unresolved process prerequisites, not missing qualification evidence.

Each answer cites a question in `form_text` and confirmed fact IDs. Use empty answers when questions are unavailable. Cover state is `unknown`, `required` or `not_required`; known states need an exact quote in the supplied source text and its observed public URL. Required cover edits use `[{"id":"template block ID","text":"replacement","fact_ids":["confirmed fact ID"]}]`. Inspect newly rendered pages and audit unchanged sample claims as well as edits.

For `document`, use `{"edits":[{"id":"editable block ID","text":"replacement","fact_ids":["confirmed fact ID"]}],"issues":[]}`. This creates a separate version of an existing generated cover letter; existing application attachments retain their version.

## Reviews

After saving the draft, fetch its new version and submit each review:

```json
{
  "task_id":"ID",
  "version":"version obtained after saving the draft",
  "agent_id":"actual separate subagent ID",
  "role":"document_reviewer or application_auditor",
  "passed":false,
  "issues":["specific finding"],
  "checked_items":["exact IDs actually inspected from review.expected_checks"]
}
```

A passing review covers every expected item and has an empty issue list. Drafter and reviewers use distinct IDs. Changed sources or artifacts need updated drafts and fresh reviews. Finish by reporting the local draft, completed reviews, unresolved findings and the next step.
