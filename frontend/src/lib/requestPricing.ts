export interface OrderLine {
  positionDescription: string;
  unitPrice: number;
  amount: number;
  unit: string;
  totalPrice: number;
  // Local UI state: remember the original offer when an alternative is selected.
  alternativeSource?: OrderLine;
  // Stable link to the initial quote extraction; never submitted to the backend.
  previewSource?: string;
  catalogArticle?: { articleNumber: string; supplierName: string };
}

export interface CatalogSelection {
  article_number: string;
  supplier_name: string | null;
  description: string;
  unit_price: string;
  currency: string;
  unit: string;
}

// A missing or zero model result needs explicit user input; zero can still be
// entered deliberately for a genuinely free quote.
export function quoteTotalDraft(total: number, missingFields?: string[] | null): string {
  if (!Number.isFinite(total) || total <= 0 || missingFields?.includes('Total Cost')) return '';
  return String(total);
}

export function parseRequestTotal(value: string): number | null {
  if (!value.trim()) return null;
  const total = Number(value);
  return Number.isFinite(total) && total >= 0 ? total : null;
}

export function applyCatalogArticle(lines: OrderLine[], index: number, article: CatalogSelection): OrderLine[] | null {
  const current = lines[index];
  const price = Number(article.unit_price);
  if (!current || !article.supplier_name || article.currency !== 'EUR' || !Number.isFinite(price) || price < 0) return null;
  return lines.map((line, i) => i === index ? {
    ...line,
    positionDescription: article.description,
    unitPrice: price,
    unit: article.unit,
    totalPrice: Math.round(price * line.amount * 100) / 100,
    previewSource: undefined,
    catalogArticle: { articleNumber: article.article_number, supplierName: article.supplier_name! },
  } : line);
}

export function sameSupplier(first: string, second: string): boolean {
  return first.trim().toLocaleLowerCase() === second.trim().toLocaleLowerCase();
}

export interface QuotePricingBasis {
  originalNetCents: number;
  originalGrossCents: number;
  taxRatePercent: number;
}

export function createQuotePricingBasis(
  lines: OrderLine[], grossTotal: number, taxRatePercent: number | null | undefined,
): QuotePricingBasis | null {
  if (taxRatePercent == null || !Number.isFinite(taxRatePercent) || taxRatePercent < 0 || taxRatePercent > 100 || !Number.isFinite(grossTotal)) return null;
  if (!lines.length || lines.some(line => !Number.isFinite(line.totalPrice) || line.totalPrice < 0)) return null;
  const originalNetCents = lines.reduce((sum, line) => sum + Math.round(line.totalPrice * 100), 0);
  const originalGrossCents = Math.round(grossTotal * 100);
  if (originalNetCents + Math.round(originalNetCents * taxRatePercent / 100) !== originalGrossCents) return null;
  return {
    originalNetCents,
    originalGrossCents,
    taxRatePercent,
  };
}

export function estimateQuoteTotal(lines: OrderLine[], basis: QuotePricingBasis | null): number | null {
  if (!basis || lines.some(line => !Number.isFinite(line.totalPrice) || line.totalPrice < 0)) return null;
  const netCents = lines.reduce((sum, line) => sum + Math.round(line.totalPrice * 100), 0);
  // Subtract the original rounded tax and add the new rounded tax, avoiding drift.
  const originalTax = Math.round(basis.originalNetCents * basis.taxRatePercent / 100);
  const updatedTax = Math.round(netCents * basis.taxRatePercent / 100);
  return (basis.originalGrossCents + netCents - basis.originalNetCents + updatedTax - originalTax) / 100;
}

export function quoteBreakdown(lines: OrderLine[], basis: QuotePricingBasis | null) {
  const gross = estimateQuoteTotal(lines, basis);
  if (gross === null || !basis) return null;
  const netCents = lines.reduce((sum, line) => sum + Math.round(line.totalPrice * 100), 0);
  return { net: netCents / 100, vat: (Math.round(gross * 100) - netCents) / 100, gross, rate: basis.taxRatePercent };
}

export function editOrderLine(
  lines: OrderLine[], index: number, field: keyof OrderLine, value: string | number,
  totalCost: number, hasQuote: boolean,
) {
  const updated = lines.map((line, i) => i === index ? { ...line, [field]: value } : line);
  if (['positionDescription', 'unitPrice', 'unit', 'totalPrice'].includes(field) && lines[index]?.[field] !== value) {
    updated[index].catalogArticle = undefined;
  }
  const financialChange = field === 'unitPrice' || field === 'amount' || field === 'totalPrice';
  if (field === 'unitPrice' || field === 'amount') {
    updated[index].totalPrice = Math.round(updated[index].unitPrice * updated[index].amount * 100) / 100;
  }
  return {
    lines: updated,
    totalCost: financialChange && !hasQuote
      ? updated.reduce((sum, line) => sum + line.totalPrice, 0)
      : totalCost,
    requiresReview: financialChange && hasQuote,
  };
}

export function removeOrderLine(lines: OrderLine[], index: number, totalCost: number, hasQuote: boolean) {
  const updated = lines.filter((_, i) => i !== index);
  return {
    lines: updated,
    totalCost: hasQuote ? totalCost : updated.reduce((sum, line) => sum + line.totalPrice, 0),
    requiresReview: hasQuote,
    restoredAlternative: lines[index]?.alternativeSource ?? null,
  };
}

// Some offers list an alternative's unit price but leave its line total blank.
function selectedAlternative(line: OrderLine): OrderLine {
  const { alternativeSource: _source, ...original } = line;
  return {
    ...original,
    totalPrice: line.totalPrice || Math.round(line.unitPrice * line.amount * 100) / 100,
    alternativeSource: original,
  };
}

export function swapAlternative(
  lines: OrderLine[], alternatives: OrderLine[], lineIndex: number, alternativeIndex: number,
) {
  if (!lines[lineIndex] || !alternatives[alternativeIndex]) return null;
  const oldLine = lines[lineIndex];
  return {
    lines: lines.map((line, index) => index === lineIndex ? selectedAlternative(alternatives[alternativeIndex]) : line),
    alternatives: alternatives.map((line, index) => index === alternativeIndex ? (oldLine.alternativeSource ?? oldLine) : line),
  };
}

export function addAlternative(lines: OrderLine[], alternatives: OrderLine[], alternativeIndex: number) {
  if (!alternatives[alternativeIndex]) return null;
  return {
    lines: [...lines, selectedAlternative(alternatives[alternativeIndex])],
    alternatives: alternatives.filter((_, index) => index !== alternativeIndex),
  };
}
