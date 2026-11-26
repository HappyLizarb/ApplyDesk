// Declining this optional recording consent does not accept the alternative shown beside it.
function declarationText(text) {
  if (location.hostname !== 'jobs.ashbyhq.com') return text;
  for (const group of document.querySelectorAll('fieldset')) {
    const title = group.querySelector(':scope > .ashby-application-form-question-title');
    const checked = [...group.querySelectorAll('input[type="radio"]:checked')];
    if (title?.innerText.trim() !== 'Interview Recording Consent' || checked.length !== 1) continue;
    const label = [...checked[0].labels || []].map(node=>node.innerText.trim()).join(' ');
    const section = group.innerText;
    if (label === 'Opt out of recording' && section && text.split(section).length === 2) text = text.replace(section,'');
  }
  return text;
}
module.exports = {declarationText};
