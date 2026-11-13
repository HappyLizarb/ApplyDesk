# Testing

Set up the checkout using the README, then run:

```sh
python -m unittest discover -s tests
node --experimental-vm-modules --test tests/*.test.mjs
for test in tests/*.test.cjs; do node "$test" || exit 1; done
node tests/job-details.test.js
```

The tests use temporary workspaces, synthetic profiles, mocked provider responses,
and local fictional application pages. They do not exercise live provider accounts
or real employer submissions.

Browser checks require Google Chrome, the Playwright dependency, and permission to
run local test servers. Set `APPLYDESK_NODE_MODULES` to the checkout's `node_modules`
directory. Document-rendering tests also require the LibreOffice and Poppler
helpers described in the README. The VM-modules flag is needed by the isolated
Quit-control test.
