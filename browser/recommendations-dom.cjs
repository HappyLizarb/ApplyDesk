const {jobLink} = require('./recommendations-url.cjs');
const {linkedinHeader, linkedinDescription, linkedinParts, linkedinCards, locateLinkedInCard, activateLinkedInCard} = require('./linkedin-dom.cjs');

// Rendered source elements are inspected locally; the full document never leaves this module.
function visible(element) {
  return !!(element && element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden');
}

function clean(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

// Only the observed recommendation headings delimit feed cards; navigation and messages are excluded.
function feedParts(source, origin, search = false) {
  if (search) {
    const main = document.querySelector('main,[role="main"]');
    const anchors = [...(main?.querySelectorAll('a[href]') || [])].filter(anchor => visible(anchor) && jobLink(anchor.href, source, origin));
    return {headings: [], anchors};
  }
  const headings = [...document.querySelectorAll('h2')].filter(element => visible(element) &&
    (source === 'handshake' ? /^Job picks for /i.test(clean(element.innerText)) :
      ['Jobs that match your profile', 'Jobs based on your preferences'].includes(clean(element.innerText))));
  const anchors = [];
  for (const heading of headings) {
    let scope = heading.parentElement;
    for (let depth = 0; scope && depth < 5; depth++, scope = scope.parentElement) {
      if (scope.querySelectorAll('h2').length !== 1) continue;
      const candidates = [...scope.querySelectorAll('a[href]')].filter(anchor => visible(anchor) && jobLink(anchor.href, source, origin));
      if (!candidates.length) continue;
      anchors.push(...candidates);
      break;
    }
  }
  return {headings, anchors};
}

// Security interstitials are a stop condition, never an alternate source of job content.
function challengePage() {
  return /just a moment|security verification|verify you are human|unusual traffic|access denied|attention required/i.test(document.title);
}

function collectFeed({source, origin, search = false}) {
  const {headings, anchors} = feedParts(source, origin, search), links = new Map(), challenge = challengePage();
  const cards = source === 'linkedin' ? linkedinCards().slice(0, 50).map(({title, label, jobId}) => ({title, label, jobId, card: true})) : [];
  const signedOut = [...document.querySelectorAll('input[type="password"]')].some(visible);
  for (const anchor of anchors) {
    const link = jobLink(anchor.href, source, origin);
    if (links.size < 50 && !links.has(link.id)) links.set(link.id, link);
  }
  const empty = search && /\b(no (?:jobs|results|matches)(?: found)?|0 jobs|zero jobs)\b/i.test(document.querySelector('main,[role="main"]')?.innerText || '');
  return {ready: !signedOut && !challenge && (links.size > 0 || cards.length > 0 || empty), loading: headings.length > 0 && !links.size && !cards.length,
    challenge, signedOut, empty, links: [...links.values()], cards, limited: true};
}

// Only a captured recommendation anchor can start a same-document source navigation.
function locateFeedLink({source, id, url, origin, search = false}) {
  if (location.origin !== origin || challengePage()) return null;
  const anchors = feedParts(source, origin, search).anchors.filter(anchor => anchor.href === url &&
    jobLink(anchor.href, source, origin)?.id === id && !anchor.closest('form,button') && (!anchor.target || anchor.target === '_self'));
  return anchors[0] || null;
}

// Handshake's observed right panel separates employer/job metadata from personal match badges.
function handshakeParts(id) {
  const panels = [...document.querySelectorAll('[data-hook="right-content"]')].filter(visible);
  if (panels.length !== 1) return {};
  const panel = panels[0], title = panel.querySelector('h1'), titleLink = title?.closest('a[href]');
  if (!titleLink || new URL(titleLink.href).origin !== location.origin || new URL(titleLink.href).pathname.replace(/\/$/, '') !== '/jobs/' + id) return {};
  const header = titleLink.parentElement;
  const companies = [...header.querySelectorAll('a[href^="/e/"]')];
  const company = companies.find(link => [...link.querySelectorAll('img')].some(image => /logo/i.test(image.alt))) || companies.find(link => clean(link.innerText));
  const headings = [...panel.querySelectorAll('h3')];
  const description = headings.find(heading => clean(heading.innerText) === 'Job description')?.parentElement?.parentElement;
  const metadata = headings.find(heading => clean(heading.innerText) === 'At a glance')?.parentElement?.parentElement;
  if (!description || description === panel || description.contains(metadata)) return {};
  const jobLocation = (metadata?.innerText || '').split('\n').map(clean).find(line => /\b(based in|remote|hybrid|onsite)\b/i.test(line)) || '';
  return {panel, description, metadata, title: clean(title.innerText), company: clean(company?.getAttribute('aria-label') || company?.innerText), location: jobLocation};
}

// Expansion is restricted to a native, non-submit Show-more control inside the verified description.
function expansionButton(parts, source) {
  if (!parts.description) return null;
  const buttons = [...parts.description.querySelectorAll('button')].filter(visible).filter(button =>
    button.type === 'button' && (source === 'handshake' ? button.matches('.view-more-button') && /^Show more\b/.test(button.getAttribute('aria-label') || '') :
      /^(show more|see more|show more description|… more)$/i.test(clean(button.innerText || button.getAttribute('aria-label')))));
  return buttons.length === 1 ? buttons[0] : null;
}

// Return a handle only after verifying platform, current job identity, and description scope.
function locateExpansion({source, id, origin}) {
  if (challengePage()) return null;
  if (!jobLink(location.href, source, origin) || jobLink(location.href, source, origin).id !== id) return null;
  return expansionButton(source === 'handshake' ? handshakeParts(id) : linkedinParts(id, origin), source);
}

// Job text includes all scoped metadata and description, never the recommendation sidebar or fit badges.
function collectJob({source, id, origin}) {
  const current = jobLink(location.href, source, origin);
  const parts = current?.id === id ? source === 'handshake' ? handshakeParts(id) : linkedinParts(id, origin) : {};
  const job = {id, url: parts.url || (current?.id === id ? location.href : ''), title: parts.title || '', company: parts.company || '',
    location: parts.location || '', text: '', employer_url: '', complete: false, issues: []};
  if (challengePage()) { job.issues.push('The source requires a security check. Complete it manually before another import.'); return job; }
  if (!parts.description || !visible(parts.description)) { job.issues.push('The complete job description could not be scoped to its exact job identity.'); return job; }
  job.text = [parts.metadata?.innerText.trim(), parts.description.innerText.trim()].filter(Boolean).join('\n\n');
  job.issues.push(...captureIssues(parts, job));
  if (job.text.length > 50000) job.text = '';
  const employerLinks = [...parts.panel.querySelectorAll('a[href]')].filter(visible).filter(anchor => /^apply on (company|employer) website$/i.test(clean(anchor.getAttribute('aria-label') || anchor.innerText)));
  if (employerLinks.length === 1) {
    const target = new URL(employerLinks[0].href);
    job.employer_url = source === 'linkedin' && target.origin === origin && target.pathname.replace(/\/$/, '') === '/safety/go' ? target.searchParams.get('url') || '' : target.href;
  }
  job.complete = job.issues.length === 0;
  return job;
}

// Completeness concerns visible job identity and full scoped content, not successful telemetry.
function captureIssues(parts, job) {
  const issues = [];
  if (!job.title || !job.company || !job.location) issues.push('The job title, employer, or location metadata is ambiguous.');
  if (job.text.length < 200 || job.text.length > 50000) issues.push('The scoped job description is missing or exceeds the capture limit.');
  const folded = [...parts.description.querySelectorAll('button')].filter(visible).some(button =>
    /^(show more|see more)/i.test(clean(button.innerText)) || clean(button.innerText) === '… more' || /^Show more\b/.test(button.getAttribute('aria-label') || ''));
  // CSS line clamps can hide presentation while innerText still contains the complete scoped text.
  if (folded) issues.push('The job description is still collapsed.');
  if (parts.description.querySelector('iframe') || [...parts.description.querySelectorAll('*')].some(element => element.shadowRoot)) issues.push('The description contains unsupported embedded content.');
  if ([...parts.description.querySelectorAll('[aria-busy="true"],[role="progressbar"]')].some(visible) || /security verification|verify you are human/i.test(document.title)) issues.push('The job description is still loading or requires a security check.');
  return issues;
}

// The executable functions are fixed application code; callers supply only source and job identifiers.
function script(fn) {
  const helpers = [jobLink, visible, clean, feedParts, challengePage, collectFeed, locateFeedLink, handshakeParts, linkedinHeader, linkedinDescription, linkedinParts, linkedinCards, locateLinkedInCard, activateLinkedInCard, expansionButton, locateExpansion, collectJob, captureIssues];
  return `(args => { ${helpers.map(helper => helper.toString()).join('\n')} return ${fn.name}(args); })`;
}

// Playwright evaluates string expressions directly; invoke the fixed function with JSON-only arguments.
function invocation(fn, args) {
  return `${script(fn)}(${JSON.stringify(args)})`;
}

async function readFeed(page, source, origin, search = false) {
  return page.evaluate(invocation(collectFeed, {source, origin, search}));
}

async function readJob(page, source, id, origin) {
  return page.evaluate(invocation(collectJob, {source, id, origin}));
}

// Poll visible reads from the controller; no in-page polling function or CSP exception is installed.
async function waitForFeed(page, source, origin, search = false) {
  const deadline = Date.now() + 6000;
  do {
    const feed = await readFeed(page, source, origin, search);
    if (feed.ready || feed.challenge) return;
    await page.waitForTimeout(100);
  } while (Date.now() < deadline);
  throw new Error('The recommendation feed did not finish rendering.');
}

// A feed rerender may detach an anchor before clicking; only that case gets a fresh verified handle.
async function followFeedLink(page, candidate, source, origin, search = false) {
  const beforeUrl = page.url();
  for (let attempt = 0; attempt < 3; attempt++) {
    if (jobLink(page.url(), source, origin)?.id === candidate.id) return;
    const handle = await page.evaluateHandle(invocation(locateFeedLink, {...candidate, source, origin, search}));
    try {
      const anchor = handle.asElement();
      if (!anchor) throw new Error('The observed recommendation link is no longer present in its feed.');
      await anchor.click({timeout: 2000});
      break;
    } catch (error) {
      if (jobLink(page.url(), source, origin)?.id === candidate.id) return;
      if (attempt === 2 || page.url() !== beforeUrl || !error.message.includes('Element is not attached to the DOM')) throw error;
    } finally { await handle.dispose(); }
  }
  await page.waitForURL(url => jobLink(url.href, source, origin)?.id === candidate.id, {timeout: 10000});
}

// A card click must reveal its own observed job URL, matching title and company before capture.
async function followLinkedInCard(page, candidate, origin) {
  const activation = await page.evaluate(invocation(activateLinkedInCard, {...candidate, origin}));
  const deadline = Date.now() + 6000;
  const beforeId = jobLink(activation.before, 'linkedin', origin)?.id;
  do {
    const link = jobLink(page.url(), 'linkedin', origin);
    if (link && (candidate.jobId ? link.id === candidate.jobId : link.id !== beforeId || activation.selected)) {
      const job = await readJob(page, 'linkedin', link.id, origin);
      if (job.title === candidate.title && job.company && candidate.label.includes(job.company) &&
          job.location && candidate.label.includes(job.location)) return link;
    }
    await page.waitForTimeout(150);
  } while (Date.now() < deadline);
  throw new Error('The selected job card did not reveal a matching job identity.');
}

// Before expansion, a verified collapsed description is usable; afterwards only complete text is usable.
async function waitForJob(page, source, id, origin, expanded = false) {
  const deadline = Date.now() + 6000;
  do {
    const job = await readJob(page, source, id, origin);
    const challenge = job.issues.some(issue => issue.startsWith('The source requires a security check.'));
    const collapsed = !expanded && job.title && job.company && job.location &&
      job.issues.includes('The job description is still collapsed.') && !job.issues.some(issue => issue.includes('still loading'));
    if (job.complete || challenge || collapsed) return;
    await page.waitForTimeout(100);
  } while (Date.now() < deadline);
}

// No generic click command exists; this optional click can only expand one verified description.
async function expandDescription(page, source, id, origin) {
  const handle = await page.evaluateHandle(invocation(locateExpansion, {source, id, origin}));
  try {
    const button = handle.asElement();
    if (button) {
      await button.click({timeout: 2000});
      await waitForJob(page, source, id, origin, true);
    }
  } finally { await handle.dispose(); }
}

module.exports = {readFeed, readJob, waitForJob, waitForFeed, followFeedLink, followLinkedInCard, expandDescription,
  scripts: {feed: script(collectFeed), job: script(collectJob)}};
