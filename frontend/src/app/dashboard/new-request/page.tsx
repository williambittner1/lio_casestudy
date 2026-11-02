'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useMutation } from '@tanstack/react-query';
import * as api from '@/lib/api';
import { useCommodityGroups } from '@/lib/hooks/useCommodityGroups';
import { Button, Input, Alert, Loader } from '@/components/base';
import { QuoteViewer } from '@/components/QuoteViewer';
import { ArticleSuggestions } from '@/components/ArticleSuggestions';
import { quoteFieldTarget } from '@/lib/quoteLinks';
import { addAlternative, applyCatalogArticle, createQuotePricingBasis, editOrderLine, estimateQuoteTotal, quoteBreakdown, quoteTotalDraft, removeOrderLine as removePricedLine, sameSupplier, swapAlternative, type OrderLine, type QuotePricingBasis } from '@/lib/requestPricing';

function LineTotalInput({ index, value, onCommit, active, events }: {
  index: number;
  value: number;
  onCommit: (value: number) => void;
  active: boolean;
  events: { onMouseEnter: () => void; onMouseLeave: () => void; onFocus: () => void; onBlur: () => void };
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  return (
    <Input
      id={`line-total-${index}`}
      label="Line total (€)"
      className={`h-10 font-semibold tabular-nums text-ink ${active ? 'ring-2 ring-accent-deep border-accent-deep' : ''}`}
      type="number"
      min="0"
      step="0.01"
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onMouseEnter={events.onMouseEnter}
      onMouseLeave={events.onMouseLeave}
      onFocus={events.onFocus}
      onBlur={() => { if (draft !== '' && Number(draft) !== value) onCommit(Number(draft)); events.onBlur(); }}
      required
    />
  );
}

export default function NewRequestPage() {
  const router = useRouter();
  const { data: commodityGroups } = useCommodityGroups();

  // Form state
  const [requestorName, setRequestorName] = useState('');
  const [title, setTitle] = useState('');
  const [vendorName, setVendorName] = useState('');
  const [vatId, setVatId] = useState('');
  const [department, setDepartment] = useState('');
  const [commodityGroupId, setCommodityGroupId] = useState<number | null>(null);
  const [orderLines, setOrderLines] = useState<OrderLine[]>([]);
  const [alternativeLines, setAlternativeLines] = useState<OrderLine[]>([]);
  // Keep the input as text so a user can type a decimal amount naturally.
  const [totalCost, setTotalCost] = useState('0');
  const [needsTotalReview, setNeedsTotalReview] = useState(false);
  const [quotePricingBasis, setQuotePricingBasis] = useState<QuotePricingBasis | null>(null);
  const [isEstimatedTotal, setIsEstimatedTotal] = useState(false);
  const [replacementTargets, setReplacementTargets] = useState<Record<number, string>>({});
  const [customerRule, setCustomerRule] = useState<{ groupId: number | null; conflicting: boolean } | null>(null);

  // UI state
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewMatches, setPreviewMatches] = useState<string[]>([]);
  const [previewUnmatched, setPreviewUnmatched] = useState<string[]>([]);
  const [previewUnavailable, setPreviewUnavailable] = useState(false);
  const [previewRegions, setPreviewRegions] = useState<api.QuoteRegion[]>([]);
  const [hasRegionData, setHasRegionData] = useState(false);
  const [showPreview, setShowPreview] = useState(true);
  const [activeField, setActiveField] = useState<string | null>(null);
  const [initialExtraction, setInitialExtraction] = useState<api.ExtractedVendorData | null>(null);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [warning, setWarning] = useState('');
  const [extracting, setExtracting] = useState(false);

  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl); }, [previewUrl]);

  useEffect(() => {
    let cancelled = false;
    setCustomerRule(null);
    if (!orderLines.length) return;
    const timer = window.setTimeout(() => {
      api.commodityGroups.customerRule(title, orderLines.map(({ positionDescription, unitPrice, amount, unit, totalPrice }) => ({ positionDescription, unitPrice, amount, unit, totalPrice })))
        .then(result => { if (!cancelled) { setCustomerRule(result); if (result.groupId != null) setCommodityGroupId(result.groupId); } })
        .catch(() => { if (!cancelled) setCustomerRule(null); });
    }, 400);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [title, orderLines]);

  // PDF parsing mutation
  const parsePDFMutation = useMutation({
    mutationFn: async (file: File) => {
      const result = await api.pdf.parse(file);
      if (!result.text.trim()) {
        throw new Error(result.warnings?.[0] || 'No readable text found in the PDF. Please fill in the request manually.');
      }
      return result;
    },
  });

  // Handle file upload and extraction
  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const uploadedFile = e.target.files?.[0];
    if (!uploadedFile) return;

    if (uploadedFile.type !== 'application/pdf') {
      setError('Please upload a PDF file');
      return;
    }

    // Clear the previous quote so a failed upload cannot be submitted with stale data.
    setFile(null);
    setPreviewUrl(null);
    setPreviewMatches([]);
    setPreviewUnmatched([]);
    setPreviewUnavailable(false);
    setPreviewRegions([]);
    setHasRegionData(false);
    setActiveField(null);
    setInitialExtraction(null);
    setTitle('');
    setVendorName('');
    setVatId('');
    setDepartment('');
    setCommodityGroupId(null);
    setOrderLines([]);
    setAlternativeLines([]);
    setReplacementTargets({});
    setTotalCost('0');
    setQuotePricingBasis(null);
    setIsEstimatedTotal(false);
    setNeedsTotalReview(false);
    setError('');
    setSuccess('');
    setWarning('');
    setExtracting(true);

    try {
      // Step 1: Parse PDF
      const parsedPdf = await parsePDFMutation.mutateAsync(uploadedFile);

      // Step 2: Extract structured data AND classify commodity group in one AI call
      const extractionResult = await api.extraction.extract(parsedPdf.text);
      
      if (!extractionResult.success || !extractionResult.data) {
        throw new Error(extractionResult.error || 'Failed to extract data');
      }

      const extracted = extractionResult.data;
      setInitialExtraction(extracted);
      setShowPreview(true);

      // Step 3: Auto-fill form (including commodity group from extraction)
      setTitle(extracted.title || '');
      setVendorName(extracted.vendorName || '');
      setVatId(extracted.vatId || '');
      setDepartment(extracted.department || '');
      setOrderLines((extracted.orderLines || []).map((line, i) => ({ ...line, previewSource: `Line ${i + 1}` })));
      setAlternativeLines((extracted.alternativeOrderLines || []).map((line, i) => ({ ...line, previewSource: `Alternative ${i + 1}` })));
      setTotalCost(quoteTotalDraft(extracted.totalCost, extractionResult.missingFields));
      setQuotePricingBasis(createQuotePricingBasis(extracted.orderLines || [], extracted.totalCost, extracted.taxRatePercent));
      setFile(uploadedFile);
      // Keep an unmodified preview if matching is unavailable; this never changes the saved PDF.
      setPreviewUrl(URL.createObjectURL(uploadedFile));
      try {
        const preview = await api.pdf.highlightPreview(uploadedFile, extracted);
        const binary = atob(preview.pdfBase64);
        const bytes = Uint8Array.from(binary, character => character.charCodeAt(0));
        setPreviewUrl(URL.createObjectURL(new Blob([bytes], { type: 'application/pdf' })));
        setPreviewMatches(preview.matchedFields);
        setPreviewUnmatched(preview.unmatchedFields);
        setHasRegionData(Array.isArray(preview.regions));
        setPreviewRegions(preview.regions || []);
      } catch {
        setPreviewUnavailable(true);
      }
      
      // Set commodity group (now included in extraction)
      if (extracted.commodityGroupId) {
        setCommodityGroupId(extracted.commodityGroupId);
      }

      // Show warnings for missing fields
      const warnings = [...(parsedPdf.warnings || []), ...(extractionResult.warnings || [])];
      if (extractionResult.missingFields?.length) {
        warnings.unshift(`Could not extract: ${extractionResult.missingFields.join(', ')}. Please fill in these fields manually.`);
      }
      if (warnings.length) setWarning(warnings.join(' '));

      setSuccess('✓ PDF extracted successfully! Review and submit.');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (err) {
      console.error('Extraction error:', err);
      setError(err instanceof Error ? err.message : 'Failed to extract data from PDF');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } finally {
      setExtracting(false);
    }
  };

  const reviewChangedItems = (lines: OrderLine[]) => {
    const estimate = estimateQuoteTotal(lines, quotePricingBasis);
    if (estimate !== null) {
      setTotalCost(estimate.toFixed(2));
      setIsEstimatedTotal(true);
    } else {
      setIsEstimatedTotal(false);
    }
    setNeedsTotalReview(true);
  };

  // Add order line manually
  const addOrderLine = () => {
    const updated = [
      ...orderLines,
      {
        positionDescription: '',
        unitPrice: 0,
        amount: 1,
        unit: '',
        totalPrice: 0,
      },
    ];
    setOrderLines(updated);
    if (file) reviewChangedItems(updated);
  };

  // Update order line
  const updateOrderLine = (index: number, field: keyof OrderLine, value: string | number) => {
    const result = editOrderLine(orderLines, index, field, value, Number(totalCost), Boolean(file));
    setOrderLines(result.lines);
    if (result.totalCost !== Number(totalCost)) setTotalCost(String(result.totalCost));
    if (result.requiresReview) reviewChangedItems(result.lines);
  };

  // Remove order line
  const removeOrderLine = (index: number) => {
    const result = removePricedLine(orderLines, index, Number(totalCost), Boolean(file));
    setOrderLines(result.lines);
    if (result.restoredAlternative) setAlternativeLines(previous => [...previous, result.restoredAlternative!]);
    if (result.totalCost !== Number(totalCost)) setTotalCost(String(result.totalCost));
    if (result.requiresReview) reviewChangedItems(result.lines);
    setReplacementTargets({});
  };

  const chooseAlternative = (alternativeIndex: number, mode: 'add' | 'replace') => {
    const target = replacementTargets[alternativeIndex];
    const result = mode === 'add'
      ? addAlternative(orderLines, alternativeLines, alternativeIndex)
      : target !== undefined && target !== ''
        ? swapAlternative(orderLines, alternativeLines, Number(target), alternativeIndex)
        : null;
    if (!result) return;
    setOrderLines(result.lines);
    setAlternativeLines(result.alternatives);
    setReplacementTargets({});
    reviewChangedItems(result.lines);
  };

  const chooseArticle = (index: number, article: api.Article) => {
    if (!article.supplier_name || (vendorName.trim() && !sameSupplier(vendorName, article.supplier_name))) return;
    const updated = applyCatalogArticle(orderLines, index, article);
    if (!updated) return;
    setOrderLines(updated);
    if (!vendorName.trim()) { setVendorName(article.supplier_name); setVatId(''); }
    if (file) {
      // The catalog price is not part of the supplier quote; its tax and discounts need review.
      setQuotePricingBasis(null);
      setIsEstimatedTotal(false);
      setNeedsTotalReview(true);
    } else {
      setTotalCost(String(updated.reduce((sum, line) => sum + line.totalPrice, 0)));
    }
  };

  // Only link a field while it still contains the value initially extracted from the quote.
  const fieldTarget = (field: string) => quoteFieldTarget(field, initialExtraction, vendorName, vatId, orderLines, alternativeLines, totalCost);

  const linkedRegions = previewRegions.filter(region => fieldTarget(region.field));
  const fieldEvents = (field: string | undefined) => ({
    onMouseEnter: () => { if (field && fieldTarget(field)) setActiveField(field); },
    onMouseLeave: () => setActiveField(null),
    onFocus: () => { if (field && fieldTarget(field)) setActiveField(field); },
    onBlur: () => setActiveField(null),
  });

  const selectQuoteField = (field: string) => {
    const target = fieldTarget(field);
    if (!target) return;
    setActiveField(field);
    document.getElementById(target)?.focus();
    document.getElementById(target)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  // Submit form
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setSuccess('');
    setWarning('');

    // Validation with scroll to top on error
    if (!commodityGroupId) {
      setError('Please select a commodity group');
      window.scrollTo({ top: 0, behavior: 'smooth' });
      return;
    }

    if (orderLines.length === 0) {
      setError('Please add at least one order line');
      window.scrollTo({ top: 0, behavior: 'smooth' });
      return;
    }
    if (orderLines.some(line => line.catalogArticle && !sameSupplier(vendorName, line.catalogArticle.supplierName))) {
      setError('A selected catalog article belongs to a different supplier. Review the supplier or replace that line.');
      window.scrollTo({ top: 0, behavior: 'smooth' });
      return;
    }
    if (file && needsTotalReview) {
      setError('Please review and confirm the total after changing the order lines.');
      return;
    }

    try {
      const created = await api.requests.create({
        requestorName,
        titleShortDescription: title,
        vendorName,
        vatId,
        commodityGroupId: commodityGroupId,
        totalCost: Number(totalCost),
        department,
        orderLines: orderLines.map(({ alternativeSource: _alternativeSource, previewSource: _previewSource, catalogArticle: _catalogArticle, ...line }) => line),
      });

      // Attach the original PDF (if one was uploaded) so it shows on the detail page.
      if (file) {
        try {
          await api.requests.uploadDocument(created.id, file);
        } catch {
          // Non-fatal — the request itself was created.
        }
      }

      setSuccess(created.approval_source === 'automatic'
        ? 'Request created and automatically approved. Redirecting...'
        : 'Request created and awaiting approval. Redirecting...');
      window.scrollTo({ top: 0, behavior: 'smooth' });
      setTimeout(() => {
        router.push('/dashboard/requests');
        router.refresh();
      }, 1500);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create request');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    }
  };

  return (
    <div className={`${file && showPreview ? 'max-w-[1800px]' : 'max-w-5xl'} mx-auto px-4 sm:px-6 lg:px-8 py-8`}>
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-semibold tracking-tight text-ink">New Procurement Request</h1>
        <p className="text-gray-600 mt-2">
          Upload a vendor offer PDF or fill in the details manually
        </p>
      </div>

      {error && <Alert variant="error" className="mb-6">{error}</Alert>}
      {warning && <Alert variant="warning" className="mb-6">{warning}</Alert>}
      {success && <Alert variant="success" className="mb-6">{success}</Alert>}

      <form onSubmit={handleSubmit} className="space-y-8">
        {/* PDF Upload Section */}
        <div className="lio-card p-6">
          <h2 className="text-xl font-semibold tracking-tight text-ink mb-4">Upload Vendor Offer (Optional)</h2>
          <p className="text-sm text-gray-600 mb-4">
            Upload a PDF to automatically extract vendor details and order items
          </p>

          <div className="border-2 border-dashed border-gray-300 rounded-lg p-8 text-center hover:border-accent-deep/50 transition-colors">
            <input
              type="file"
              accept="application/pdf"
              onChange={handleFileChange}
              className="hidden"
              id="pdf-upload"
              disabled={extracting}
            />
            <label htmlFor="pdf-upload" className="cursor-pointer">
              {extracting ? (
                <div className="flex flex-col items-center">
                  <Loader size="lg" />
                  <p className="mt-4 text-sm text-gray-600">Extracting data from PDF...</p>
                </div>
              ) : file ? (
                <div>
                  <svg
                    className="mx-auto h-12 w-12 text-green-500"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M5 13l4 4L19 7"
                    />
                  </svg>
                  <p className="mt-2 text-sm font-medium text-gray-900">{file.name}</p>
                  <p className="mt-1 text-xs text-gray-500">Click to upload a different file</p>
                </div>
              ) : (
                <div>
                  <svg
                    className="mx-auto h-12 w-12 text-gray-400"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"
                    />
                  </svg>
                  <p className="mt-2 text-sm text-gray-600">
                    Click to upload or drag and drop
                  </p>
                  <p className="mt-1 text-xs text-gray-500">PDF up to 10MB</p>
                </div>
              )}
            </label>
          </div>
        </div>

        <div className={file && previewUrl && showPreview ? 'grid grid-cols-1 gap-y-4 xl:grid-cols-[minmax(0,1.1fr)_2.5rem_minmax(0,1fr)] xl:gap-x-2 xl:items-start' : 'grid grid-cols-1 gap-4'}>
          {file && previewUrl && (
            <aside id="quote-preview" className={`${showPreview ? 'order-2 xl:order-none' : 'hidden'} lio-card overflow-hidden xl:sticky xl:top-20`} aria-labelledby="quote-preview-heading">
              <div className="relative flex min-h-12 items-center justify-between gap-3 px-4 py-2">
                <h2 id="quote-preview-heading" className="text-sm font-semibold text-ink">Supplier quote</h2>
                <div className="flex items-center gap-3 text-xs">
                  <a href={previewUrl} target="_blank" rel="noreferrer" className="font-medium text-accent-deep underline underline-offset-4">Open PDF</a>
                  <details className="relative text-ink/70">
                    <summary aria-label="About quote highlights" title="About quote highlights" className="flex h-7 w-7 cursor-pointer list-none items-center justify-center rounded-full border border-line font-semibold hover:bg-ink/5 focus-visible:outline-2 focus-visible:outline-accent-deep">i</summary>
                    <div className="absolute right-0 top-9 z-20 w-64 rounded-lg border border-line bg-white p-3 leading-relaxed shadow-lg">
                      {previewUnavailable ? <p>Highlights are unavailable; the original PDF is shown.</p> : <>
                        <p>Highlights are possible matches, not proof of source. Hover or select to compare. Editing a field removes its original match.</p>
                        <div className="mt-2 flex flex-wrap gap-3"><span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-[#55b5cb]" aria-hidden="true" /> Vendor</span><span className="inline-flex items-center gap-1.5"><span className="h-2.5 w-2.5 rounded-sm bg-[#efbb52]" aria-hidden="true" /> Order lines</span></div>
                        {previewRegions.some(region => region.matchKind === 'price_row') && <p className="mt-2">Some item rows are inferred from prices and quantity. Check the descriptions against the quote.</p>}
                        <p className="mt-2 font-medium">{previewMatches.length} fields matched · {previewUnmatched.length} not highlighted</p>
                        <p className="mt-1">{previewUnmatched.length ? `Not highlighted: ${previewUnmatched.join(', ')}. Text may be paraphrased, repeated or in an image.` : 'All eligible fields have a possible text match.'}</p>
                      </>}
                    </div>
                  </details>
                </div>
              </div>
              {hasRegionData && !previewUnavailable ? (
                <QuoteViewer url={previewUrl} regions={linkedRegions} activeField={activeField} onActiveField={setActiveField} onSelectField={selectQuoteField} />
              ) : <iframe title="Vendor quote preview" src={previewUrl} className="block h-[65vh] min-h-96 w-full border-t border-line bg-gray-100 xl:h-[calc(100vh-10rem)]" />}
            </aside>
          )}
          {file && previewUrl && (
            <div className={`${showPreview ? 'order-1 xl:order-none xl:sticky xl:top-20 xl:flex xl:justify-center' : ''}`}>
              <button type="button" aria-label={showPreview ? 'Hide PDF' : 'Show PDF'} title={showPreview ? 'Hide PDF' : 'Show PDF'} aria-expanded={showPreview} aria-controls="quote-preview" onClick={() => { setShowPreview(!showPreview); setActiveField(null); }} className={`inline-flex h-9 items-center justify-center gap-1 rounded-full border border-line bg-white px-3 text-sm font-medium text-ink/70 shadow-sm hover:bg-ink/5 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent-deep ${showPreview ? 'xl:w-9 xl:px-0' : ''}`}>
                <svg aria-hidden="true" className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d={showPreview ? 'm14 6-6 6 6 6' : 'm10 6 6 6-6 6'} /></svg>
                <span className={showPreview ? 'xl:sr-only' : ''}>{showPreview ? 'Hide PDF' : 'Show PDF'}</span>
              </button>
            </div>
          )}
          <div className="order-3 min-w-0 space-y-8 xl:order-none">

        {/* Request Details */}
        <div className="lio-card p-6 space-y-4">
          <h2 className="text-xl font-semibold tracking-tight text-ink mb-4">Request Details</h2>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Input
              label="Requestor Name *"
              value={requestorName}
              onChange={(e) => setRequestorName(e.target.value)}
              required
              placeholder="John Doe"
            />

            <Input
              label="Department"
              value={department}
              onChange={(e) => setDepartment(e.target.value)}
              placeholder="IT Department"
            />
          </div>

          <Input
            label="Title / Short Description *"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            placeholder="Adobe Creative Cloud Subscription"
          />
        </div>

        {/* Vendor Information */}
        <div className="lio-card p-6 space-y-4">
          <h2 className="text-xl font-semibold tracking-tight text-ink mb-4">{file && <span className="inline-block h-3 w-3 rounded-sm bg-[#55b5cb] mr-2" aria-hidden="true" />}Vendor Information</h2>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <Input
              id="vendor-name"
              label="Vendor Name *"
              className={activeField === 'Vendor name' ? 'ring-2 ring-accent-deep border-accent-deep' : ''}
              {...fieldEvents('Vendor name')}
              value={vendorName}
              onChange={(e) => setVendorName(e.target.value)}
              required
              placeholder="Adobe Systems"
            />

            <Input
              id="vendor-vat"
              label="VAT ID"
              className={activeField === 'VAT ID' ? 'ring-2 ring-accent-deep border-accent-deep' : ''}
              {...fieldEvents('VAT ID')}
              value={vatId}
              onChange={(e) => setVatId(e.target.value)}
              placeholder="DE123456789"
            />
          </div>
          {file && <p className="text-sm text-gray-600">Check that the vendor is the supplier named in the quote.</p>}

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Commodity Group *
            </label>
            <select
              value={commodityGroupId || ''}
              onChange={(e) => setCommodityGroupId(Number(e.target.value))}
              required
              disabled={customerRule?.groupId != null}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-gray-900 focus:ring-2 focus:ring-accent-deep/60 focus:border-accent-deep/40"
            >
              <option value="">
                {commodityGroups ? 'Select a commodity group' : 'Loading...'}
              </option>
              {commodityGroups?.map((group) => (
                <option key={group.id} value={group.id}>
                  {group.id} - {group.category} - {group.name}
                </option>
              ))}
            </select>
            {customerRule?.groupId != null && <p className="mt-2 text-xs text-accent-deep">Acme&apos;s accounting rule selected this category.</p>}
            {customerRule?.conflicting && <p className="mt-2 text-xs text-red-700">These items require different Acme categories. Create separate requests.</p>}
          </div>
        </div>

        {/* Order Lines */}
        <div className="lio-card p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-xl font-semibold tracking-tight text-ink">{file && <span className="inline-block h-3 w-3 rounded-sm bg-[#efbb52] mr-2" aria-hidden="true" />}Order Lines *</h2>
            <Button type="button" onClick={addOrderLine} variant="outline" size="sm">
              + Add Line
            </Button>
          </div>
          <p className="text-xs text-ink/55 mb-2">Line totals reflect quoted discounts.</p>

          {orderLines.length === 0 ? (
            <div className="text-center py-8 text-gray-500">
              No order lines yet. Add one to get started.
            </div>
          ) : (
            <div className="divide-y divide-line">
              {orderLines.map((line, index) => (
                <div key={index} className="py-4 first:pt-3 last:pb-0"
                  onMouseEnter={() => { const field = line.previewSource && `${line.previewSource} description`; if (field && fieldTarget(field)) setActiveField(field); }}
                  onMouseLeave={() => setActiveField(null)}>
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="text-xs font-semibold uppercase tracking-wide text-ink/55">Line {index + 1}</h3>
                    <button
                      type="button"
                      onClick={() => removeOrderLine(index)}
                      className="rounded text-xs text-ink/55 hover:text-red-700 focus-visible:outline-2 focus-visible:outline-accent-deep"
                    >
                      Remove
                    </button>
                  </div>

                  <div className="space-y-3">
                    <div>
                      <Input
                        id={`line-description-${index}`}
                        label="Description"
                        className={`h-10 ${activeField === `${line.previewSource} description` ? 'ring-2 ring-accent-deep border-accent-deep' : ''}`}
                        {...fieldEvents(line.previewSource && `${line.previewSource} description`)}
                        value={line.positionDescription}
                        onChange={(e) =>
                          updateOrderLine(index, 'positionDescription', e.target.value)
                        }
                        required
                        placeholder="Item description"
                      />
                      <ArticleSuggestions description={line.positionDescription} vendorName={vendorName} selected={line.catalogArticle} onChoose={article => chooseArticle(index, article)} />
                    </div>

                    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                    <Input
                      id={`line-unit-price-${index}`}
                      label="Unit Price (€)"
                      type="number"
                      className={`h-10 font-semibold tabular-nums text-ink ${activeField === `${line.previewSource} unit price` ? 'ring-2 ring-accent-deep border-accent-deep' : ''}`}
                      {...fieldEvents(line.previewSource && `${line.previewSource} unit price`)}
                      step="0.01"
                      value={line.unitPrice}
                      onChange={(e) =>
                        updateOrderLine(index, 'unitPrice', parseFloat(e.target.value) || 0)
                      }
                      required
                    />

                    <Input
                      id={`line-amount-${index}`}
                      label="Amount"
                      className={`h-10 ${activeField === `${line.previewSource} amount` ? 'ring-2 ring-accent-deep border-accent-deep' : ''}`}
                      {...fieldEvents(line.previewSource && `${line.previewSource} amount`)}
                      type="number"
                      step="0.01"
                      value={line.amount}
                      onChange={(e) =>
                        updateOrderLine(index, 'amount', parseFloat(e.target.value) || 1)
                      }
                      required
                    />

                    <Input
                      id={`line-unit-${index}`}
                      label="Unit"
                      className={`h-10 ${activeField === `${line.previewSource} unit` ? 'ring-2 ring-accent-deep border-accent-deep' : ''}`}
                      {...fieldEvents(line.previewSource && `${line.previewSource} unit`)}
                      value={line.unit}
                      onChange={(e) => updateOrderLine(index, 'unit', e.target.value)}
                      required
                      placeholder="pcs, licenses, etc."
                    />
                    <LineTotalInput
                      index={index}
                      value={line.totalPrice}
                      onCommit={(value) => updateOrderLine(index, 'totalPrice', value)}
                      active={activeField === `${line.previewSource} line total`}
                      events={fieldEvents(line.previewSource && `${line.previewSource} line total`)}
                    />
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {alternativeLines.length > 0 && (
          <div className="lio-card p-6 space-y-4">
            <div>
              <h2 className="text-xl font-semibold tracking-tight">Alternative offer positions</h2>
              <p className="text-sm text-ink/60 mt-1">Replace an order line or add an alternative as another item.</p>
            </div>
            <ul className="space-y-2">
              {alternativeLines.map((line, index) => (
                <li id={`alternative-${index}`} tabIndex={-1} key={index}
                  {...fieldEvents(line.previewSource && `${line.previewSource} description`)}
                  className={`border border-line rounded-xl p-4 space-y-3 ${activeField?.startsWith(`${line.previewSource} `) ? 'ring-2 ring-accent-deep' : ''}`}>
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <span className="font-medium text-ink">{line.positionDescription || 'Alternative item'}</span>
                    <span className="text-sm text-ink/60 tabular-nums">{line.amount} {line.unit} × <strong className="text-ink">€{line.unitPrice.toFixed(2)}</strong></span>
                  </div>
                  <div className="flex flex-wrap gap-2 items-center">
                    <label htmlFor={`replace-target-${index}`} className="sr-only">Choose order line to replace</label>
                    <select
                      id={`replace-target-${index}`}
                      value={replacementTargets[index] ?? ''}
                      onChange={(e) => setReplacementTargets({ ...replacementTargets, [index]: e.target.value })}
                      className="h-9 w-full min-w-0 max-w-full rounded-full border border-line bg-white px-3 text-sm text-ink focus:outline-none focus:ring-2 focus:ring-accent-deep/60 sm:w-auto sm:flex-1"
                    >
                      <option value="">Choose a line to replace</option>
                      {orderLines.map((item, itemIndex) => (
                        <option key={itemIndex} value={itemIndex}>Line {itemIndex + 1}: {item.positionDescription || 'Untitled item'}</option>
                      ))}
                    </select>
                    <Button type="button" variant="outline" size="sm" disabled={!replacementTargets[index]} onClick={() => chooseAlternative(index, 'replace')}>Replace</Button>
                    <Button type="button" variant="secondary" size="sm" onClick={() => chooseAlternative(index, 'add')}>Add as item</Button>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Total Cost */}
        <div className="rounded-2xl border border-accent/40 bg-accent-soft/20 p-6 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <label htmlFor="request-total" className="text-sm font-medium text-ink/70">Total cost</label>
              <p className="text-xs text-ink/55 mt-1">{isEstimatedTotal ? 'Estimated total · review tax and fees' : file && needsTotalReview ? 'Previous total · update required' : file ? 'Quoted total · review tax and fees' : 'Total for this request'}</p>
            </div>
            <div className="flex items-center gap-1 text-2xl font-semibold tracking-tight text-accent-deep sm:text-3xl">
              <span aria-hidden="true">€</span>
              <input
                id="request-total"
                aria-label="Total cost in euros"
                type="number"
                min="0"
                step="0.01"
                value={totalCost}
                {...fieldEvents('Total cost')}
                onChange={(e) => { setTotalCost(e.target.value); if (file) { setQuotePricingBasis(null); setIsEstimatedTotal(false); setNeedsTotalReview(true); } }}
                className={`w-36 max-w-full rounded-lg bg-transparent text-right tabular-nums outline-none focus:ring-2 focus:ring-accent-deep/60 ${activeField === 'Total cost' ? 'ring-2 ring-accent-deep' : ''}`}
                required
              />
            </div>
          </div>
          {(() => {
            const breakdown = quoteBreakdown(orderLines, quotePricingBasis);
            return breakdown ? (
              <dl className="space-y-2 border-t border-accent/30 pt-3 text-sm tabular-nums text-ink/75">
                <div className="flex justify-between gap-4"><dt>Items and charges before VAT</dt><dd>€{breakdown.net.toFixed(2)}</dd></div>
                <div className="flex justify-between gap-4"><dt>VAT ({breakdown.rate}%)</dt><dd>€{breakdown.vat.toFixed(2)}</dd></div>
              </dl>
            ) : file ? <p className="border-t border-accent/30 pt-3 text-sm text-ink/60">VAT breakdown unavailable: check the supplier&apos;s quote.</p> : null;
          })()}
          {file && (
            needsTotalReview ? (
              <div className="flex flex-wrap items-center justify-between gap-3 border-t border-accent/30 pt-3">
                <p className="text-sm text-ink/75">{isEstimatedTotal
                  ? `Estimated using ${quotePricingBasis?.taxRatePercent}% VAT. Check shipping, discounts and tax, then confirm.`
                  : 'The total could not be recalculated reliably. Update it for tax, shipping and discounts, then confirm.'}</p>
                <Button type="button" variant="outline" size="sm" onClick={() => setNeedsTotalReview(false)}>Confirm total</Button>
              </div>
            ) : <p className="text-sm text-ink/60">Check the total against the supplier&apos;s quote.</p>
          )}
        </div>

        {/* Submit */}
        <div className="flex items-center justify-end space-x-4">
          <Button
            type="button"
            variant="outline"
            onClick={() => router.back()}
          >
            Cancel
          </Button>
          <Button type="submit" size="lg">
            Create Request
          </Button>
        </div>
          </div>
        </div>
      </form>
    </div>
  );
}

