-- Prices are split-adjusted: NVDA around its 10-for-1 split on 2024-06-10 (close vs close_as_traded)
SELECT date, open, close, close_as_traded, split
FROM p_v1
WHERE ticker = 'NVDA' AND date BETWEEN TIMESTAMP '2024-06-03' AND TIMESTAMP '2024-06-14'
ORDER BY date
