# Testing

Set up the checkout using the README, then run:

```sh
python -m unittest discover -s tests
node --experimental-vm-modules --test tests/*.test.mjs
node --test --test-concurrency=1 tests/*.test.cjs
node tests/job-details.test.js
```

The tests use temporary workspaces, synthetic profiles, mocked provider responses,
and local fictional application pages. They do not exercise live provider accounts
or real employer submissions.

Browser integration tests run headless Chrome with temporary profiles and local test
servers. Dedicated upload checks run against synthetic inputs in memory. Install
the Playwright dependency and set `APPLYDESK_NODE_MODULES` to the checkout’s
`node_modules` directory. Document-rendering tests also require the LibreOffice and Poppler
helpers described in the README. The VM-modules flag is needed by the isolated
Quit-control test.
