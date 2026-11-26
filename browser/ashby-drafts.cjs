const crypto = require('node:crypto');
const {publicFormQuery} = require('./ashby.cjs');
const SAVE_HASH = '92e736d6645d95a56dc604468efe7632559b08c350fd470eaf541e39e89f18a7';
const LOCATION_HASH = 'bd60a76c77bdb2a361ed0d6108c284db0ac195d06ea238d6a5a0fc419bf0cfec';

// Bind form definitions to the official response for this exact tenant and job.
function trackAshbyForms(page, state) {
  state.ashbyForms = new Map();state.ashbyEdits = new Map();state.ashbyApplication = null;
  if (state.ashbyTracking) return;
  state.ashbyTracking = true;
  page.on('response', async response => {
    try {
      const request = response.request(), pageUrl = page.url();
      await acknowledgeAshbyEdit(request,response,state);
      if (state.ashbyEdits && page.url() !== pageUrl) return;
      if (!publicFormQuery(request) || request.postDataJSON().operationName !== 'ApiJobPosting') return;
      const job = (await response.json()).data?.jobPosting;
      if (page.url() !== pageUrl || !job) return;
      const controls = [job.applicationForm, ...(job.surveyForms || [])];
      if (controls.every(form=>form?.formControls?.length === 1 && form.formControls[0].title === 'Submit')) {
        const values = request.postDataJSON().variables, root = job.applicationForm;
        state.ashbyApplication = {pageUrl, tenant:values.organizationHostedJobsPageName, job:values.jobPostingId,
          form:root.id, definition:root.sourceFormDefinitionId, action:root.formControls[0].identifier,
          surveys:(job.surveyForms || []).map(form=>({formRenderId:form.id, actionIdentifier:form.formControls[0].identifier, sourceFormDefinitionId:form.sourceFormDefinitionId}))};
      }
      for (const form of controls) {
        if (!form?.id || !form.sourceFormDefinitionId) continue;
        const entries = (form.sections || []).flatMap(section=>section.fieldEntries || []);
        state.ashbyForms.set(form.id, {definition:form.sourceFormDefinitionId, pageUrl,
          fields:new Map(entries.map(entry=>[entry.field.path,entry.field])), values:new Map(entries.map(entry=>[entry.field.path,entry.fieldValue?.value])), controls:form.formControls});
      }
    } catch { /* Failed or changed definitions grant no draft permission. */ }
  });
}

// Only a planned answer can authorize its corresponding autosave, never another field.
function armAshbyAnswer(state, field, answer) {
  const binding = field.ashby_binding, form = state.ashbyForms?.get(binding?.form);
  const definition = form?.fields.get(binding?.path);
  if (!definition) return;
  let value = answer;
  if (definition.type === 'Boolean' && field.widget === 'ashby_boolean') value = answer === 'yes';
  if (definition.type === 'ValueSelect' && field.type === 'radio' && answer === true) {
    const options = definition.selectableValues.filter(option=>option.label.trim() === field.label.trim());
    if (options.length !== 1) return;
    value = options[0].value;
  }
  if (definition.type === 'MultiValueSelect' && field.type === 'checkbox' && answer === true) {
    const options = definition.selectableValues.filter(option=>option.label.trim() === field.label.trim());
    if (options.length !== 1) return;
    value = [options[0].value];
  }
  const previous = state.ashbyEdits.get(binding.form + ':' + binding.path);
  const saved = previous?.saved && JSON.stringify(previous.value) === JSON.stringify(value) || JSON.stringify(form.values?.get(binding.path)) === JSON.stringify(value);
  const edit = {form:binding.form, path:binding.path, definition:form.definition, pageUrl:form.pageUrl,
      value, saved, locationTypes:definition.type === 'Location' ? definition.locationTypes : null, expires:Date.now()+30000};
  state.ashbyEdits.set(binding.form + ':' + binding.path, edit);
  return edit;
}

// Exact operation hashes distinguish autosaves and location reads from application submission.
function ashbyDraftRequest(request, state) {
  try {
    const url = new URL(request.url()), frame = request.frame(), page = new URL(frame.url());
    if (request.method() !== 'POST' || url.origin !== 'https://jobs.ashbyhq.com' || url.pathname !== '/api/non-user-graphql'
        || page.origin !== url.origin || frame !== frame.page().mainFrame()) return false;
    const data = request.postDataJSON(), values = data.variables;
    if (Object.keys(data).some(key=>!['operationName','variables','query'].includes(key)) || !values) return false;
    const hash = crypto.createHash('sha256').update(data.query).digest('hex');
    const edits = [...(state.ashbyEdits?.values() || [])].filter(edit=>edit.pageUrl === page.href && Date.now()<edit.expires);
    if (hash === LOCATION_HASH && data.operationName === 'ApiAutocompleteGeoLocation') {
      const emptyLookup = values.text === '' && Array.isArray(values.locationTypes)
        && JSON.stringify(values.locationTypes) === JSON.stringify(['Country','Region','City']);
      return Object.keys(values).length === 2 && (emptyLookup || edits.some(edit=>edit.locationTypes && edit.value === values.text
        && JSON.stringify(edit.locationTypes) === JSON.stringify(values.locationTypes)));
    }
    if (hash !== SAVE_HASH || data.operationName !== 'ApiSetFormValue' || Object.keys(values).length !== 5
        || values.organizationHostedJobsPageName !== page.pathname.split('/')[1]) return false;
    const edit = edits.find(edit=>values.formRenderIdentifier === edit.form && values.path === edit.path
      && values.formDefinitionIdentifier === edit.definition && JSON.stringify(values.value) === JSON.stringify(edit.value));
    if (!edit) return false;
    edit.request = request;edit.saved = false;
    return true;
  } catch { return false; }
}
// A matching server field readback must acknowledge the planned value before submission.
async function acknowledgeAshbyEdit(request, response, state) {
  const edit = [...(state.ashbyEdits?.values() || [])].find(item=>item.request === request);
  if (!edit) return;
  try {
    const body = await response.json(), form = body.data?.setFormValue;
    const entry = form?.sections?.flatMap(section=>section.fieldEntries || []).find(item=>item.field.path === edit.path);
    edit.saved = response.ok() && !body.errors?.length && form.id === edit.form
      && JSON.stringify(entry?.fieldValue?.value) === JSON.stringify(edit.value);
    if (!edit.saved) edit.error = true;
  } catch { edit.error = true; }
}

// Browser values alone cannot prove an autosave reached the employer's draft.
async function waitForAshbySave(edit, check) {
  if (!edit) return;
  const deadline = Date.now()+8000;
  while (!edit.saved && !edit.error && Date.now()<deadline) {
    check();await new Promise(resolve=>setTimeout(resolve,100));
  }
  check();
  if (!edit.saved) throw new Error('Ashby did not confirm saving the planned field value.');
}
module.exports = {waitForAshbySave, trackAshbyForms, armAshbyAnswer, ashbyDraftRequest};
