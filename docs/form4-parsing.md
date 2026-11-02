# Form 4 parsing

How the scraper (`sec-feature-pipeline/scraper`) turns one SEC filing into transaction records, and the decisions behind it. The XML is declared in `form4.py`, the record in `transaction.py`, and the mapping in `parser.py`.

## Source

The scraper fetches the full submission (`<accession>.txt`). It is an SGML wrapper, not XML: the EDGAR header carries `<ACCEPTANCE-DATETIME>` (US/Eastern), and an `<XML>` block carries the filer's `<ownershipDocument>`. Each part is cut out and typed by `Submission`. A filing without either part, or one that fails to parse, is logged and yields no records.

## Decisions

- **Acceptance time:** every record's `accepted_at` is when SEC accepted the filing (timezone-aware; ISO 8601 in messages, a UTC timestamp column in the feature store), the moment it became public. The insider's trade `date` is kept as a feature but never times a label.
- **One reporting owner:** a joint filing (several `<reportingOwner>`s, e.g. a fund, its general partner and the managing individuals) is attributed to the first owner listed; the others are ignored. Kept deliberately for now. Revisit if owner identity starts to drive the strategy (e.g. scoring by role).
- **Derivative flag:** set by the table a transaction comes from (`nonDerivativeTable` or `derivativeTable`). Both tables share one layout.
- **Key:** a hash of accession, table and position in the table (see `configuration.md`). A transaction that fails validation keeps its position, so its neighbours' keys do not move. The `accession` and `position` themselves are stored too, so downstream steps can count filings and order transactions within them.
- **Rule 10b5-1:** flagged by the filing's `aff10b5One` checkbox (on Form 4 since April 2023) or by any footnote attached anywhere in the transaction that mentions `10b5-1`; filers mostly attach it to the transaction code.
- **Holdings:** `nonDerivativeHolding` / `derivativeHolding` rows list accounts the filing does not trade. Their shares are summed per ownership (direct, indirect) from the transaction's own table, each 0 when none is listed. They are disjoint from `remaining_shares` (the traded accounts after the trade), so target adds the three into `total_held`.
- **Owner code:** the four relationship flags (director, officer, 10% owner, other) as a four-character bit string.
- **Tickers:** stripped and upper-cased; placeholders (`NONE`, `N/A`, `NA`, dashes) become missing, so the row is dropped downstream rather than priced.
- **Missing and malformed values:** raw text is stripped; an absent value takes the record's default (0 shares, 0 price, direct ownership); a value that fails validation drops that transaction only, with a warning.
