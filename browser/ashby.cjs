const crypto = require('node:crypto');
const READ_QUERIES = {
  "ApiJobPosting": "e1e9e81eb52affddbe9e9dd6ed68ccbfe653e59d8a7402f237ac94094c89cff1",
  "ApiOrganizationFromHostedJobsPageName": "90e5a406dbfe8b7f4529188fbbadd4a9f3e85a2c81b5445eab28115f886257a2"
};

// These observed public queries load form definitions, never candidate data or mutations.
function publicFormQuery(request) {
  try {
    const url = new URL(request.url()), page = new URL(request.frame().url());
    if (request.method() !== 'POST' || url.origin !== 'https://jobs.ashbyhq.com' || url.pathname !== '/api/non-user-graphql'
        || page.origin !== url.origin || request.frame() !== request.frame().page().mainFrame()) return false;
    const [tenant, job] = page.pathname.split('/').filter(Boolean), data = request.postDataJSON();
    if (!tenant || !job || !data || Object.keys(data).some(key => !['operationName','variables','query'].includes(key))) return false;
    if (typeof data.query !== 'string' || crypto.createHash('sha256').update(data.query).digest('hex') !== READ_QUERIES[data.operationName]) return false;
    const values = data.variables;
    if (!values || values.organizationHostedJobsPageName !== tenant) return false;
    if (data.operationName === 'ApiJobPosting') return Object.keys(values).length === 2 && values.jobPostingId === job;
    return Object.keys(values).length === 2 && values.searchContext === 'JobPosting';
  } catch { return false; }
}

module.exports = {publicFormQuery};
