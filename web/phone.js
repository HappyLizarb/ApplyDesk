import {h} from './ui.js';
import {callingRegions} from './calling-regions.js';

const callingCodes=Object.keys(callingRegions),regionNames=new Intl.DisplayNames(['en'],{type:'region'});

// Labels describe shared numbering regions without assigning the user a residence country.
function callingLabel(code) {
  const regions=callingRegions[code];
  if(!regions)return `${code} (Other region)`;
  const names=regions.map(region=>region==='001'?'International services':regionNames.of(region));
  return `${code} (${names.join(' / ')})`;
}

// Only ten-digit numbers without extensions or an international prefix receive the requested mask.
function formattedNumber(value) {
  if(!/^[\d\s()-]+$/.test(value))return value;
  const digits=value.replace(/\D/g,'');
  if(digits.length!==10)return value;
  return `(${digits.slice(0,3)})-${digits.slice(3,6)}-${digits.slice(6)}`;
}

// Split only an explicit prefix; the narrow closed control displays the code, with full menu labels.
export function phoneFields(identity) {
  let code=identity.phone_country_code||'',number=identity.phone||'';
  if(!code&&number.startsWith('+')){
    const matches=callingCodes.filter(value=>number.startsWith(value)).sort((a,b)=>b.length-a.length);
    if(matches.length){code=matches[0];number=number.slice(code.length).trimStart();}
  }
  const codes=[...new Set([...callingCodes,...(code?[code]:[])])];
  return `<div class="phone-fields"><label class="phone-code" for="profile-phone-code">Calling code<select id="profile-phone-code" name="identity.phone_country_code" title="${h(code?callingLabel(code):'Calling code')}"><option value="">Code</option>${codes.map(value=>`<option value="${h(value)}" ${value===code?'selected':''}>${h(callingLabel(value))}</option>`).join('')}</select><span class="phone-code-display" aria-hidden="true"><span>${h(code||'Code')}</span><span>⌄</span></span></label>
    <label for="profile-identity-phone">Phone<input id="profile-identity-phone" type="tel" name="identity.phone" value="${h(formattedNumber(number))}" autocomplete="tel-national" maxlength="500"></label></div>`;
}

// Preserve the caret's digit position while formatting typing, paste, and edits in the middle.
export function bindPhone(form) {
  const input=form.elements.namedItem('identity.phone'),code=form.elements.namedItem('identity.phone_country_code');
  input.addEventListener('input',event=>{
    if(event.isComposing)return;
    const formatted=formattedNumber(input.value);
    if(formatted===input.value)return;
    const digitsBefore=input.value.slice(0,input.selectionStart).replace(/\D/g,'').length;
    input.value=formatted;
    let position=0,count=0;
    while(position<formatted.length&&count<digitsBefore){if(/\d/.test(formatted[position]))count++;position++;}
    input.setSelectionRange(position,position);
  });
  code.addEventListener('change',()=>{
    form.querySelector('.phone-code-display>span').textContent=code.value||'Code';
    code.title=code.value?callingLabel(code.value):'Calling code';
  });
}
