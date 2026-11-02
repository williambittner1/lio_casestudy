import test from 'node:test';
import assert from 'node:assert/strict';
import { quoteFieldTarget, savedQuoteData, savedQuoteFieldTarget } from '../src/lib/quoteLinks.ts';
import { swapAlternative } from '../src/lib/requestPricing.ts';

const base = { positionDescription: 'Picture', unitPrice: 1438, amount: 1, unit: 'piece', totalPrice: 1438 };
const alternative = { positionDescription: 'Alternative picture', unitPrice: 1926, amount: 1, unit: 'piece', totalPrice: 1926 };
const initial = { vendorName: 'Gregg', vatId: 'DE123', totalCost: 2092.02, orderLines: [base], alternativeOrderLines: [alternative] };
const lines = [{ ...base, previewSource: 'Line 1' }];
const alternatives = [{ ...alternative, previewSource: 'Alternative 1' }];
const target = (field, currentLines = lines, currentAlternatives = alternatives, vendor = 'Gregg') =>
  quoteFieldTarget(field, initial, vendor, 'DE123', currentLines, currentAlternatives);

test('links are removed when a field is edited and return when it is restored', () => {
  assert.equal(target('Vendor name'), 'vendor-name');
  assert.equal(target('Vendor name', lines, alternatives, 'Different supplier'), null);
  assert.equal(target('Line 1 description'), 'line-description-0');
  assert.equal(target('Line 1 description', [{ ...lines[0], positionDescription: 'Edited' }]), null);
  assert.equal(target('Line 1 unit price', [{ ...lines[0], unitPrice: 100 }]), null);
  assert.equal(target('Line 1 amount'), 'line-amount-0');
  assert.equal(target('Line 1 amount', [{ ...lines[0], amount: 2 }]), null);
  assert.equal(target('Line 1 unit'), 'line-unit-0');
  assert.equal(target('Line 1 unit', [{ ...lines[0], unit: 'box' }]), null);
  assert.equal(target('Line 1 description'), 'line-description-0');
});

test('links follow the original item when alternatives are swapped', () => {
  const swapped = swapAlternative(lines, alternatives, 0, 0);
  assert.equal(target('Line 1 description', swapped.lines, swapped.alternatives), 'alternative-0');
  assert.equal(target('Alternative 1 unit price', swapped.lines, swapped.alternatives), 'line-unit-price-0');
  assert.equal(target('Line 3 description'), null);
  assert.equal(target('unknown'), null);
});

test('saved requests rebuild matches from current data and map each line field separately', () => {
  const request = { title: 'Quote', vendor_name: 'Gregg', vat_id: null, department: null,
    commodity_group_id: 15, commodity_groups: { name: 'Office' }, total_cost: 1758,
    order_lines: [
      { position_description: 'Picture', unit_price: 1438, amount: 1, unit: 'piece', total_price: 1438 },
      { position_description: 'Shipping', unit_price: 320, amount: 1, unit: 'piece', total_price: 320 },
    ] };
  const data = savedQuoteData(request);
  assert.equal(data.vendorName, 'Gregg');
  assert.equal(data.vatId, '');
  assert.equal(data.orderLines[1].positionDescription, 'Shipping');
  assert.deepEqual(data.alternativeOrderLines, []);
  assert.equal(savedQuoteFieldTarget('Vendor name', 2), 'saved-vendor-name');
  assert.equal(savedQuoteFieldTarget('Total cost', 2), 'saved-total-cost');
  assert.equal(savedQuoteFieldTarget('Line 1 amount', 2), 'saved-line-0-amount');
  assert.equal(savedQuoteFieldTarget('Line 2 amount', 2), 'saved-line-1-amount');
  assert.equal(savedQuoteFieldTarget('Line 2 line total', 2), 'saved-line-1-total');
  assert.equal(savedQuoteFieldTarget('Line 3 amount', 2), null);
  assert.equal(savedQuoteFieldTarget('Alternative 1 amount', 2), null);
});

test('the original quoted total links only while the saved input still matches', () => {
  assert.equal(quoteFieldTarget('Total cost', initial, 'Gregg', 'DE123', lines, alternatives, '2092.02'), 'request-total');
  assert.equal(quoteFieldTarget('Total cost', initial, 'Gregg', 'DE123', lines, alternatives, ''), null);
  assert.equal(quoteFieldTarget('Total cost', initial, 'Gregg', 'DE123', lines, alternatives, '100'), null);
});
