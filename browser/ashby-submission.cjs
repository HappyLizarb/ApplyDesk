const crypto = require('node:crypto');
const SUBMIT_HASH = '1379480b7ee730be01ada027f0312c1046fa9d4e09a00b395628d3a96a9d8169';

// Bind the final action to the exact application and surveys returned by the employer.
function annotateAshbySubmission(snapshot, state) {
  const binding = state.ashbyApplication;
  if (!binding || binding.pageUrl !== snapshot.url) return;
  const files = (state.ashbyFiles || []).filter(file=>file.verified && file.binding.form === binding.form && file.binding.path === '_systemfield_resume');
  if (files.length !== 1 || [...state.ashbyEdits.values()].some(edit=>!edit.saved)) {
    if (state.ashbyEdits?.size) snapshot.warnings.push('The employer has not confirmed all planned answers and the resume attachment.');
    return;
  }
  snapshot.submission_target = 'https://jobs.ashbyhq.com/api/non-user-graphql';
  snapshot.submission_provider = 'ashby';
  snapshot.ashby_submission = binding;
}

// Whitespace and commas are insignificant in these fixed, literal-free GraphQL documents.
function ashbySubmissionRequest(request, permit) {
  try {
    const binding = permit.ashby, data = request.postDataJSON(), values = data.variables;
    if (!binding || request.method() !== 'POST' || request.frame().url() !== binding.pageUrl
        || request.frame() !== request.frame().page().mainFrame() || data.operationName !== 'ApiSubmitMultipleFormsAction'
        || Object.keys(data).length !== 3 || typeof data.query !== 'string'
        || crypto.createHash('sha256').update(data.query.replace(/[\s,]+/g,'')).digest('hex') !== SUBMIT_HASH) return false;
    const names = ['organizationHostedJobsPageName','jobPostingId','applicationFormRenderIdentifier','applicationFormActionIdentifier',
      'applicationFormDefinitionIdentifier','surveyIdentifiers','recaptchaToken','sourceAttributionCode',
      'viewedAutomatedProcessingLegalNoticeRuleId','deviceFingerprint','applicationRequestId'];
    if (!values || Object.keys(values).some(key=>!names.includes(key)) || values.organizationHostedJobsPageName !== binding.tenant
        || values.jobPostingId !== binding.job || values.applicationFormRenderIdentifier !== binding.form
        || values.applicationFormDefinitionIdentifier !== binding.definition || values.applicationFormActionIdentifier !== binding.action
        || typeof values.recaptchaToken !== 'string' || !values.recaptchaToken || values.recaptchaToken.length > 20000) return false;
    if (JSON.stringify(values.surveyIdentifiers) !== JSON.stringify(binding.surveys)) return false;
    permit.request = request;
    return true;
  } catch { return false; }
}

// Only the response to this reserved submission request can provide server receipt evidence.
function watchAshbyReceipt(page, permit) {
  if (!permit.ashby) return;
  const listener = async response => {
    if (response.request() !== permit.request) return;
    page.removeListener('response', listener);
    try {
      const body = await response.json(), result = body.data?.submitMultipleFormsAction;
      if (response.ok() && !body.errors?.length && result?.applicationFormResult?.__typename === 'FormSubmitSuccess') {
        permit.receipt = 'FormSubmitSuccess';
        permit.surveys_confirmed = result.surveyFormResults?.every(form=>form.__typename === 'FormSubmitSuccess') === true;
      }
    } catch { /* No receipt is inferred from a click, status code, or unreadable response. */ }
  };
  page.on('response', listener);
  permit.cleanup = ()=>page.removeListener('response', listener);
}
module.exports = {annotateAshbySubmission, ashbySubmissionRequest, watchAshbyReceipt};
