import test from 'node:test';
import assert from 'node:assert/strict';
import { addAlternative, createQuotePricingBasis, editOrderLine, estimateQuoteTotal, parseRequestTotal, quoteBreakdown, quoteTotalDraft, removeOrderLine, swapAlternative } from '../src/lib/requestPricing.ts';

const quote = [
  { positionDescription: 'Moss picture', unitPrice: 894.08, amount: 1, unit: 'piece', totalPrice: 715.26 },
  { positionDescription: 'Logo', unitPrice: 622, amount: 1, unit: 'piece', totalPrice: 622 },
];

test('a missing quote total requires explicit input instead of silently becoming zero', () => {
  assert.equal(quoteTotalDraft(0, ['Total Cost']), '');
  assert.equal(quoteTotalDraft(0), '');
  assert.equal(quoteTotalDraft(1500, ['Total Cost']), '');
  assert.equal(quoteTotalDraft(2092.02, []), '2092.02');
  assert.equal(quoteTotalDraft(Number.NaN), '');
});

test('editing requires an explicitly entered, non-negative total', () => {
  assert.equal(parseRequestTotal(''), null);
  assert.equal(parseRequestTotal('   '), null);
  assert.equal(parseRequestTotal('-1'), null);
  assert.equal(parseRequestTotal('not a number'), null);
  assert.equal(parseRequestTotal('0'), 0);
  assert.equal(parseRequestTotal('1999.99'), 1999.99);
});

test('description edits preserve the discounted line and gross quote total', () => {
  const result = editOrderLine(quote, 0, 'positionDescription', 'Picture', 1847.19, true);
  assert.equal(result.lines[0].totalPrice, 715.26);
  assert.equal(result.totalCost, 1847.19);
  assert.equal(result.requiresReview, false);
  assert.equal(quote[0].positionDescription, 'Moss picture');
});

test('price edits and removal preserve the quote total but require renewed review', () => {
  const edited = editOrderLine(quote, 0, 'amount', 2, 1847.19, true);
  assert.equal(edited.lines[0].totalPrice, 1788.16);
  assert.equal(edited.totalCost, 1847.19);
  assert.equal(edited.requiresReview, true);
  const removed = removeOrderLine(quote, 0, 1847.19, true);
  assert.equal(removed.totalCost, 1847.19);
  assert.equal(removed.requiresReview, true);
});

test('manually created requests recalculate their total from line totals', () => {
  const result = editOrderLine(quote, 0, 'totalPrice', 700, 0, false);
  assert.equal(result.totalCost, 1322);
  assert.equal(result.requiresReview, false);
});

test('swapping an alternative retains the original as an option and fills an absent total', () => {
  const options = [{ positionDescription: 'Alternative moss', unitPrice: 1926, amount: 1, unit: 'piece', totalPrice: 0 }];
  const result = swapAlternative(quote, options, 0, 0);
  assert.equal(result.lines[0].positionDescription, 'Alternative moss');
  assert.equal(result.lines[0].totalPrice, 1926);
  assert.equal(result.alternatives[0].positionDescription, 'Moss picture');
  assert.equal(result.alternatives[0].totalPrice, 715.26);
  const restored = swapAlternative(result.lines, result.alternatives, 0, 0);
  assert.equal(restored.lines[0].totalPrice, 715.26);
});

test('adding an alternative removes it from available options and keeps existing lines', () => {
  const options = [{ positionDescription: 'Extra', unitPrice: 50, amount: 2, unit: 'piece', totalPrice: 100 }];
  const result = addAlternative(quote, options, 0);
  assert.equal(result.lines.length, 3);
  assert.equal(result.lines[2].totalPrice, 100);
  assert.deepEqual(result.alternatives, []);
  assert.equal(addAlternative(quote, options, 5), null);
  assert.equal(swapAlternative(quote, options, 5, 0), null);
});

