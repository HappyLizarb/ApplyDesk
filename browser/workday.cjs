// Workday's email chooser opens the credential form without submitting credentials.
function emailEntry(snapshot, button) {
  let url;
  try { url = new URL(snapshot.url); } catch { return false; }
  return url.protocol === 'https:' && /^[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com$/.test(url.hostname)
    && !url.username && !url.password && !url.port
    && /\/job\/[^/]+\/[^/]+_JR\d+\/apply\/applyManually$/.test(url.pathname)
    && snapshot.fields.length === 0 && snapshot.text.includes('Create Account/Sign In')
    && button.text === 'Sign in with email' && !button.form_id && !button.form_action;
}

module.exports = {emailEntry};

// The account chooser is outside the login form and does not send credentials.
function accountEntry(snapshot, button) {
  return emailEntry({...snapshot, fields: []}, {...button, text: 'Sign in with email'})
    && button.text === 'Create Account' && snapshot.text.includes("Don't have an account yet?")
    && snapshot.fields.filter(field => field.type === 'password').length === 1;
}
module.exports.accountEntry = accountEntry;

// Workday initializes an empty draft before rendering fields; this does not submit it.
function draftInitialization(request) {
  try {
    const page = new URL(request.frame().url()), url = new URL(request.url());
    const tenant = /^([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com$/.exec(page.hostname)?.[1];
    const posting = /^\/[a-z]{2}-[A-Z]{2}\/[\w-]+\/job\/[^/]+\/([^/]+_JR\d+)\/apply\/applyManually$/.exec(page.pathname)?.[1];
    if (!tenant || !posting || page.protocol !== 'https:' || url.origin !== page.origin || url.search
        || request.method() !== 'POST' || request.frame() !== request.frame().page().mainFrame()) return false;
    if (url.pathname !== `/wday/cxs/${tenant}/jobpostings/${posting}/jobapplications`) return false;
    const body = request.postDataJSON();
    return body !== null && typeof body === 'object' && !Array.isArray(body) && Object.keys(body).length === 0;
  } catch { return false; }
}
module.exports.draftInitialization = draftInitialization;

// Workday explicitly marks schema reads separately from writes to the same form endpoints.
function workdayFormDefinition(request) {
  try {
    const frame=request.frame(), page=new URL(frame.url()), url=new URL(request.url());
    const tenant=/^([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com$/.exec(page.hostname)?.[1];
    if (!tenant || page.protocol!=='https:' || url.origin!==page.origin || url.search
        || !/\/job\/[^/]+\/[^/]+_JR\d+\/apply\/applyManually$/.test(page.pathname)
        || frame!==frame.page().mainFrame() || request.method()!=='POST'
        || request.headers()['form-definition']!=='true') return false;
    const match=new RegExp('^/wday/calypso/cxs/jobapplication/'+tenant+'/jobapplication/[a-f0-9]{32}/([a-z]+)$').exec(url.pathname);
    const keys={source:['sourcePopulatedFromUrl','source'],emailaddress:['emailAddress'],
      phonenumber:['countryPhoneCode','extension','phoneNumber'],previousworker:['candidateIsPreviousWorker'],
      educations:[],workexperiences:['currentlyWorkHere'],skills:['skills'],socials:['facebookAccount','linkedInAccount','twitterAccount'],resumeattachments:['attachments']}[match?.[1]];
    const body=request.postDataJSON();
    return !!keys && body!==null && typeof body==='object' && !Array.isArray(body)
      && Object.keys(body).every(key=>keys.includes(key))
      && (match[1]!=='previousworker' || !Object.hasOwn(body,'candidateIsPreviousWorker')
        || typeof body.candidateIsPreviousWorker==='boolean');
  } catch {return false;}
}
module.exports.workdayFormDefinition=workdayFormDefinition;
