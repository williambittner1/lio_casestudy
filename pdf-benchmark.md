# PDF extraction verification

## Current status

The requested repeated benchmark was run on 2026-11-01 after the user explicitly authorized 15 model extractions. An earlier attempt had been rejected by automatic approval review; no calls were made in that earlier attempt.

## Repeated live benchmark (2026-11-01)

Signed in as the seeded Acme admin to the running local API. Parsed each of the five supplied PDFs once with `/pdf/parse`, then submitted each parsed text three times to `/extraction`: **15 extraction calls, 15 successful responses**. Three requests ran concurrently. No procurement requests were created. The parser reported no warnings; the styleGREEN parse contained an OCR supplement. This tests model variation and backend post-processing with one OCR result per document; it does not measure OCR variability or test the frontend. Docker daemon access was unavailable, so the running image version and effective model name could not be independently verified. The checked-in default is `gpt-6-luna`, but an environment override can change it. Do not claim the exact model or rebuilt image was verified for this run.

| Quote | Successful runs | Supplier and VAT ID | Base positions and alternatives | Printed gross total |
| --- | ---: | --- | --- | --- |
| Dream in Green | 3/3 | Correct | Two base positions, including the discounted line; vertical logo classified as an alternative in all 3 | €1,847.19 in all 3 |
| styleGREEN | 3/3 | FlowerArt GmbH and DE271073640 in all 3 | Three expected positions in all 3 | €1,546.99 in all 3 |
| Gärtner Gregg | 3/3 | Correct | Two base positions and two alternatives in all 3 | €2,092.02 in all 3 |
| Hoffmann Nürnberg | 3/3 | Correct supplier; VAT ID blank in all 3 | Two expected positions in all 3 | €20,230.00 in all 3 |
| Apple | 3/3 | Apple and DE258811348 in all 3 | One MacBook at the expected price and quantity in all 3 | €1,759.01 in all 3 |

Supplier comparison accepts Apple as the vendor and tolerates accents; one Hoffmann result omitted umlauts. Amounts and quantities were compared with the documented quote values, not the wording of every description. All 15 returned the expected gross totals and base/alternative price and quantity patterns; all 6 runs of the two quotes containing alternatives separated the expected alternatives. No unexpected alternatives appeared in the remaining 9 runs. Missing Hoffmann VAT ID was correctly left blank. These checks do not establish that every extracted field, description, unit or source highlight was correct.

Observed variation: descriptions sometimes switched between German and English or differed in detail. Apple's category was Hardware once and Business Laptop — Mid-range twice, both IT categories; customer confirmation is needed on the desired level of specificity. The extracted tax rate was blank for Dream in Green, styleGREEN and Apple, so those results do not supply a complete VAT breakdown even though the printed gross total was correct. Empty or inferred units and short descriptions still require review. All styleGREEN responses warned that supplier details came from an image. Individual extraction response times were approximately 2.9–5.4 seconds; these are local end-to-end API timings, not a provider latency benchmark. API usage/cost was not captured.

This small evaluation covers five supplied examples and three runs per example. It is not a statistically strong reliability claim. Additional unseen suppliers, scan quality, page layouts, currencies, taxes and alternative patterns should be tested. Raw response data was stored temporarily outside the repository; no credentials were recorded.

## One approved GPT-6 Luna check (2026-11-01)

One model call was authorized and run on the styleGREEN quote using text parsed on the host. GPT-6 Luna returned `FlowerArt GmbH (styleGREEN)`, the three expected lines and €1,546.99, in 7.3 seconds; VAT ID was empty. The host has no Tesseract, so this model call did **not** include OCR text. Separately, the already-running Docker backend parsed the same quote with OCR, finding both FlowerArt GmbH and its VAT ID in the supplementary text without another model call. This does not establish whether Luna will extract the VAT ID when provided Docker's OCR text. One call is not a benchmark.

## Local checks completed

The running local API parsed all five PDFs successfully, with no parsing warnings. Each PDF was parsed once. The image-heavy styleGREEN PDF produced supplementary OCR text; a separate local check confirmed that the OCR supplement itself contains both `FlowerArt GmbH` and its VAT ID. These checks exercise parsing and OCR, not the accuracy of the model's extraction or classification.

| Quote | Local parse | OCR supplement |
| --- | --- | --- |
| Dream in Green, AN-4120 | Success | Not needed by current heuristic |
| styleGREEN, OF2312380 | Success | Present; supplier and VAT ID recovered in OCR text |
| Gärtner Gregg, A0492/23 | Success | Not needed by current heuristic |
| Hoffmann Nürnberg | Success | Not needed by current heuristic |
| Apple | Success | Not needed by current heuristic |

The backend regression suite passed 39 tests at that stage, including mocked extraction and OCR tests and the PDF-preview checks. These deterministic tests are not a substitute for repeated real-model results. The original one-run baseline and its expected values are recorded in [quote-extraction-baseline.md](quote-extraction-baseline.md); the source PDFs remain in the supplied case-study package.

## Method used for the model benchmark

Use the current running local app and its configured model. Parse each supplied PDF once with `/pdf/parse`, then send the returned text to `/extraction` three times. The cached parse means the test measures model variation for one OCR result per quote; it does not measure OCR variability. Record success, supplier, VAT ID, base and alternative line prices and quantities, printed gross total, classification and warnings for each run. Compare against the supplied PDFs, not merely against a previous model response.

Review particularly: missing VAT IDs must not break extraction; Gärtner Gregg's two picture alternatives and Dream in Green's vertical logo must stay out of the base order; the Dream in Green discounted price and printed total must be preserved; and styleGREEN must identify FlowerArt GmbH rather than the customer. Record partial failures and ambiguous values rather than counting any successful HTTP response as correct.

Three runs per quote are a small diagnostic sample, not proof of reliability or a statistically robust comparison against the original single-run baseline. Repeat on additional supplier layouts and scans for a stronger evaluation. Store detailed outputs locally and avoid committing credentials or confidential quote contents.
