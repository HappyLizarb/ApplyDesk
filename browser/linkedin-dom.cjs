// Locate only the job description, excluding the company and recommendation sections beside it.
function linkedinDescription(id) {
  const exact = document.getElementById('JobDetails_AboutTheJob_' + id);
  if (visible(exact)) return exact;
  if ([...document.querySelectorAll('[id^="JobDetails_AboutTheJob_"]')].some(visible)) return null;
  const headings = [...document.querySelectorAll('h2')].filter(element => visible(element) && clean(element.innerText) === 'About the job');
  if (headings.length !== 1) return null;
  let scope = headings[0].parentElement;
  for (let depth = 0; scope && scope !== document.body && depth < 4; depth++, scope = scope.parentElement) {
    if ([...scope.querySelectorAll('h2')].some(heading => heading !== headings[0] && visible(heading))) return null;
    if (clean(scope.innerText).length >= 200) return scope;
  }
  return null;
}

// A scoped header binds the job title to its preceding company link and following location.
function linkedinHeader(element, description, pageTitle) {
  const title = clean(element.innerText);
  let header = element.parentElement;
  for (let depth = 0; header && depth < 5 && header !== document.body; depth++, header = header.parentElement) {
    if (header.contains(description) && pageTitle.length !== 3) break;
    const companies = [...header.querySelectorAll('a[href]')].filter(anchor => visible(anchor) &&
      /^\/company\//.test(new URL(anchor.href).pathname) && (anchor.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING));
    const names = [...new Set(companies.map(anchor => {
      const label = clean(anchor.getAttribute('aria-label') ||
        anchor.querySelector('[aria-label^="Company, "]')?.getAttribute('aria-label') || anchor.innerText);
      return label.startsWith('Company, ') ? label.replace(/^Company, /, '').replace(/\.$/, '') : label;
    }).filter(Boolean))];
    if (names.length !== 1 || (pageTitle.length === 3 && pageTitle[1] !== names[0])) continue;
    const lines = header.innerText.split('\n').map(clean).filter(Boolean), titleIndex = lines.findIndex(line => line.includes(title));
    const location = titleIndex < 0 ? '' : (lines.slice(titleIndex + 1, titleIndex + 4)
      .find(line => !/^(verified job|on-site|hybrid|remote|full-time|part-time)$/i.test(line)) || '').split('·')[0].trim();
    if (!location) continue;
    let panel = header;
    for (let level = 0; panel && level < 6 && !panel.contains(description); level++) panel = panel.parentElement;
    if (!panel || panel === document.body || !panel.contains(description)) continue;
    return {header, panel, description, title, company: names[0], location, url: element.href || window.location.href};
  }
  return {};
}

// Current URL identity and one matching title/company header also support heading-based descriptions.
function linkedinParts(id, origin) {
  const description = linkedinDescription(id);
  if (!description) return {};
  const pageTitle = document.title.split(' | '), namedPage = pageTitle.length === 3 && pageTitle[2] === 'LinkedIn';
  const titles = [...document.querySelectorAll('a[href]')].filter(anchor => visible(anchor) &&
    /^\/jobs\/view\//.test(new URL(anchor.href).pathname) && jobLink(anchor.href, 'linkedin', origin)?.id === id &&
    clean(anchor.innerText) && (!namedPage || clean(anchor.innerText) === pageTitle[0]));
  if (namedPage) titles.push(...[...document.querySelectorAll('h1,p')].filter(element => visible(element) && clean(element.innerText) === pageTitle[0]));
  const headers = new Map();
  for (const title of titles) {
    const parts = linkedinHeader(title, description, namedPage ? pageTitle : []);
    if (parts.header) headers.set(parts.header, parts);
  }
  return headers.size === 1 ? [...headers.values()][0] : {};
}

// LinkedIn's visible result buttons contain a separate, explicitly labeled Dismiss control.
function linkedinCards() {
  const main = document.querySelector('main,[role="main"]');
  const cards = new Map();
  for (const dismiss of main?.querySelectorAll('button,[role="button"]') || []) {
    const match = clean(dismiss.getAttribute('aria-label') || dismiss.innerText).match(/^Dismiss (.+) job$/);
    const card = match && dismiss.parentElement?.closest('button,[role="button"]');
    if (!card || !visible(card) || card.closest('form') || card === dismiss) continue;
    const label = clean(card.innerText || card.getAttribute('aria-label'));
    const jobId = card.getAttribute('componentkey')?.match(/^job-card-component-ref-(\d{1,22})$/)?.[1] || '';
    if (label && label.includes(match[1])) cards.set(card, {element: card, title: match[1], label, jobId});
  }
  return [...cards.values()];
}

// Reacquire one exact observed card; never click its nested dismissal or an ambiguous duplicate.
function locateLinkedInCard({origin, title, label, jobId}) {
  if (location.origin !== origin || challengePage()) return null;
  const matches = linkedinCards().filter(card => card.title === title && (jobId ? card.jobId === jobId : card.label === label));
  return matches.length === 1 ? matches[0].element : null;
}

// Dispatch on the verified outer card so a centered Dismiss control cannot receive the click.
function activateLinkedInCard(args) {
  const card = locateLinkedInCard(args);
  if (!card) throw new Error('The observed job card is unavailable or ambiguous.');
  const before = location.href;
  const selected = card.getAttribute('aria-selected') === 'true' || card.getAttribute('aria-pressed') === 'true' ||
    /^(selected[, ]|current[, ])/i.test(card.getAttribute('aria-label') || '');
  card.dispatchEvent(new MouseEvent('click', {bubbles: true}));
  return {before, selected};
}

module.exports = {linkedinHeader, linkedinDescription, linkedinParts, linkedinCards, locateLinkedInCard, activateLinkedInCard};
