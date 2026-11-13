# ApplyDesk

A personal macOS job tracker with an in-app chat, separate application and review agents, and browser-assisted application workflows. The controller runs on your Mac; the Codex desktop app does not need to remain open.

## Connect and chat

Open **Account setup** and choose a connection:

- **ChatGPT account** uses eligible ChatGPT-plan access and shares its usage limits.
- **OpenAI**, **Anthropic**, or **Google API key** uses the selected provider's API billing and quotas.

The **Use for agents** dropdown selects one connection for all four roles. ApplyDesk does not silently switch providers or fall back from ChatGPT-plan access to API billing. API keys and ChatGPT tokens are stored in macOS Keychain. Connecting or switching a source leaves work paused until you send a message.

The model menu beside the chat composer lets you choose a model and supported thinking strength separately for the **Master Agent**, **Application Agent**, **Document Reviewer**, and **Application Auditor**. Changes save automatically and are retained per connection. Stop active work before changing models. Available models and capabilities depend on the selected account.

**Chat** opens a full-page conversation. The send button becomes **Stop** while agents are working. Sending a message while paused resumes work. **New chat** clears the conversation and pauses work while retaining your saved profile, jobs, and documents. Account setup also has an **Automatically process queued drafts** option.

## Workflow

1. Enter and confirm facts in **Profile**, or upload a LinkedIn PDF and request extraction. Extracted proposals require an independent audit and your explicit merge/save before becoming confirmed profile facts.
2. Upload finished PDF or DOCX resumes in **Documents** and assign industry tags. Each tag has one active resume; **General** is the fallback. Uploaded resumes are not rewritten by the agents. Deleting a resume or cover-letter template removes it from future use while preserving copies attached to existing applications; unused files and previews are removed. Cover-letter templates and transcripts have separate panels.
3. Ask chat to find roles on the web, company career pages, job boards, or your connected platforms. Browser search supports Google, Bing, and DuckDuckGo, and the agent can read other public URLs directly. It copies complete descriptions into the tracker in the order it selects jobs. LinkedIn and Handshake connections in **Profile** provide optional recommendations; Indeed remains an option for later. Web research uses the selected agent connection without an extra search API key.
4. Ask the chat to prepare an application. The master delegates the work to the Application Agent; the controller then runs separate Document Reviewer and Application Auditor requests with their own contexts. ApplyDesk assumes you meet job qualifications. Specific application answers and personal claims still use your confirmed information. Cover letters are generated only when the application explicitly requires one.
5. Review drafts and blockers in the application details. Correct a blocked task's inputs before retrying it. Agent activity is visible in chat.

Discuss applications naturally in chat. Permission, the total application limit, locations (including anywhere), role preferences, and a chosen resume can be agreed across messages. Profile-confirmation follow-ups and continuations retain the same remaining allowance; no scripted phrase or repeated instruction is required. A specifically selected upload is used unchanged. Limits remain bounded from 1 to 100, and later stop or draft-only instructions take precedence. Keep ApplyDesk open and the Mac awake during a run.

Role preferences describe broad families of work. Hardware includes analog, digital, verification, product management, semiconductor process and related roles. Environmental preferences include compliance, water, wastewater, waste, sustainability and ESG. Duties establish relevance; titles need not contain the exact words you used. Saved industries and levels are defaults, your latest instructions take precedence, and only explicitly mandatory filters exclude jobs. The agent varies short searches across related specialties instead of combining all preferences into one narrow query.

Before attempting submission, the controller checks the agreed scope, listing completeness, separate document and factual reviews, field readbacks, attachment hashes, and the current final form. It records each attempt before clicking and counts a submission only after observing a new receipt. Uncertain outcomes and previously attempted listings are not retried automatically. Missing answers, sign-in, CAPTCHA, declarations needing approval, and unsupported controls stop progress and require attention. Website support is incomplete and can break when layouts change; capture failures are reported separately from searches with no matches.

Employer accounts are shared across roles. Saving an existing account confirms reuse for that employer; passwords stay in macOS Keychain. Signup attempts are recorded before browser actions and never retried automatically, including after a crash or removed application. Unknown employers, conflicting legacy accounts, changed sign-in boundaries, and shared-site ambiguity pause for manual sign-in. Employer aliases and accounts created outside ApplyDesk cannot be discovered reliably; verify an existing account before creating one.

## Data and agent boundaries

Profiles, documents, chat history, task records, and browser sessions are saved locally under `~/Library/Application Support/ApplyDesk`, outside this repository. Agent work sends the relevant task content to the selected provider. Structured agent context excludes voluntary disability and veteran answers, but uploaded documents or chat messages may themselves contain personal information.

ChatGPT and OpenAI API requests use streamed Responses requests with `store: false`. Anthropic and Google use their native APIs and return final text. Provider-specific data handling and account limits still apply. ChatGPT sign-in does not import your existing ChatGPT conversations.

The controller creates separate run records for each agent and does not accept the master's own claims as independent reviewer verdicts. Separate reviews reduce some errors but do not guarantee correctness. Agents have bounded application tools, not arbitrary shell, filesystem, or generic browser-click access.

Disconnecting ChatGPT removes local tokens and attempts remote session revocation. To remove the registered authorization itself, use ChatGPT's **Settings → Security and login → Sign in with ChatGPT** controls. The app reports when revocation could not be confirmed.

An optional external-chat bridge is available through [agent.py](agent.py) and [its guide](applydesk/CHAT_SKILL.md). It uses a separate local credential, is limited to draft operations, and rejects writes while the built-in agents are active. No global plugin or MCP connection is installed.

## Run from source

This repository contains macOS source code, without a prebuilt app bundle or portable installer. Install Python 3.11 or newer, Node.js, and Google Chrome. From the repository directory:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt cryptography
npm install
export APPLYDESK_NODE="$(command -v node)"
export APPLYDESK_NODE_MODULES="$PWD/node_modules"
python run.py --no-open
```

Open the private local URL printed in the terminal. It includes a temporary access token; keep the URL private. The service stays running until you confirm **Quit ApplyDesk** or press Control-C in the terminal.

For a dedicated Chrome app window, install Apple's Command Line Tools and compile the included window helper:

```sh
mkdir -p build
xcrun clang -fobjc-arc -framework Cocoa launcher.m -o build/ApplyDesk
export APPLYDESK_LAUNCHER="$PWD/build/ApplyDesk"
python run.py
```

Document uploads and previews also need LibreOffice and Poppler. The current renderer expects these executable helpers:

```text
~/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override/soffice
~/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override/pdftoppm
```

If those helpers are absent, configure their installed paths in [applydesk/document_render.py](applydesk/document_render.py) before importing documents. Installing the Python requirements does not install these renderers. Scanned PDFs have no OCR support, and complex templates require visual review.
