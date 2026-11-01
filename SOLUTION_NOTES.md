# Case study solution notes

This is a running record for the `improvements` branch. Update it as the solution develops; use it to prepare the pull request and interview hand-off. The brief sets a five-hour timebox, so choices and limitations matter as much as the amount implemented.

**OCR and styleGREEN:** Implemented. See [section 6](#6-ocr-for-stylegreen-and-other-image-heavy-pdfs) for how image-based supplier text is read, and the verification section for the live retest and its limits. The concise reviewer-facing summary is in [handover.md](handover.md).

**Latest evaluation:** The previously outstanding 15-call benchmark was completed on 2026-11-01. All responses returned the expected quoted gross totals and base/alternative price patterns. This is not a complete field-level reliability score; see [pdf-benchmark.md](pdf-benchmark.md) for results, observed variation and configuration limits. Earlier dated notes below describe the status at the time and are retained as history.

## Starting point

The new-request page uploaded a PDF, the backend extracted plain text with `pypdf`, and a single OpenAI call extracted fields and chose a commodity group. The frontend populated an editable form. In an initial one-run-per-document check of the five supplied PDFs, all parsed, two extractions failed because a missing VAT ID was returned as `null`, and both quotes with alternatives showed alternatives as ordinary order lines. A later manual Dream in Green extraction displayed an incorrect total. See [quote-extraction-baseline.md](quote-extraction-baseline.md) for the detailed baseline.

## Changes: PDF extraction and quote review

### 1. Retain partial results when optional fields are missing

**Problem:** Pydantic expected a string for fields like VAT ID. An AI response containing `null` caused validation to fail, discarding all extracted data even when the remaining fields were useful.

**Change:** `backend/app/schemas/extraction.py` normalizes null text and optional line fields to the existing empty-field representation. The service reports missing fields, including missing line units, for review. The prompt tells the model not to invent absent values. JSON response mode asks the model for valid JSON.

**Trade-off:** Existing request schemas use zero for missing numbers; zero is not a reliable measure of confidence. The user still needs to review fields. This is not a general solution for malformed or semantically wrong AI output.

### 2. Distinguish base items from alternatives

**Problem:** Dream in Green and Gärtner Gregg both include alternative products. Treating them as additional order lines makes the proposed purchase misleading.

**Change:** The extraction response now includes `alternativeOrderLines` separately from `orderLines`. The prompt describes alternative headings and labels. The form lets the requester select which line to replace or explicitly add an alternative as another item. Swapping keeps the replaced line available as an alternative, so the choice can be undone. A narrow fallback handles an explicitly marked alternative block when exactly one order line matches a price in that block. If the document mentions alternatives, the form warns the user to verify the result, including when detection is ambiguous.

**Trade-off:** This is not a general-purpose document-layout parser. Models may misclassify alternatives; the price fallback intentionally does nothing on ambiguity. The requester chooses the replacement explicitly because the model doesn't reliably specify which base line an alternative replaces. Alternatives are not stored as a structured set of options in the saved request. The original PDF remains the source of truth.

### 3. Preserve and review quote totals and discounts

**Problem:** The frontend originally replaced the quote's gross total with the sum of line totals whenever any line was edited or removed. That could discard tax or shipping. It also recalculated a discounted line as unit price multiplied by quantity when the price or quantity changed, without an editable discounted total.

**Change:** The backend prefers a uniquely identifiable, explicitly labelled final amount on one text line (for example `Endsumme 1.847,19 €`) to an incorrect model guess. If text layout makes this ambiguous, it does not guess. A later correction addresses a reported usability bug: adding or swapping an alternative left the old total unchanged. The backend now returns a VAT rate only when native PDF text provides a single unambiguous rate and printed net, tax, gross and extracted base-line totals all reconcile. For such quotes the form calculates a new gross **estimate** from the changed line totals and the verified rate, using cents to limit rounding drift. For example, on Gärtner Gregg's quote, adding the €1,926 alternative changes €2,092.02 to an estimated €4,383.96; replacing the €1,438 item gives €2,672.74. The rate is verified by code, not trusted from the model. The form exposes the after-discount line total and requires review and confirmation after a financial edit, alternative selection or line removal. If the original numbers cannot be verified, it labels the displayed value as the previous total and asks the requester to update it. Text-only edits preserve the discounted amount. Manually created requests continue to calculate their total from line totals.

**Trade-off:** Final-amount and tax matching support a limited set of unambiguous German-format labels; mixed tax rates, separately listed fees and other layouts fall back to manual review. An estimated changed total assumes the same VAT rate and that shipping and other conditions do not change; the supplier may apply a different price, tax or discount. Confirmation is a safeguard, not proof that the number is correct. Editing the total manually disables subsequent automatic estimates for that quote. If a user changes a unit price or quantity, the line total is initially recomputed without a discount and must be reviewed or edited.

### 4. Reduce customer/supplier and department confusion

**Problem:** The original prompt treated the customer company as a possible department, and in a retest it misidentified the recipient as the supplier for the styleGREEN quote.

**Change:** The prompt distinguishes the supplier from the customer and asks for a department only when one is explicit. When the supplementary OCR contains one unambiguous legal company and German VAT ID together in a footer, the backend can use that pair and warns the user to verify it. A conflicting VAT ID or multiple identities are not silently overwritten. The form reminds the user to check the supplier.

**Known limitation:** In a text-only retest the model returned `Lio Technologies GmbH`, the recipient, as supplier for the styleGREEN quote. After OCR and the footer check, one live retest returned the legal supplier FlowerArt GmbH and its VAT ID. The check is deliberately narrow, supports only a subset of footer layouts, and still requires review because OCR and AI can make mistakes.

### 5. Avoid mixing an unsuccessful new upload with an older quote

**Problem:** If a new upload failed after a previous extraction, the prior form contents could remain visible alongside the new file selection.

**Change:** Starting a new valid upload clears previous extracted fields. The document is attached to the future request only after parsing and extraction succeed. The requester's own name is preserved.

### 6. OCR for styleGREEN and other image-heavy PDFs

**Problem:** The styleGREEN PDF has selectable body text but its header and footer are embedded in a page-sized image. Plain text extraction missed the footer's supplier name and VAT ID.

**Change:** For scanned or image-heavy pages, the backend renders up to six pages locally and runs German/English Tesseract OCR, using a high-contrast version to expose pale footer text. OCR text supplements native PDF text for the model. Docker installs Poppler and Tesseract; the Python package adds Pillow. Missing tools, failures and the page cap produce warnings. The native text path still works if OCR is unavailable.

**Trade-off:** OCR can misread characters, duplicates some document text, takes extra time, and recognizes only selected pages. The simple page-image heuristic can miss smaller embedded images; complex layouts still need review. The original selectable text is retained and the AI is told that OCR can contain errors. No image is sent to the model, only the resulting text.

### 7. Restore Lio's compact review layout

**Problem:** The editable line total introduced an extra row and the original total confirmation checkbox made the price hard to scan.

**Change:** Order line fields, including the editable line total, share a row on wider screens and stack responsively on smaller ones. Unit, line and overall prices use stronger type. The quoted total uses the existing cyan accent and remains editable. Alternative controls use the existing rounded buttons and restrained card style.

### 8. Restore removed alternatives and show a VAT breakdown

**Problem:** Adding or swapping an alternative removed it from the available choices. Removing its resulting order line then lost the option entirely. The gross total also did not show how much was VAT.

**Change:** The form remembers the original quoted alternative as temporary client-side state. Removing the corresponding order line returns that option to the alternatives list, even if the line was edited; it can then be added again. Swapping continues to offer the replaced item. This UI metadata is stripped from the request sent to the backend. When quote amounts and a single VAT rate have been verified, the total section shows items and charges before VAT, the VAT amount and the gross total. All three update when the selected items change.

**Trade-off:** Editing an alternative and then removing it restores the supplier's original version, not the user's edited copy. Quotes with unverified or mixed tax rates show that the breakdown is unavailable rather than inventing a VAT figure. The estimate assumes shipping and tax conditions stay the same.

### 9. Show a reviewable PDF with possible field matches

**Problem:** Requesters needed to switch away from the form to check extracted values against the quote.

**Change:** After extraction, a temporary PDF preview appears beside the form on wide screens and above it on narrow screens; the requester can hide or show it. Cyan marks possible vendor and VAT-ID matches; amber marks possible order-line and alternative matches. Poppler extracts positions for native PDF text, and the backend marks literal matches in a copy using pypdf. The frontend renders the PDF with PDF.js and overlays the possible matches. Hovering or focusing a linked form field emphasizes its possible PDF match; selecting a PDF match focuses the corresponding field. A stable per-item source reference keeps links attached when an alternative moves, and editing a field removes that field's original match until its original value is restored. The original upload remains unchanged and is still the file attached to a submitted request. The viewer lists fields it could not highlight and falls back to the browser PDF viewer if interactive rendering or preview generation fails.

**Trade-off:** These are text matches, not verified AI provenance: a repeated value may be highlighted in several places, and short matches or matches in a different context can be misleading. Only the first few words of long descriptions are matched. Image-only/OCR text, paraphrases, frequently repeated matches and rotated/cropped pages may not be highlighted. Highlights reflect the initial extraction rather than later manual edits. The PDF still requires human review; the preview does not prove that every extracted value is correct. Preview generation runs locally and makes no extra model call. It supports at most 20 pages and the existing 10 MB upload limit.

### 10. Keep page content stable when a scrollbar appears

**Problem:** Switching between short and long pages could change the available viewport width and shift centered content.

**Change:** The root page reserves a stable scrollbar gutter. Browsers without support fall back to a consistently present vertical scrollbar. Overlay scrollbars do not need reserved space.

**Verification:** In a local browser walkthrough, the shared navigation and main content had the same 1,270 px width on a short Suppliers page and a taller New Request page at a 1,280 px viewport. The sign-out button remained at the same horizontal position.

### 11. Remove the PDF viewer's intrusive highlight popup

**Problem:** Hovering an annotated PDF highlight displayed a large yellow note that obscured the quote.

**Change:** Highlight annotations retain their colour and location but no longer contain the note text that triggers the viewer's popup. The preview legend and list of unmatched fields remain available. A regression test checks that the generated annotations contain no popup text. Reader-specific behaviour should still be checked in the target browser.

**Follow-up implemented:** The split viewer now supports highlighting and selecting possible matches in both directions. These are literal matches, not model-provided source locations. OCR-only text, edited fields and ambiguous values still need review; see section 9.

### 12. Give the quote more room during review

**Problem:** The split viewer devoted too much height to its explanation and page controls, leaving less space to read the supplier's quote. Its hide/show control also sat above both columns.

**Change:** The viewer uses a compact heading and page controls, a larger document area, and a slightly wider quote column. The hide control sits between the columns on wide screens; when hidden, a labelled show control sits above the form. On narrow screens the controls and quote stack above the form. The explanation, colour legend and unmatched-field details remain available from the information control. The PDF viewer stays mounted when hidden so the current page is retained.

**Verification:** Chrome was checked with the Gärtner Gregg quote and a local mock API, with no model call. The viewer rendered, the control appeared between desktop columns, page 2 remained selected after hide/show, and the form values remained present. At a 390 px viewport, the layout stacked without horizontal page overflow and the hide control worked. Frontend unit and type checks passed; see the production build result in the handover.

### 13. Suggest negotiated catalog articles

**Problem:** Requesters could create order lines without seeing their organization's negotiated catalog prices.

**Change:** The new tenant-scoped `/articles/suggestions` endpoint ranks up to five matches using description words or an article number. A small German-to-English moss alias also finds possible catalog items for the provided Moosbild quotes; this is a candidate, not a claim of equivalence. After a short pause in typing, the request form shows matches with their supplier, article number, catalog quantity, currency, unit and price. A requester explicitly selects an item; the form applies its description, unit and price to the existing quantity. If the request has no supplier, selecting the article fills it. Because a request has one supplier, articles from other suppliers are visible but cannot be applied, and submission checks that selected catalog lines still match the request supplier. Only EUR articles can be applied. On PDF requests, choosing an article invalidates the original quote's tax calculation and requires manual total review. With no PDF, the line total and request total update immediately.

**Trade-offs:** Matching is lexical and can miss synonyms, translations or long descriptions; it produces candidates, not verified equivalents. Catalog quantity is shown for review but its commercial meaning and applicability of a price are not verified. No catalog article ID is persisted: the request stores the chosen description, unit and price as a snapshot, so future audit or catalog-price changes cannot be linked automatically. Supplier comparison uses names and may reject legitimate spelling variations. A quote's shipping, tax and discounts cannot safely be assumed to apply to a catalog price.

**Verification:** Local backend tests check ranking, article-number search, authentication and tenant isolation, including requester access and another organization's empty catalog. Frontend tests check application of price and quantity, rejected currencies, and clearing selection when key fields change. A Chrome walkthrough with a local mock checked selecting an article, populating supplier and price, and disabling a different supplier's match. No paid model calls were made.

### 14. Simplify order lines

The form now separates items with quiet divider lines rather than nested cards, uses a compact header and controls, and groups price, amount, unit and total beneath the description. Catalog suggestions appear directly beneath their line. Existing editable discounted totals and quote-field highlighting remain available.

### 15. Switch the extraction model to GPT-6 Luna

The backend and Compose defaults now use `gpt-6-luna`. The Chat Completions request uses low reasoning effort, removes `temperature` for GPT-6, and allows a larger completion budget; explicit `OPENAI_MODEL` overrides still take precedence. The legacy temperature setting remains for non-GPT-6 overrides. A mocked regression test checks the request parameters. One live styleGREEN check is documented in [pdf-benchmark.md](pdf-benchmark.md); it was text-only because host Tesseract was unavailable. A combined OCR-plus-Luna evaluation and comparison are still needed. See [OpenAI's GPT-6 guide](https://developers.openai.com/api/docs/guides/latest-model) for parameter compatibility.

### 16. Match quote fields in context and scroll continuously

**Problem:** The same number, such as `1`, appeared in multiple items, and global literal matching incorrectly linked it to unrelated positions. Switching between pages also caused abrupt jumps.

**Original change:** An item description had to have one unambiguous literal text match. Numeric values were linked only when found near that item's description on the same page. Section 19 describes the subsequent matching improvement. Image-heavy or scanned pages can also supply high-confidence Tesseract word boxes converted to PDF coordinates. The viewer displays all pages in one scrollable column and moves smoothly to an off-screen field match when the corresponding form field or item is entered; reduced-motion preferences are respected. Hiding the viewer preserves its scroll position.

**Limitations:** These are contextual matches, not verified model source citations. OCR may misread text, descriptions may be paraphrased, and repeated or geometrically ambiguous text is deliberately unlinked. OCR preview is limited to the first six image-heavy pages and does extra processing. PDF pages are rendered together, which can increase memory use on long documents. A real Gregg quote check linked the two quantities to their respective page 1 and page 2 rows; a local Chrome walkthrough checked scrolling between pages and hide/show. OCR coordinate conversion has a deterministic simulated TSV test, while a live image-only OCR preview check remains needed in Docker.

### 17. Apply Acme's customer-specific categorization

The extraction endpoint, live form and request create/update operations apply Acme's rules based on the signed-in organization: cable ties → Electrical Engineering (11); printer toner → IT Hardware (29); and explicitly monthly software subscriptions costing up to and including €20 per month → Office Supplies (2001). The shared reference taxonomy lacked Office Supplies, so startup seeding adds the new entry without deleting existing data. A mixed request with conflicting Acme rules is rejected with a request to split it.

The €20 rule uses the line's stated total as a monthly amount; it is only applied to a single line with an explicit monthly period. Unclear billing periods and multi-line subscription costs are not guessed. **Acme must validate the €20 threshold, whether tax is included, and how recurring and multi-line charges should be treated.** Rule matching uses a small German/English vocabulary and cannot recognize every product or formulation. Tests cover the threshold, conflicting categories, create/update and another tenant.

### 18. Investigate slow builds

The original app already used `next build --turbopack` and Google-hosted Geist fonts; the added PDF.js dependency increases the frontend build's work, but its effect has not been isolated. During investigation, the host showed high load, heavy swap use and around 4 GB free disk space. A separate standard Next.js build logged network retries, compiled and generated pages, but did not finish trace collection during the check. These observations are not a controlled before/after benchmark, so no single cause is confirmed.

A subsequent local production build completed successfully, including trace collection and the new article page. This is not a timed comparison with the original project, and the rebuilt Docker images have not yet been checked end-to-end.

### 19. Restore contextual quote highlights

**Problem:** The stricter matcher introduced in section 16 hid all fields of some items when a description occurred more than once, was split over lines, or differed from the generated description. It also required one precise number format and did not link units.

**Change:** The preview searches for literal parts of a description and, when a title repeats, can select a single row supported by a matching unit or line price. Quantities and prices accept common unambiguous German and English number formats and remain restricted to that item's row. Explicit unit labels such as `Stk.` can link to the extracted `pieces` field. If a single price is explicitly marked `E.P.`, it can be linked to the unit price without pretending that the calculated line total was printed. Repeated exact vendor names and VAT IDs link to their first occurrence. Form-to-PDF and PDF-to-form interactions include units.

**Trade-offs and verification:** The preview remains a possible text match, not model-supplied provenance. It leaves ambiguous matches, inferred units, calculated totals, unreadable OCR and unsupported page geometry unmarked. A deterministic replay of a previously saved Dream in Green extraction improved from 0 of 14 original fields matched to 17 of 17 fields matched, including the three newly supported unit fields; this is a source-matching check, not a new model accuracy benchmark. A saved styleGREEN extraction matched 16 of 17 fields with native text only; the VAT ID needs OCR. Unit-column selection handles `qm` and `Lfm` even when a similar unit appears in the description. A Chrome walkthrough using a local mock and the Gregg quote checked that the second quantity activated its own page-two match. Backend tests cover repeated titles, matching prices, integer/decimal formats, OCR boxes, absent units, ambiguous values, and the `E.P.` case. OCR word coordinates were simulated in tests; an image-only Docker walkthrough is still outstanding.

### 20. Open suggested catalog articles

Each suggestion's description links to a read-only article detail page in a new browser tab, preserving the unfinished request. It shows the catalog number, description, supplier, unit, price, and catalog quantity. `GET /articles/{id}` requires authentication and checks the organization before returning the record; a record belonging to another organization returns 404. A local browser walkthrough checked the link and preserved form, and backend tests cover authentication and tenant isolation. Applying a suggestion still requires an explicit **Use article** action and the existing supplier and currency checks.

### 21. Restrict requester visibility and initial self-approval policy

The customer initially clarified that buyers and admins can decide other users' pending requests, and admins retain their management permissions. Requesters now see only their own requests; the API applies the same ownership check to details, changes, status, deletion, activity and document access. Older records without `created_by` use `user_id` as the owner. The initial implementation restricted self-approval; the customer subsequently requested a change described in section 24. Authorization is enforced server-side, not just in the interface. API tests cover different roles, a separate organization, direct URLs, attachments and the legacy owner fallback.

**Follow-up:** Section 22 implements the requested approval recheck after edits.

The complete backend test suite passed 61 tests after these changes. Frontend unit tests, type checking and a local production build also passed; the browser checks used a local mock rather than the running Docker backend.

### 22. Recheck approvals after edits and automate Acme's smaller requests

Acme requests below €2,000 are automatically approved when created. The cutoff is strict: €1,999.99 qualifies, while €2,000.00 requires a buyer or admin. Only Acme has this policy; other organizations retain manual approvals. The amount is rounded to the two decimal places stored in the database before comparing it to the threshold, and negative or non-finite request totals are rejected. The policy evaluates the recorded request total; it does not independently verify a supplier's quote or define whether Acme's threshold is gross or net.

When extraction reports a missing or zero total, the quote form leaves the total blank and requires explicit user input. The request editor also rejects a blank total instead of coercing it to zero. A genuinely free request can still be entered as zero deliberately; no system currently verifies that entered amount against the quote.

Every actual content edit or changed PDF attachment invalidates the old decision and re-runs the applicable policy. Thus, an Acme request still below the limit is automatically approved again, and a larger request becomes pending with the old reviewer and timestamp cleared. Unchanged saves and identical document uploads are no-ops. Status transitions are separate workflow actions and do not invalidate approval. Automatic and manual decisions are distinguishable in the API and UI, and activity records explain rechecks. A nullable migration field preserves historical decisions without guessing their source or retroactively approving existing pending requests. PostgreSQL row locks serialize changes and decisions on the same request; a later production version should also prevent reviewers from deciding on stale screen data and validate the source and currency of the recorded amount.

Verification: 68 backend tests passed, including the cutoff, rounding, invalid totals, request edits, document replacement, tenant boundaries and legacy records. The migration was exercised against a temporary PostgreSQL table and rolled back. A browser walkthrough using the real backend and an isolated SQLite database checked €1,999.99 → automatic approval, an edit to €2,000 → pending, buyer approval, and a title edit → pending again. It also checked the admin rules page. No paid model call or existing app-data change was involved; the Docker stack still needs to be rebuilt and exercised.

### 23. Expose Acme's rules and complete the handover files

Admins can inspect the three active category rules and the auto-approval threshold in **Organization → Settings**; the read-only API returns them only for the applicable organization. The €20 assumption is shown with its validation caveat. The original PDF baseline has been copied into the repository so the handover links work, and the README describes the approval policy and test commands. The tracked `.env.example` was cleared of its key value; the private `.env` remains available for local use. The request-detail editor now states accurately that saved order lines cannot currently be edited there.

### 24. Saved-request quote review and self-approval

The request-detail page now reuses the continuously scrolling quote viewer. It rebuilds possible text matches from the saved PDF and current vendor and order-line values using the existing local highlight endpoint; it does not call the model again. Hovering or focusing a matched value shows its PDF counterpart, and choosing a PDF highlight focuses the saved value. The original document is unchanged. Because original extraction coordinates and alternative positions are not stored, this is a fresh contextual match to current fields, not a record of model provenance. Ambiguous, edited or otherwise unmatched fields may have no highlight; the viewer remains usable if matching fails.

Following the customer's clarification, buyers and admins can approve their own pending requests. The API enforces this consistently with both approval screens. The earlier restriction on rejecting one's own request is retained, since the clarification concerned approval. Requesters still cannot make approval decisions. This is a customer policy decision; the absence of independent review should be discussed before production. The below-€2,000 Acme rule and approval rechecks after content edits are unchanged.

Verification: the backend suite passed 68 tests and all 18 frontend tests passed. In a browser against the real backend and an isolated SQLite database, a buyer-owned Gärtner Gregg request showed the two-page continuous PDF; focusing line 2's quantity scrolled to the corresponding second-page occurrence, selecting the PDF description focused the saved field, and the buyer approved the request. The test used no model calls and did not modify the running application database. The Docker stack still needs to be rebuilt and checked separately.

### 25. Source matches for translated descriptions and totals

The Hoffmann example contained an English saved description for a German PDF row, so literal description matching left that entire row unlinked. The preview now attempts a conservative fallback only when a unique numbered row has the same quantity, unit price and line total, and the same pricing signature is not shared by another request line. Such links are explicitly marked as inferred from prices and quantity, because this does not establish semantic equivalence. It also recognises `flat fee` / `Pauschale`, disambiguates repeated quantities against the printed description and price columns, and links a saved total only beside an explicit final-total label. Unlabelled, net-only and ambiguous totals remain unlinked. Both request creation and saved-request detail can link an unchanged total.

Verification: a local replay using the actual Hoffmann PDF and the values visible in the customer's screenshot matched the vendor, total and all five fields for each of its two order lines. A browser walkthrough using the real backend and a disposable SQLite database checked the translated row and total in both directions. Tests cover translated descriptions, duplicate price signatures, subtotal/net labels and duplicate quantities. These checks did not call the model. Manually entered, missing, inferred or edited values need not appear in the original document; textual matching remains a review aid, not proven provenance. The backend suite passed 72 tests, and the frontend suite passed 19 tests.

## Verification and evidence

- The original comparison used five local PDF uploads through the app's API, one model call per quote. AI outputs vary; a single run is a diagnostic baseline, not a statistical benchmark.
- After the initial improvements, one live run of all five PDFs returned successful extraction responses, including the two that previously failed on null VAT IDs. Gärtner Gregg's alternatives were returned separately; the Dream in Green alternative and styleGREEN supplier were still incorrect in that run.
- After strengthening alternative handling, one additional live Dream in Green run returned its two base items, its vertical logo separately as an alternative, and the printed total of €1,847.19. A repeat styleGREEN run still identified the recipient as supplier. These small samples do not establish general accuracy.
- Added deterministic backend regression tests with a simulated model response for null fields, alternative handling, ambiguity and labelled final totals; no API key is needed for these tests.
- Added frontend pricing tests for description edits, discounted lines, price changes, removal and manual requests. The full UI still needs a hands-on check after rebuilding Docker.
- A local OCR check recovered `FlowerArt GmbH` and the VAT ID printed in the styleGREEN image footer. One subsequent live extraction returned the same legal supplier, VAT ID, three items and €1,546.99 total. Two calls were made while investigating the OCR improvement. These are individual runs, not an accuracy benchmark.
- The full backend suite passed: 27 tests, with two dependency deprecation warnings. An initial run stalled under the restricted network sandbox; rerunning outside that sandbox allowed the in-process test client to complete.
- Frontend pricing tests cover swapping an alternative, restoring the original, adding an alternative and pricing-review behavior. A browser walkthrough against a local mock API verified swap/add actions and the desktop and narrow layouts. A full walkthrough against the rebuilt Docker stack remains to be done.
- Frontend type checking and production build were run locally.
- The follow-up pricing fix was checked using the actual extracted text from the Gärtner Gregg and Hoffmann PDFs, deterministic regression tests for mismatched totals, mixed rates and OCR-only totals, and frontend tests for adding, swapping, removing and restoring items. A browser walkthrough against a local mock API showed €4,383.96 after adding the €1,926 alternative and €2,385.95 after replacing the base item with the €1,685 alternative. The mock tests do not measure AI extraction quality. The full backend suite passed 35 tests, with two existing dependency deprecation warnings; frontend pricing tests, type checking and production build passed. No additional paid model calls were made for this follow-up.
- The existing backend suite includes role checks for approvals and admin-only settings, plus organization-scoped supplier data. These checks are useful but do not establish complete authorization or tenant isolation coverage for every endpoint or the planned catalog suggestions.
- For the follow-up review improvements, frontend tests cover removing and re-adding an alternative, including a modified or swapped line, and checking the VAT breakdown. Backend tests cover the annotated preview, unhighlighted fields, rotated-page fallback, endpoint authentication and malformed input. The full backend suite passed 39 tests with two dependency deprecation warnings; frontend tests and type checking passed. A mock browser walkthrough verified adding, removing and re-adding the alternative and the changing breakdown; the annotated Gärtner Gregg PDF was rendered and inspected, and the embedded viewer was checked in Chrome. The Codex in-app browser did not render its embedded PDF viewer. No model calls were made for this follow-up.
- A subsequent local check parsed all five supplied PDFs successfully and verified FlowerArt GmbH and its VAT ID in the styleGREEN OCR supplement. The full backend suite again passed 39 tests. The planned 15-call repeated live model benchmark was blocked by automatic approval review and has not been run. See [pdf-benchmark.md](pdf-benchmark.md) for the local results, the approval requirement and the proposed methodology.
- For the split viewer, local browser checks with a mock API covered PDF rendering, PDF-to-form selection, form-to-PDF focus, hiding and showing the viewer, edited-field unlinking and the stacked narrow-screen layout. Frontend unit tests cover edited fields and swapping alternatives; backend preview tests check returned coordinates and page restrictions. No paid model calls were used.

### API documentation

FastAPI serves an interactive schema at `/docs`. `/pdf/parse` now returns `warnings` alongside `text`; `/extraction` returns structured data, missing fields and warnings. Protected operations use the app's bearer token. Trying an extraction from the docs calls the configured model and may consume API credits. The docs are useful for development, but exposing interactive documentation and endpoints publicly should be an explicit deployment decision.

### Model selection and evaluation

The original default was `gpt-4o-mini`; it has now been changed to `gpt-6-luna`, as described in section 15. As of 2026-11-01, OpenAI's [GPT-4o Mini page](https://developers.openai.com/api/docs/models/gpt-4o-mini) lists it as deprecated and prices standard text tokens at $0.15 input / $0.60 output per million. [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) is priced at $0.10 input / $0.50 output per million. Token prices do not establish total cost or quality for this workload. The latest 15-call benchmark is documented in [pdf-benchmark.md](pdf-benchmark.md); the effective model name of the running API was not independently confirmed.

Run the targeted tests from the app directory (using a Python environment with project dependencies, `pytest`, and `httpx` installed):

```bash
cd backend
OPENAI_API_KEY= python -m pytest tests/ -q
```

For pricing tests, with a Node version that supports TypeScript type stripping (the current local environment uses Node 24):

```bash
cd frontend
node --test tests/*.test.mjs
npx tsc --noEmit
npm run build
```

## Remaining assignment work

- Validate Acme's €20-per-month threshold, tax basis and treatment of ambiguous or multi-line subscriptions.
- Verify supplier extraction and OCR against additional supplier layouts, including low-quality scans and ambiguous totals, and complete a full browser walkthrough with the real backend.
- Validate Acme's approval threshold basis and add trusted total/currency controls before production.
- Confirm whether a pending or rejected request should be allowed to move to the closed status; status and approval are currently independent.
- After final review, push the improvements branch and open a PR against main with the handover and trade-offs.