const greggLines = [
  { positionDescription: 'Moss picture', unitPrice: 1438, amount: 1, unit: 'piece', totalPrice: 1438 },
  { positionDescription: 'Shipping', unitPrice: 320, amount: 1, unit: 'service', totalPrice: 320 },
];
const greggAlternative = [{ positionDescription: 'Alternative', unitPrice: 1926, amount: 1, unit: 'piece', totalPrice: 1926 }];

test('adding and swapping Gregg alternatives estimates the gross total with VAT', () => {
  const basis = createQuotePricingBasis(greggLines, 2092.02, 19);
  assert.ok(basis);
  assert.equal(estimateQuoteTotal(addAlternative(greggLines, greggAlternative, 0).lines, basis), 4383.96);
  const swapped = swapAlternative(greggLines, greggAlternative, 0, 0);
  assert.equal(estimateQuoteTotal(swapped.lines, basis), 2672.74);
  const restored = swapAlternative(swapped.lines, swapped.alternatives, 0, 0);
  assert.equal(estimateQuoteTotal(restored.lines, basis), 2092.02);
});

test('estimation responds to edits and removal without cumulative rounding drift', () => {
  const basis = createQuotePricingBasis(greggLines, 2092.02, 19);
  const edited = editOrderLine(greggLines, 0, 'amount', 2, 2092.02, true);
  assert.equal(estimateQuoteTotal(edited.lines, basis), 3803.24);
  assert.equal(estimateQuoteTotal(removeOrderLine(greggLines, 0, 2092.02, true).lines, basis), 380.8);
  assert.equal(estimateQuoteTotal(greggLines, basis), 2092.02);
});

test('unverified, inconsistent or invalid pricing cannot produce an estimate', () => {
  assert.equal(createQuotePricingBasis(greggLines, 2092.02, null), null);
  assert.equal(createQuotePricingBasis(greggLines, 2092.03, 19), null);
  assert.equal(createQuotePricingBasis(greggLines, 2092.02, -1), null);
  const basis = createQuotePricingBasis(greggLines, 2092.02, 19);
  assert.equal(estimateQuoteTotal([{ ...greggLines[0], totalPrice: -1 }], basis), null);
  assert.equal(estimateQuoteTotal(greggLines, null), null);
});

test('removing a selected alternative restores the original option, including after editing', () => {
  const added = addAlternative(greggLines, greggAlternative, 0);
  const edited = editOrderLine(added.lines, 2, 'unitPrice', 2000, 4383.96, true);
  const removed = removeOrderLine(edited.lines, 2, 4383.96, true);
  assert.deepEqual(removed.restoredAlternative, greggAlternative[0]);
  const addedAgain = addAlternative(removed.lines, [...added.alternatives, removed.restoredAlternative], 0);
  assert.equal(addedAgain.lines[2].unitPrice, 1926);
  assert.equal(removeOrderLine(greggLines, 0, 2092.02, true).restoredAlternative, null);
});

test('removing a swapped alternative restores it as an option', () => {
  const swapped = swapAlternative(greggLines, greggAlternative, 0, 0);
  const removed = removeOrderLine(swapped.lines, 0, 2672.74, true);
  assert.deepEqual(removed.restoredAlternative, greggAlternative[0]);
  assert.equal(swapped.alternatives[0].positionDescription, 'Moss picture');
});

test('verified breakdown shows net and VAT, and unavailable rates show no breakdown', () => {
  const basis = createQuotePricingBasis(greggLines, 2092.02, 19);
  assert.deepEqual(quoteBreakdown(greggLines, basis), { net: 1758, vat: 334.02, gross: 2092.02, rate: 19 });
  const added = addAlternative(greggLines, greggAlternative, 0);
  assert.deepEqual(quoteBreakdown(added.lines, basis), { net: 3684, vat: 699.96, gross: 4383.96, rate: 19 });
  assert.equal(quoteBreakdown(greggLines, null), null);
});
