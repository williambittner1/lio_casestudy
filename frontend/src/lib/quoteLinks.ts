import type { ExtractedVendorData } from './api';
import type { OrderLine } from './requestPricing';
import type { ProcurementRequestWithDetails } from '@/types/database';

// Saved requests keep the current fields and original PDF, but not the original
// extraction or its coordinates. Re-match their current values without an AI call.
export function savedQuoteData(request: ProcurementRequestWithDetails): ExtractedVendorData {
  return {
    title: request.title, vendorName: request.vendor_name, vatId: request.vat_id || '',
    department: request.department || '', commodityGroupId: request.commodity_group_id,
    commodityGroupName: request.commodity_groups?.name || null, totalCost: request.total_cost ?? 0,
    orderLines: (request.order_lines || []).map(line => ({
      positionDescription: line.position_description, unitPrice: line.unit_price,
      amount: line.amount, unit: line.unit, totalPrice: line.total_price,
    })),
    alternativeOrderLines: [],
  };
}

export function savedQuoteFieldTarget(field: string, lineCount: number): string | null {
  if (field === 'Vendor name') return 'saved-vendor-name';
  if (field === 'VAT ID') return 'saved-vendor-vat';
  if (field === 'Total cost') return 'saved-total-cost';
  const match = /^Line (\d+) (description|unit price|amount|unit|line total)$/.exec(field);
  if (!match) return null;
  const index = Number(match[1]) - 1;
  if (index < 0 || index >= lineCount) return null;
  const suffix = { description: 'description', 'unit price': 'unit-price', amount: 'amount', unit: 'unit', 'line total': 'total' }[match[2]];
  return `saved-line-${index}-${suffix}`;
}

// A link is only a possible text match to the original extraction, not provenance.
export function quoteFieldTarget(
  field: string,
  initial: ExtractedVendorData | null,
  vendorName: string,
  vatId: string,
  orderLines: OrderLine[],
  alternativeLines: OrderLine[],
  totalCost?: string,
): string | null {
  if (!initial) return null;
  if (field === 'Vendor name') return vendorName === initial.vendorName ? 'vendor-name' : null;
  if (field === 'VAT ID') return vatId === initial.vatId ? 'vendor-vat' : null;
  if (field === 'Total cost') return totalCost !== undefined && totalCost.trim() !== '' && Number(totalCost) === initial.totalCost ? 'request-total' : null;
  const match = /^(Line|Alternative) (\d+) (description|unit price|amount|unit|line total)$/.exec(field);
  if (!match) return null;
  const source = `${match[1]} ${match[2]}`;
  const original = (match[1] === 'Line' ? initial.orderLines : initial.alternativeOrderLines)?.[Number(match[2]) - 1];
  if (!original) return null;
  const key = match[3] === 'description' ? 'positionDescription' : match[3] === 'unit price' ? 'unitPrice' : match[3] === 'amount' ? 'amount' : match[3] === 'unit' ? 'unit' : 'totalPrice';
  const lineIndex = orderLines.findIndex(line => line.previewSource === source && line[key] === original[key]);
  if (lineIndex >= 0) return `${key === 'positionDescription' ? 'line-description' : key === 'unitPrice' ? 'line-unit-price' : key === 'amount' ? 'line-amount' : key === 'unit' ? 'line-unit' : 'line-total'}-${lineIndex}`;
  const alternativeIndex = alternativeLines.findIndex(line => line.previewSource === source && line[key] === original[key]);
  return alternativeIndex >= 0 ? `alternative-${alternativeIndex}` : null;
}
