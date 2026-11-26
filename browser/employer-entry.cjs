const {continueSuccessFactors} = require('./successfactors.cjs');
const {fresh} = require('./actions.cjs');
const {inspect} = require('./inspect.cjs');
const {checkpoint} = require('./control.cjs');

// SuccessFactors attaches an entry workflow to Apply; following its fallback href loses that workflow.
async function employerEntry(page, state, params) {
  const check = checkpoint(state), before = await fresh(page, state, params.snapshot_id);
  const current = new URL(before.url), link = before.links.find(item=>item.id === params.link_id);
  const target = link && new URL(link.href), posting = /\/(\d+)\/$/.exec(current.pathname)?.[1];
  if (current.hostname !== 'jobs.supermicro.com' || !posting || !link || target.origin !== current.origin
      || target.pathname !== `/talentcommunity/apply/${posting}/` || !/^apply now[ »]*$/i.test(link.text)
      || ![params.company,params.title].every(value=>typeof value === 'string' && value.trim()
        && before.text.toLowerCase().includes(value.toLowerCase()))) throw new Error('The employer application entry changed.');
  const handle = await page.evaluateHandle(id=>globalThis.__applyDesk.elements.get(id),link.id);
  const element = handle.asElement();
  if (!element || !await element.evaluate(node=>node.matches('a.dialogApplyBtn'))) throw new Error('The observed Apply control changed.');
  state.employerEntry = {pageUrl:before.url,posting,company:params.company,title:params.title,expires:Date.now()+15000,request:null};
  check();await element.click({signal:check.signal});
  await page.waitForLoadState('domcontentloaded',{timeout:10000}).catch(()=>{});
  let after = before;
  const deadline = Date.now() + 10000;
  while (after.snapshot_id === before.snapshot_id && Date.now() < deadline) {
    await page.waitForTimeout(500);check();
    after = await inspect(page,state);
  }
  if (after.snapshot_id === before.snapshot_id && await continueSuccessFactors(page,state,check)) after = await inspect(page,state);
  state.employerEntry = null;
  if (after.snapshot_id === before.snapshot_id) {
    const blocked = state.blocked.filter(item=>item.reason !== 'Unverified page telemetry.');
    throw new Error('The employer Apply control did not open its application.' + (blocked.length ? ' Blocked request: ' + blocked.map(item=>item.url).join('; ') : ''));
  }
  return after;
}
module.exports = {employerEntry};
