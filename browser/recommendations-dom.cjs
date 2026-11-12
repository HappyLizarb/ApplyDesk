const {jobLink} = require('./recommendations-url.cjs');

// Rendered source elements are inspected locally; the full document never leaves this module.
function visible(element) {
  return !!(element && element.getClientRects().length && getComputedStyle(element).visibility !== 'hidden');
}

function clean(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

// Only the observed recommendation headings delimit feed cards; navigation and messages are excluded.
function feedParts(source, origin) {
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

function collectFeed({source, origin}) {
  const {headings, anchors} = feedParts(source, origin), links = new Map(), challenge = challengePage();
  const signedOut = [...document.querySelectorAll('input[type="password"]')].some(visible);
  for (const anchor of anchors) {
    const link = jobLink(anchor.href, source, origin);
    if (links.size < 50 && !links.has(link.id)) links.set(link.id, link);
  }
  return {ready: !signedOut && !challenge && links.size > 0, loading: headings.length > 0 && !links.size,
    challenge, links: [...links.values()], limited: true};
}

// Only a captured recommendation anchor can start a same-document source navigation.
function locateFeedLink({source, id, url, origin}) {
  if (location.origin !== origin || challengePage()) return null;
  const anchors = feedParts(source, origin).anchors.filter(anchor => anchor.href === url &&
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

// A title candidate is valid only beside one visible company and its job location.
function linkedinHeader(element, description, pageTitle) {
  const title = clean(element.innerText);
  let header = element.parentElement;
  for (let depth = 0; header && depth < 5 && header !== document.body; depth++, header = header.parentElement) {
    if (header.contains(description)) break;
    const companies = [...header.querySelectorAll('a[href]')].filter(anchor => visible(anchor) && /^\/company\//.test(new URL(anchor.href).pathname));
    const names = [...new Set(companies.map(anchor => clean(anchor.querySelector('[aria-label^="Company, "]')?.getAttribute('aria-label')?.replace(/^Company, /, '').replace(/\.$/, '') || anchor.innerText)).filter(Boolean))];
    if (names.length !== 1 || (pageTitle.length === 3 && pageTitle[1] !== names[0])) continue;
    const lines = header.innerText.split('\n').map(clean).filter(Boolean), titleIndex = lines.indexOf(title);
    const location = titleIndex >= 0 ? (lines[titleIndex + 1] || '').split('·')[0].trim() : '';
    if (!location) continue;
    let panel = header.parentElement;
    for (let level = 0; panel && level < 6 && !panel.contains(description); level++) panel = panel.parentElement;
    return {header, panel: panel && panel !== document.body && panel.contains(description) ? panel : header,
      description, title, company: names[0], location, url: element.href || window.location.href};
  }
  return {};
}

// The ID-bearing description must agree with one distinct title/company header.
function linkedinParts(id, origin) {
  const description = document.getElementById('JobDetails_AboutTheJob_' + id);
  if (!visible(description)) return {};
  const pageTitle = document.title.split(' | '), namedPage = pageTitle.length === 3 && pageTitle[2] === 'LinkedIn';
  const titles = [...document.querySelectorAll('a[href]')].filter(anchor => visible(anchor) &&
    /^\/jobs\/view\//.test(new URL(anchor.href).pathname) && jobLink(anchor.href, 'linkedin', origin)?.id === id &&
    clean(anchor.innerText) && (!namedPage || clean(anchor.innerText) === pageTitle[0]));
  if (namedPage) titles.push(...[...document.querySelectorAll('p')].filter(element => visible(element) && clean(element.innerText) === pageTitle[0]));
  const headers = new Map();
  for (const title of titles) {
    const parts = linkedinHeader(title, description, namedPage ? pageTitle : []);
    if (parts.header) headers.set(parts.header, parts);
  }
  return headers.size === 1 ? [...headers.values()][0] : {};
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
  const clipped = [parts.description, ...parts.description.querySelectorAll('*')].filter(visible).some(element => {
    const style = getComputedStyle(element);
    return Number(style.webkitLineClamp) > 0 && ['hidden', 'clip'].includes(style.overflowY) && element.scrollHeight > element.clientHeight + 1;
  });
  if (folded || clipped) issues.push('The job description is still collapsed.');
  if (parts.description.querySelector('iframe') || [...parts.description.querySelectorAll('*')].some(element => element.shadowRoot)) issues.push('The description contains unsupported embedded content.');
  if ([...parts.description.querySelectorAll('[aria-busy="true"],[role="progressbar"]')].some(visible) || /security verification|verify you are human/i.test(document.title)) issues.push('The job description is still loading or requires a security check.');
  return issues;
}

// The executable functions are fixed application code; callers supply only source and job identifiers.
function script(fn) {
  const helpers = [jobLink, visible, clean, feedParts, challengePage, collectFeed, locateFeedLink, handshakeParts, linkedinHeader, linkedinParts, expansionButton, locateExpansion, collectJob, captureIssues];
  return `(args => { ${helpers.map(helper => helper.toString()).join('\n')} return ${fn.name}(args); })`;
}

// Playwright evaluates string expressions directly; invoke the fixed function with JSON-only arguments.
function invocation(fn, args) {
  return `${script(fn)}(${JSON.stringify(args)})`;
}

async function readFeed(page, source, origin) {
  return page.evaluate(invocation(collectFeed, {source, origin}));
}

async function readJob(page, source, id, origin) {
  return page.evaluate(invocation(collectJob, {source, id, origin}));
}

// Poll visible reads from the controller; no in-page polling function or CSP exception is installed.
async function waitForFeed(page, source, origin) {
  const deadline = Date.now() + 6000;
  do {
    const feed = await readFeed(page, source, origin);
    if (feed.ready || feed.challenge) return;
    await page.waitForTimeout(100);
  } while (Date.now() < deadline);
  throw new Error('The recommendation feed did not finish rendering.');
}

// A feed rerender may detach an anchor before clicking; only that case gets a fresh verified handle.
async function followFeedLink(page, candidate, source, origin) {
  const beforeUrl = page.url();
  for (let attempt = 0; attempt < 3; attempt++) {
    if (jobLink(page.url(), source, origin)?.id === candidate.id) return;
    const handle = await page.evaluateHandle(invocation(locateFeedLink, {...candidate, source, origin}));
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

module.exports = {readFeed, readJob, waitForJob, waitForFeed, followFeedLink, expandDescription,
  scripts: {feed: script(collectFeed), job: script(collectJob)}};
