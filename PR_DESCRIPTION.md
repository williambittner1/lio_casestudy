# Procurement quote review for Acme

Acme's quote uploads could fail on missing VAT IDs, count alternative positions as purchases, and obscure discounted or gross totals. This change retains partial extraction results, separates alternatives, adds local OCR for image-based supplier details, and lets requesters review the quote alongside the extracted fields. The form preserves and explains the quoted total and uses a VAT estimate only when its assumptions reconcile.

Negotiated catalog suggestions are scoped to the organization and shown alongside order lines. Requesters can inspect an article and explicitly apply a compatible same-supplier offer. Acme-specific commodity rules cover cable ties, toner and small monthly software subscriptions; the assumed €20 monthly cutoff needs customer validation. Acme requests below €2,000 are automatically approved, while higher amounts require a buyer or admin. Content edits re-evaluate approval, and request visibility is limited by organization and role.

## Verification

- The backend regression and smoke suite passed 72 tests; the frontend tests and type checking passed.
- A live API benchmark parsed each of five supplied quotes once and extracted each three times. All 15 responses returned the expected quoted gross totals and base/alternative price patterns. The three OCR-supplemented styleGREEN responses included the supplier and VAT ID. See [pdf-benchmark.md](pdf-benchmark.md) for the methodology, variation and limitations.
- The effective model and exact running image were not independently verified for that benchmark. Rebuild Docker and check the requester, buyer and admin flows and the €2,000 boundary on the actual stack before handover.

## Trade-offs and next steps

- The small five-document benchmark does not establish general model reliability. OCR is capped at six selected pages, and source highlights are inferred matches rather than verified citations. Tax breakdowns may require manual review.
- Catalog matching is lexical, a request has one supplier, and selected articles are saved as snapshots rather than catalog links. Suggestions require user review.
- Confirm Acme's €20 subscription definition and the gross/net basis for approval. Auto-approval currently relies on an editable recorded total and is not a production-grade financial control.
- With more time, expand field-level evaluation, capture rejection reasons and resubmission, improve multilingual and contract-aware suggestions, and make customer-specific rules configurable.

The fuller change record is in [SOLUTION_NOTES.md](SOLUTION_NOTES.md); [handover.md](handover.md) is the concise reviewer summary.
