import assert from 'node:assert/strict';
import {compensationLabel, descriptionItems} from '../web/job-details.js';

// Presentation keeps the posted numbers and units without exposing source paragraphs.
const salaries = [
  ['Annual Compensation Range: $80,000 - $105,000', '$80,000–$105,000/yr'],
  ['Total Compensation Package: $110,000-$150,000 annually. This includes retirement and health benefits.', '$110,000–$150,000/yr'],
  ['The hourly rate range for this position is $35- $35.', '$35/hr'],
  ['Pay: $0/hour', '$0/hr'],
  ['Salary: USD 80k to 105k per year', '$80,000–$105,000/yr'],
  ['Salary: £60,000–£80,000 per annum', '£60,000–£80,000/yr'],
  ['Pay: CAD 40–50 per hour', 'CA$40–CA$50/hr'],
  ['Monthly pay: €5,000–€6,000', '€5,000–€6,000/mo'],
  ['Pay: $32.50/hr', '$32.5/hr'],
  ['Annual salary up to $100,000', '≤$100,000/yr'],
  ['Salary: $90,000', '$90,000'],
  ['Salary: 80000–100000 annually', '80,000–100,000/yr'],
  ['Salary: $80,000/yr\n\nSalary: $80,000/yr', '$80,000/yr'],
  ['Salary: $80,000/yr\n\nSalary: $90,000/yr', '$80,000/yr\n$90,000/yr'],
  ['Compensation: unpaid', 'Unpaid'],
  ['Salary competitive; 401(k) match 5%.', ''],
  ['Published 2024–2025', ''],
  ['Salary: $90,000. Employees receive a $500 stipend per month.', '$90,000\n$500/mo'],
  ['Signing bonus: $5,000. Base salary: $100,000 per year.', '$5,000\n$100,000/yr'],
  ['Salary: $80–100k per year', '$80,000–$100,000/yr'],
  ['Pay: $20/hr - $30/hr', '$20–$30/hr'],
  ['Pay: $20/hr or $50,000/yr', '$20/hr\n$50,000/yr'],
  ['Annual salary: $80,000 in California; $95,000 in New York.', '$80,000/yr\n$95,000'],
  ['Annual Compensation Range\n\n$80,000–$105,000', '$80,000–$105,000/yr'],
  ['', ''],
];
for (const [source, expected] of salaries) assert.equal(compensationLabel(source), expected, source);

assert.deepEqual(descriptionItems('First paragraph. Another sentence.\n\nSecond paragraph.\r\n• Third item\n- Fourth item\n1. Fifth item'),
  ['First paragraph. Another sentence.', 'Second paragraph.', 'Third item', 'Fourth item', 'Fifth item']);
assert.deepEqual(descriptionItems('•No space\n\n  \n·\u00a0\u00a0Copied Word bullet\n-5 degrees\n<img src=x onerror=alert(1)>'),
  ['No space', 'Copied Word bullet', '-5 degrees', '<img src=x onerror=alert(1)>']);
assert.deepEqual(descriptionItems(), []);
console.log(`${salaries.length} compensation and 3 paragraph-format checks passed.`);
