import { test } from 'node:test';
import assert from 'node:assert/strict';
import { applyCatalogArticle, editOrderLine, sameSupplier } from '../src/lib/requestPricing.ts';

const line = { positionDescription: 'Quoted item', unitPrice: 10, amount: 3, unit: 'pcs', totalPrice: 30, previewSource: 'Line 1' };
const article = { article_number: 'A-1', supplier_name: 'Acme Supplier', description: 'Docking station', unit_price: '12.50', currency: 'EUR', unit: 'piece' };

test('selecting a catalog article applies the negotiated price and preserves request quantity', () => {
  const [selected] = applyCatalogArticle([line], 0, article);
  assert.equal(selected.totalPrice, 37.5);
  assert.equal(selected.amount, 3);
  assert.equal(selected.unitPrice, 12.5);
  assert.equal(selected.previewSource, undefined);
  assert.equal(selected.catalogArticle.articleNumber, 'A-1');
  assert.equal(line.positionDescription, 'Quoted item');
});

test('reject unsupported catalog prices and suppliers and remove attribution when a price changes', () => {
  assert.equal(applyCatalogArticle([line], 0, { ...article, currency: 'USD' }), null);
  assert.equal(applyCatalogArticle([line], 0, { ...article, unit_price: 'invalid' }), null);
  assert.equal(applyCatalogArticle([line], 0, { ...article, supplier_name: null }), null);
  const selected = applyCatalogArticle([line], 0, article);
  assert.equal(editOrderLine(selected, 0, 'unitPrice', 20, 37.5, false).lines[0].catalogArticle, undefined);
  assert.equal(editOrderLine(selected, 0, 'amount', 4, 37.5, false).lines[0].catalogArticle.articleNumber, 'A-1');
  assert.equal(sameSupplier(' ACME Supplier ', 'acme supplier'), true);
  assert.equal(sameSupplier('Acme Supplier', 'Other Supplier'), false);
});
