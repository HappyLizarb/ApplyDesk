# ApplyDesk

ApplyDesk is a local macOS app for applying to jobs and tracking what happened. Give the agent instructions in chat. It uses your saved profile and documents, works in Chrome, and records drafts, blockers, submission attempts, and employer confirmations.

## Install

Download **ApplyDesk.dmg** from [GitHub Releases](https://github.com/HappyLizarb/ApplyDesk/releases/latest). On an Apple Silicon Mac running macOS 14 or later, open the image and drag ApplyDesk to Applications. Install Google Chrome in Applications. The release is ad-hoc signed and macOS may ask you to approve it.

## Apply and track

1. Open **Account setup** and connect ChatGPT or a supported provider. Select the connection and model for the agent.
2. Confirm your information in **Profile**. Upload your resume under **Documents** and choose the industry tag it should be used for.
3. In **Chat**, describe the roles, locations, resume, and any application limit. Say whether to prepare drafts or submit applications. Shortcuts elsewhere in the app open an editable message. Send it when ready.
4. Follow progress in **Applications**. ApplyDesk saves the attempt before submission and marks a job submitted once it sees an employer confirmation. If a required answer is missing or the outcome is uncertain, the tracker shows what needs attention.

Keep ApplyDesk open. While the agent is working, ApplyDesk prevents idle system sleep and allows the display to sleep. Stop in chat to pause. Closing the lid, locking the session, choosing Sleep, or losing access to the Chrome window can interrupt browser input. The optional automatic-start timer can resume unfinished chat work while the app is running.

## Data

ApplyDesk stores profiles, documents, conversations, and the application tracker under `~/Library/Application Support/ApplyDesk`. Provider credentials and saved employer passwords use macOS Keychain. Application sign-ins stay in your Chrome profile. The selected provider receives the task content it needs, which can include personal information from messages and uploaded documents.

## Run from source

Install Python 3.11 or newer, Google Chrome, and Apple's Command Line Tools. Document previews use LibreOffice and Poppler. Make `soffice` and `pdftoppm` available on your PATH or set `APPLYDESK_SOFFICE` and `APPLYDESK_PDFTOPPM` to their executable paths.

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
mkdir -p build
xcrun clang -fobjc-arc -mmacosx-version-min=14.0 -framework Cocoa -framework ApplicationServices -framework ScreenCaptureKit native/*.m -o build/ApplyDesk
export APPLYDESK_LAUNCHER="$PWD/build/ApplyDesk"
python run.py
```

Use `python run.py --no-open --data-dir "$TMPDIR/applydesk-dev"` to start a separate development workspace. Its printed local URL contains a private session token.

## Tests

Python tests cover the service, including the application and receipt workflow. Node.js and Playwright are development dependencies for interface tests. The suites use synthetic applicant data.

```sh
python -m pip install -e '.[test]'
python -m unittest discover -s tests
npm install
npm test
```
