# PDF extraction baseline

Date: 2026-10-31. One run per quote against the existing local Lio API, signed in as the seeded Acme admin. Each PDF was sent through `/pdf/parse` and the resulting text through `/extraction`, the same two endpoints used by the new-request screen. No procurement requests were created. These are observations from one run; AI output may vary. Expected values are transcribed from the supplied PDFs, and costs below are in EUR. Source PDFs are in the accompanying case-study package rather than this repository.

## Results at a glance

| Quote | PDF parse | Extraction | Outcome |
| --- | --- | --- | --- |
| Dream in Green, 4120 | Success | Success | Correct supplier and gross total; an alternative line was included as an ordinary order line. |
| styleGREEN, OF2312380 | Success | Failed | AI returned `null` for VAT ID; response validation rejected it and no extracted data was returned. |
| Gärtner Gregg, A0492/23 | Success | Success | Correct supplier, VAT ID and gross total; two alternative lines were included as ordinary order lines. |
| Hoffmann Nürnberg, ANG-HN-2026-041 | Success | Failed | AI returned `null` for VAT ID; response validation rejected it and no extracted data was returned. |
| Apple, 2212618452 | Success | Success | Correct item, VAT ID and gross total; category was Office Equipment rather than an IT/hardware category. |

## Dream in Green, quote 4120

Source: `quotes/AN-4120-Kdnr-14918.pdf`. Supplier is Dream in Green GmbH, VAT ID DE325240530. The baseline offer has a moss picture (quantity 1, unit price 894.08, discounted line total 715.26) and a horizontal logo (quantity 1, 622.00); the vertical logo at 430.00 is explicitly an alternative. Net positions total 1,337.26; shipping is 215.00 net; the document's final total is 1,847.19 gross.

Lio returned the correct supplier, VAT ID and 1,847.19 total, with all three positions presented as normal order lines, including the 430.00 alternative. The category was Office Equipment. The department was set to Lio Technologies GmbH, which the document identifies as the customer rather than a department.

## styleGREEN, quote OF2312380

Source: `quotes/AN-OF2312380-Kdnr-57692.pdf`. The quote identifies the seller as styleGREEN and mentions FlowerArt GmbH in its terms. The listed positions are: moss module 1.28 × 559.00 = 715.52; edging 4.80 × 25.13 = 120.62; logo 1 × 350.00 = 350.00. Positions total 1,186.14 net, plus 113.85 net shipping and taxes; the final total is 1,546.99 gross. VAT ID is not apparent in the supplied document text.

Lio returned `success: false` because `vatId` was null and the response schema expected a string. Consequently, no item or total data was returned for comparison.

## Gärtner Gregg, quote A0492/23

Source: `quotes/AngebotA0492_23.Pdf`. Supplier is Gärtner Gregg, VAT ID DE198570491. The base offer has a moss picture (1 × 1,438.00) and transport, packaging and shipping (1 × 320.00). Two alternative picture configurations are separately priced at 1,926.00 and 1,685.00; neither is part of the stated base total. Net total is 1,758.00, VAT is 334.02, and gross total is 2,092.02.

The API returned the correct supplier, VAT ID and gross total, but also returned both alternative configurations as normal order lines without identifying them as alternatives. It set department to Lio Technologies GmbH, the named customer. It supplied `pieces` as unit even though no unit is shown for the relevant positions in the quote.

The pre-existing result visible in the browser before this run likewise contained both alternatives and showed a total cost of 4,099.00. Its extraction history and any manual edits are unknown, so it is a separate observation rather than a result of the controlled API run.

## Hoffmann Nürnberg, quote ANG-HN-2026-041

Source: `quotes/Angebot_Hoffmann_Nuernberg_ACME_Schleifmaschine.pdf`. Supplier is Hoffmann Nürnberg GmbH Qualitätswerkzeuge. The quote lists a grinding machine (1 × 15,000.00) and delivery and installation (1 × 2,000.00). Net total is 17,000.00, VAT is 3,230.00, and gross total is 20,230.00. No VAT ID appears in the extracted document text.

Lio returned `success: false` because `vatId` was null and the response schema expected a string. Consequently, no item or total data was returned for comparison.

## Apple, quote 2212618452

Source: `quotes/Quote_1__Lio_Technologies_GmbH__1x_MBA___2212618452.pdf`. The offer is from the Apple Business Team, VAT ID DE258811348. It lists one 13-inch MacBook Air at 1,467.61, VAT of 280.85 and a copyright levy of 10.55, with a total of 1,759.01.

Lio returned the item, quantity, price, VAT ID and total correctly. It classified the laptop as Facility Management / Office Equipment; the taxonomy contains Information Technology / Hardware, a more natural match. It also populated department with Lio Technologies GmbH, the customer named on the quote.

## Priorities suggested by this baseline

1. Handle absent or null optional fields without rejecting the entire extraction, and report missing fields for human review.
2. Distinguish alternative positions from the base offer, rather than treating them as additional purchases.
3. Define and preserve the meaning of totals, discounts, shipping, tax and fees; compare line totals with the stated offer total without assuming they must match.
4. Improve classification and distinguish the customer company from an actual department; do not invent missing units.

At the time of the baseline, the request form recalculated total cost as the sum of line totals whenever a line was edited or removed. That behavior could replace a gross quote total with a sum of net line amounts; the later changes and verification are described in [SOLUTION_NOTES.md](SOLUTION_NOTES.md).
