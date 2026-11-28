const currencyPattern = '(?:US\\$|CA\\$|C\\$|A\\$|NZ\\$|S\\$|HK\\$|[$€£¥]|\\b(?:USD|EUR|GBP|CAD|AUD|NZD|CHF|SGD|HKD|JPY)\\b)';
const amountPattern = '\\d[\\d,]*(?:\\.\\d+)?(?:\\s*(?:[km]|thousand|million)\\b)?';
const ratePattern = '(?:/\\s*(?:hr|hour|yr|year|mo|month|wk|week)\\b|per (?:hour|year|month|week)\\b)';
const moneyPattern = new RegExp(`(?<currency>${currencyPattern})?\\s*(?<low>${amountPattern})(?:\\s*(?<lowPeriod>${ratePattern}))?(?:\\s*(?:[-–—]|to)\\s*(?<highCurrency>${currencyPattern})?\\s*(?<high>${amountPattern}))?(?:\\s*(?<suffixCurrency>${currencyPattern}))?(?:\\s*(?<highPeriod>${ratePattern}))?`, 'gi');
const currencyLabels = {USD:'$',EUR:'€',GBP:'£',CAD:'CA$',AUD:'A$',NZD:'NZ$',CHF:'CHF ',SGD:'S$',HKD:'HK$',JPY:'¥','US$':'$','C$':'CA$'};
const periods = [
  ['hr', /\b(?:hourly|per hour|an hour)\b|\/\s*(?:hr|hour)\b/i],
  ['yr', /\b(?:annual(?:ly)?|yearly|per (?:year|annum)|a year)\b|\/\s*(?:yr|year)\b/i],
  ['mo', /\b(?:monthly|per month|a month)\b|\/\s*(?:mo|month)\b/i],
  ['wk', /\b(?:weekly|per week|a week)\b|\/\s*(?:wk|week)\b/i],
];

// Expand explicit shorthand without converting currencies or pay periods.
function amountLabel(value, currency) {
  const number = Number(value.match(/^[\d,.]+/)[0].replaceAll(',', ''));
  const multiplier = /(?:m|million)$/i.test(value) ? 1e6 : /(?:k|thousand)$/i.test(value) ? 1e3 : 1;
  return (currencyLabels[currency.toUpperCase()] || currency) + (number * multiplier).toLocaleString('en-US', {maximumFractionDigits: 10});
}

// Never borrow a period from another amount or sentence.
function payPeriod(block, matches, index) {
  const match = matches[index], previous = matches[index - 1], next = matches[index + 1];
  const start = previous ? previous.index + previous[0].length : 0;
  const before = block.slice(start, match.index).split(/[;]|\.\s/).at(-1);
  const after = block.slice(match.index + match[0].length, next?.index).split(/[;]|\.\s/)[0];
  const explicit = match.groups.lowPeriod || match.groups.highPeriod || '';
  const candidates = periods.filter(([,pattern]) => pattern.test(explicit || before + ' ' + after));
  return candidates.length === 1 ? '/' + candidates[0][0] : '';
}

// Shared shorthand in "$80–100k" applies to both bounds.
function rangeLabel(groups, unit, period) {
  const {highCurrency, high} = groups;
  const suffix = high?.match(/(?:[km]|thousand|million)$/i)?.[0];
  const low = suffix && /^\d{1,3}$/.test(groups.low.trim()) ? groups.low.trim() + suffix : groups.low.trim();
  const first = amountLabel(low, unit), last = high ? amountLabel(high.trim(), highCurrency || unit) : first;
  const leftPeriod = groups.lowPeriod && groups.highPeriod && groups.lowPeriod !== groups.highPeriod;
  if (leftPeriod) return first + groups.lowPeriod + '–' + last + groups.highPeriod;
  return first + (last === first ? '' : '–' + last) + period;
}

// Keep numeric pay statements; source prose stays internal.
function compactPay(block) {
  const matches = [...block.matchAll(moneyPattern)], labels = [];
  for (const [index, match] of matches.entries()) {
    const {low, high, currency, highCurrency, suffixCurrency} = match.groups;
    const unit = currency || highCurrency || suffixCurrency || '';
    if (!unit && (!high || /^(?:19|20)\d{2}$/.test(low.trim()) || !/[,km]|\d{4}/i.test(low))) continue;
    const qualifier = block.slice(0, match.index).match(/\b(up to|at least|starting at|from)\s*$/i)?.[1];
    const bound = !high && qualifier ? (/up to/i.test(qualifier) ? '≤' : '≥') : '';
    labels.push(bound + rangeLabel(match.groups, unit, payPeriod(block, matches, index)));
  }
  return labels.length ? labels : /\bunpaid\b/i.test(block) ? ['Unpaid'] : [];
}

// Exact source compensation stays stored; only its presentation is shortened.
export function compensationLabel(text = '') {
  const labels = [];
  let heading = '';
  for (const block of text.split(/\n\s*\n/)) {
    const values = compactPay(heading + block);
    labels.push(...values);
    heading = !values.length && /^(?:annual|hourly|monthly|weekly)\s+(?:salary|pay|compensation)(?: range)?\s*:?\s*$/i.test(block.trim()) ? block + '\n' : '';
  }
  return [...new Set(labels)].join('\n');
}

// Copied DOM paragraphs and existing list items become one bullet each.
export function descriptionItems(text = '') {
  return text.split(/\r?\n/).map(line => line.trim().replace(/^(?:[-*–—]\s+|[•·●▪◦‣]\s*|\d+[.)]\s+)/, '')).filter(Boolean);
}
