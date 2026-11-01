# Lio procurement case study — handover

**Status:** Implemented on the `improvements` branch; final Docker verification and the PR are still to be completed. PDF extraction, quote review, negotiated article suggestions, Acme-specific categorisation and the approval threshold are included.

## What changed

- **PDF extraction:** Missing optional fields no longer discard the whole result. The extraction separates base positions from alternatives and uses explicitly labelled quote totals when they can be identified reliably.
- **Image-based text / OCR:** The styleGREEN quote contains supplier details in an image that ordinary PDF text extraction misses. Image-heavy or scanned pages are rendered and processed locally with German and English Tesseract OCR. The OCR text supplements the selectable PDF text; a cautious footer check can recover an unambiguous legal supplier and VAT ID.
- **Quote review:** Requesters can add, replace, remove and re-add alternative positions. For quotes with a verifiable single VAT rate and reconciled totals, the form shows a price/VAT breakdown and estimates a new total after item changes. Other quotes require manual total review.
- **PDF review:** A hideable split view places the continuously scrolling quote beside the form on wide screens. Form interactions scroll to possible source text. The saved-request detail page uses the same viewer and recomputes matches from the stored PDF and current fields without another model call. A translated description can be linked to a unique numbered row using matching prices and quantity; the UI labels this as an inference requiring review. The quoted total can be linked when it appears beside an unambiguous final-total label. The original PDF is preserved.
- **Layout:** A stable scrollbar gutter prevents centered page content from shifting between short and long pages.
- **Negotiated articles:** Matching catalog entries appear beside order lines, scoped to the current organization. Selecting a suggestion's description opens its catalog record in a new tab; using its price requires a separate explicit action. Supplier, price, unit and quantity are visible. Different-supplier matches cannot be applied to the same request.
- **Request permissions and approvals:** Requesters can access only their own requests. Buyers and admins can see organization requests, decide other users' pending requests and approve their own. Rejection of one's own request remains restricted. Admins retain member, master-data and settings access.
- **Acme approval threshold:** New Acme requests below €2,000 are automatically approved; €2,000 and above require a buyer or admin. Editing request content or changing the attached PDF runs the check again, so an eligible smaller request is automatically approved again and a larger request returns to pending. A missing extracted total requires explicit input instead of silently becoming zero. Lifecycle status changes are separate. Other organizations retain manual approval. Existing decisions and pending requests are not changed by the migration. Admins can inspect this rule under **Organization → Settings**.
- **Model:** The extraction default is now GPT-6 Luna with low reasoning effort; the existing model override remains available.
- **Acme categorisation:** Cable ties map to Electrical Engineering, toner to IT Hardware, and an explicitly monthly software subscription of up to **€20 per month** to Office Supplies. An admin can inspect these rules in **Organization → Settings**. The €20 threshold is an inclusive assumption based on the line total and **must be validated with Acme**, including tax and billing-period treatment. A single request with conflicting rules must be split.

## Evidence and limitations

- The initial baseline covered all five supplied quotes once. A subsequent live benchmark parsed each quote once and extracted each three times: 15/15 successful responses and expected gross totals, with the expected base and alternative price patterns. StyleGREEN returned FlowerArt GmbH and its VAT ID in all three OCR-supplemented runs. See [pdf-benchmark.md](pdf-benchmark.md) for the detailed results and limitations.
- For styleGREEN, an earlier live retest after OCR recovered **FlowerArt GmbH**, the VAT ID, three line items and the quoted total. This was a single run under the earlier configuration, not a reliability benchmark or a combined OCR-plus-Luna check.
- The backend regression and smoke suites passed 72 tests, covering model parameters, Acme rules, PDF matching, approval boundaries, editing, requester isolation and tenant isolation. All 19 frontend tests passed, covering catalog price selection, quote links and explicit entry of missing totals. Browser walkthroughs against the real backend and disposable SQLite databases checked automatic approval, edits, the rules screen, buyer self-approval, and saved-request matching in both directions across two PDF pages. Earlier quote-view and catalog checks used a local mock.
- The approval-source migration was checked on a temporary PostgreSQL table with rollback, preserving existing decisions. Whether the running Docker stack has applied it has not been independently confirmed.
- Frontend type checking and the local production build passed, including the new article detail page. Docker Compose still needs an end-to-end check with the rebuilt images.
- OCR is limited to the first six selected pages and can misread or miss image text. AI output and alternative classification still need human review. PDF highlights are contextual matches, not proven provenance: ambiguous, paraphrased or unrecognized OCR text may remain unhighlighted. The repeated evaluation covers only five PDFs and one OCR parse per quote; it does not verify frontend behavior or general reliability.
- The VAT estimate assumes that the verified rate and other quote conditions remain applicable. If they cannot be verified, the requester must review the total manually.
- Catalog matching is lexical and may miss equivalents. Article choice is saved as a line snapshot, not a persistent catalog link; quantity and negotiated terms require review. The latest OCR-supplemented API benchmark recovered styleGREEN's supplier and VAT ID in 3/3 runs. Docker access was unavailable, so the running image version and effective model name could not be independently confirmed.
- Auto-approval uses the request's recorded total, which users can edit; it does not independently verify quote totals, currency, or authorization against purchase orders. Validate the gross/net policy and add trusted amount controls before production. Existing records are not retroactively approved.
- Lifecycle status is independent of approval, so a pending or rejected request can still be marked closed. Confirm whether the customer wants those transitions restricted.
- The request-detail editor does not yet support changing saved order lines in the UI. The backend rechecks approval if order lines are changed through the API.
- The source PDFs are in the case-study package, not this repository. The reproducible observations are in [quote-extraction-baseline.md](quote-extraction-baseline.md) and [pdf-benchmark.md](pdf-benchmark.md).

## Remaining work

1. Rebuild Docker and walk through the PDFs, requester/buyer/admin roles, and the €1,999.99 / €2,000 approval boundary with the real stack.
2. Validate the €20-per-month assumption, tax basis, billing periods, and the approval total's gross/net meaning with Acme.
3. Expand the evaluation to unseen quotes and more scanned layouts as time and API budget allow.
4. Review the changes, commit and push the branch, and open the final PR. See [INTERVIEW_PREP.md](INTERVIEW_PREP.md) for a suggested walkthrough.

## Product ideas for more time

- **Rejection reasons and resubmission:** Ask the buyer for a reason, show it to the requester and preserve the decision in the activity log. Let the requester correct and resubmit, and confirm whether notification and a reason are mandatory. This is a proposal, not implemented.
- **Better negotiated-article guidance:** Improve multilingual matching, validate units and agreement terms, retain the chosen article ID and show savings only for comparable offers.
- **Measurable extraction quality:** Evaluate labelled quotes field by field, capture corrections, improve source attribution and expand scanned-document coverage.
- **Customer-configurable controls:** Confirm approval/status semantics, the gross/net and currency basis, independent-review requirements, and Acme category thresholds before making these settings configurable.

Run locally with Docker Compose from this directory after configuring the OpenAI key in `.env`:

```bash
docker compose up --build
```

See [SOLUTION_NOTES.md](SOLUTION_NOTES.md) for implementation decisions, test commands and detailed trade-offs.

The interview presentation is available as an [editable PowerPoint](presentation/Lio_Acme_Case_Study_William_v6.pptx) and [PDF](presentation/Lio_Acme_Case_Study_William_v6.pdf).
