const assert = require('node:assert/strict');
const {publicFormQuery} = require('../browser/ashby.cjs');
const queries = require('./fixtures/ashby-form-queries.json');
const frame = {url:()=> 'https://jobs.ashbyhq.com/sample-employer/00000000-0000-4000-8000-000000000001/application',page:()=>({mainFrame:()=>frame})};
const request = body => ({method:()=> 'POST',url:()=> 'https://jobs.ashbyhq.com/api/non-user-graphql',frame:()=>frame,postDataJSON:()=>body});
for (const query of queries) {
  assert.equal(publicFormQuery(request(query)),true);
  assert.equal(publicFormQuery(request({...query,query:query.query+'\nmutation CreateApplication { submit }'})),false);
  assert.equal(publicFormQuery(request({...query,variables:{...query.variables,organizationHostedJobsPageName:'other'}})),false);
  assert.equal(publicFormQuery(request({...query,variables:{...query.variables,email:'private@example.test'}})),false);
}
console.log('Observed public form queries allowed; mutations, different tenants and extra personal data blocked.');
