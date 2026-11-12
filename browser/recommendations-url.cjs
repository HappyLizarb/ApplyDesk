const START_URLS = {linkedin: 'https://www.linkedin.com/jobs/', handshake: 'https://app.joinhandshake.com/home'};

// Only observed links on the selected platform can identify a recommendation.
function jobLink(raw, source, testOrigin = '') {
  try {
    const url = new URL(raw);
    const origin = testOrigin || new URL(START_URLS[source]).origin;
    if (url.origin !== origin || url.username || url.password) return null;
    const match = source === 'handshake' ? url.pathname.match(/^\/job-search\/(\d+)\/?$/) :
      url.pathname.match(/^\/jobs\/view\/(?:[^/]+-)?(\d+)\/?$/);
    const id = match?.[1] || (source === 'linkedin' && /^\/jobs\/search-results\/?$/.test(url.pathname) ? url.searchParams.get('currentJobId') : null);
    return /^\d{1,22}$/.test(id || '') ? {id, url: url.href} : null;
  } catch { return null; }
}

// Test origins are available only to explicit local fixtures, never ordinary app callers.
function sourceOrigin(session) {
  return session.options.allowLocal && session.options.sourceTestOrigin || new URL(START_URLS[session.state.source]).origin;
}

module.exports = {START_URLS, jobLink, sourceOrigin};
