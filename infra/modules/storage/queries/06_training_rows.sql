-- Training rows (RT4): features plus the label; label_known_at is always after accepted_at, so no look-ahead
SELECT ticker, accepted_at, next_print, label_known_at, n_insiders, officer, sale,
       round(tx_value / 1e6, 2) AS tx_value_musd, sic_group, filing_lag_days,
       round(pct_change * 100, 2) AS pct_change_pct
FROM rt4_v1
WHERE sale
ORDER BY accepted_at DESC
LIMIT 20
