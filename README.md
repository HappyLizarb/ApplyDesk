# ApplyDesk

A personal macOS job tracker with one in-app agent and browser-assisted application workflows. The controller runs on your Mac; the Codex desktop app does not need to remain open.

## Connect and chat

Open **Account setup** and choose a connection:

- **ChatGPT account** uses eligible ChatGPT-plan access and shares its usage limits.
- **OpenAI**, **Anthropic**, **Google**, and **Grok (xAI)** use the selected provider’s API key, billing, and quotas.

The **Use for agent** dropdown selects the connection for your single agent. ApplyDesk does not silently switch providers or fall back from ChatGPT-plan access to API billing. API keys and ChatGPT tokens are stored in macOS Keychain. Connecting or switching a source leaves work paused until you send a message.

Manage provider credentials under **API Keys**. Choose the model and thinking strength from the menu beside the chat composer. ApplyDesk saves these settings per connection. Stop active work before changing models.

**Chat** opens a full-page conversation. The send button becomes **Stop** while the agent is working. Sending a message while paused resumes work. **New chat** clears the conversation and pauses work while retaining your saved profile, jobs, and documents. Account setup also has an **Automatically process queued drafts** option.

## Workflow

1. Enter and confirm facts in **Profile**, or upload a LinkedIn PDF and request extraction. Extracted proposals require your explicit merge/save before becoming confirmed profile facts.
2. Upload finished PDF or DOCX resumes in **Documents** and assign industry tags. Each tag has one active resume; **General** is the fallback. Uploaded resumes are not rewritten by the agent. Deleting a resume removes it from future use while preserving copies attached to existing applications; unused files and previews are removed. An optional transcript upload is also available.
3. Ask chat to find roles on public employer career pages and recruiting sites. The agent can search the web, read a listing, and copy the full description into the tracker. You can also add a listing link yourself.
4. Ask chat to prepare or submit applications. The agent reuses your confirmed answers and saved profile. It can make ordinary assumptions about skills. Required years of professional experience, eligibility, and personal declarations need supporting information. The agent skips optional cover letters and flags required letters for manual handling.
5. Review drafts and blockers in the application details. Correct a blocked task's inputs before retrying it. Agent activity is visible in chat.

Discuss applications naturally in chat. Permission, an optional application count, locations (including anywhere), role preferences, and a chosen resume can be agreed across messages. Profile-confirmation follow-ups and continuations retain the same remaining allowance; no scripted phrase or repeated instruction is required. A specifically selected upload is used unchanged. ApplyDesk honors any requested positive count; when no count is specified, there is no application cap. It can search and apply across batches, and later stop or draft-only instructions take precedence. Keep ApplyDesk open and the Mac awake during a run.

The agent compares the duties in each listing with the roles and levels you select in Profile. It searches across related specialties using your latest instructions and applies any filters you mark as mandatory.

Before submitting, ApplyDesk checks the agreed scope, documents, and final form. It records the attempt before clicking and counts a submission only after observing an employer confirmation. An uncertain outcome stays in the tracker for review. A missing personal answer, CAPTCHA, or unsupported form can require your input.

The agent reuses saved employer accounts. Saving an existing account confirms reuse for that employer; passwords stay in macOS Keychain. Signup attempts are recorded before browser actions and never retried automatically, including after a crash or removed application. Unknown employers, conflicting legacy accounts, changed sign-in boundaries, and shared-site ambiguity pause for manual sign-in. Employer aliases and accounts created outside ApplyDesk cannot be discovered reliably; verify an existing account before creating one.

ApplyDesk reuses a window in your signed-in Google Chrome profile. If macOS confirms that there are no windows, ApplyDesk opens one and waits for it. Each application uses its own tab. ApplyDesk leaves the window open when you stop.

You can delete a profile PDF, resume, transcript, or saved document from its file control or preview. ApplyDesk stops active work, removes the selected local file and previews, and preserves application outcomes and saved profile facts. Affected unfinished applications show **Needs input**.

## Data and agent boundaries

Profiles, documents, chat history, task records, and research browser sessions are saved locally under `~/Library/Application Support/ApplyDesk`, outside this repository. Application sign-ins stay in your existing Chrome profile. Agent work sends the relevant task content to the selected provider. Structured agent context excludes voluntary disclosures, but uploaded documents or chat messages may themselves contain personal information.

ChatGPT and OpenAI API requests use streamed Responses requests with `store: false`. Anthropic, Google, and Grok use their native APIs and return final text. Grok Responses requests use `store: false`. Provider-specific data handling and account limits still apply. ChatGPT sign-in does not import your existing ChatGPT conversations.

For application work, the agent uses the profile, documents, and tools provided by ApplyDesk. Its browser actions are scoped to the application session. The controller checks the current form and submission permission before granting the final click.

Disconnecting ChatGPT removes local tokens and attempts remote session revocation. To remove the registered authorization itself, use ChatGPT's **Settings → Security and login → Sign in with ChatGPT** controls. The app reports when revocation could not be confirmed.

An optional external-chat bridge is available through [agent.py](agent.py) and [its guide](applydesk/CHAT_SKILL.md). It uses a separate local credential, is limited to draft operations, and rejects writes while the built-in agent is active. No global plugin or MCP connection is installed.

## Run from source

For Python development, install the checkout into a virtual environment with `python -m pip install -e .`. Restart the local service after editing Python. To exercise a release build, install the wheel produced by `python -m pip wheel --no-deps .`. The wheel includes the web interface, browser worker and agent guides.

Use `applydesk --no-open --data-dir "$TMPDIR/applydesk-dev"` for a separate development workspace. Its local URL contains a private session token. Live Chrome input requires the native macOS helper and its permissions. Set `APPLYDESK_LAUNCHER` to the installed helper or a development build before testing browser input.

This repository contains macOS source code, without a prebuilt app bundle or portable installer. Install Python 3.11 or newer, Node.js, and Google Chrome. From the repository directory:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
npm install
export APPLYDESK_NODE="$(command -v node)"
export APPLYDESK_NODE_MODULES="$PWD/node_modules"
python run.py --no-open
```

Open the private local URL printed in the terminal. It includes a temporary access token; keep the URL private. The service stays running until you confirm **Quit ApplyDesk** or press Control-C in the terminal.

For a dedicated Chrome app window, install Apple's Command Line Tools and compile the included window helper:

```sh
mkdir -p build
xcrun clang -fobjc-arc -mmacosx-version-min=14.0 -framework Cocoa -framework ApplicationServices -framework ScreenCaptureKit launcher.m chrome-approval.m native-browser-*.m -o build/ApplyDesk
export APPLYDESK_LAUNCHER="$PWD/build/ApplyDesk"
python run.py
```

Document uploads and previews also need LibreOffice and Poppler. The current renderer expects these executable helpers:

```text
~/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override/soffice
~/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override/pdftoppm
```

If those helpers are absent, configure their installed paths in [applydesk/document_render.py](applydesk/document_render.py) before importing documents. Installing the Python requirements does not install these renderers. Scanned PDFs have no OCR support, and complex document layouts require visual review.
