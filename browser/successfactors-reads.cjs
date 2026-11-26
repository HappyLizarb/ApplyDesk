// These DWR operations read policy text only; no other batched method is permitted.
function successFactorsRead(request, state) {
  if (state.successFactorsBinding?.tenant !== 'supermicro') return false;
  try {
    const frame = request.frame(), page = new URL(frame.url()), url = new URL(request.url());
    if (request.method() !== 'POST' || page.origin !== 'https://career4.successfactors.com'
        || !['/career','/careers'].includes(page.pathname) || url.origin !== page.origin || url.search
        || frame !== frame.page().mainFrame()) return false;
    const lines = (request.postData() || '').trim().split('\n'), values = {};
    for (const line of lines) {
      const index = line.indexOf('=');
      if (index < 1 || Object.hasOwn(values,line.slice(0,index))) return false;
      values[line.slice(0,index)] = line.slice(index+1);
    }
    const privacy = values['c0-scriptName'] === 'rcmDpcsControllerProxy'
      && values['c0-methodName'] === 'getDpscStatementByTypeAndCountry'
      && values['c0-param0'] === 'number:2' && values['c0-param1'] === 'string:US';
    const policy = values['c0-scriptName'] === 'pwdPolicyControllerProxy'
      && values['c0-methodName'] === 'getPasswordPolicy' && values['c0-param0'] === 'string:false';
    const keys = ['callCount','page','httpSessionId','scriptSessionId','c0-scriptName','c0-methodName','c0-id','c0-param0','batchId'];
    if (privacy) keys.push('c0-param1');
    return (privacy || policy) && Object.keys(values).length === keys.length && keys.every(key=>Object.hasOwn(values,key))
      && values.callCount === '1' && values['c0-id'] === '0' && /^\d+$/.test(values.batchId)
      && url.pathname === `/xi/ajax/remoting/call/plaincall/${values['c0-scriptName']}.${values['c0-methodName']}.dwr`;
  } catch { return false; }
}
module.exports = {successFactorsRead};
