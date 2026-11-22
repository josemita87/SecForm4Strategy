-- Largest open-market insider sales in the last 30 days of data; the reported price must be within 50% of that day's close (filters filer typos)
SELECT t.accepted_at, t.ticker, t.insider_name, t.shares, t.price, p.close_as_traded AS close_that_day,
       round(t.shares * t.price / 1e6, 1) AS value_musd, t.rule105b1 AS planned_10b5_1
FROM bt4_v1 t
JOIN p_v1 p ON p.ticker = t.ticker AND p.date = date_trunc('day', t.date)
WHERE t.coding = 'S' AND NOT t.derivative
  AND t.accepted_at > (SELECT max(accepted_at) FROM bt4_v1) - INTERVAL '30' DAY
  AND p.date > (SELECT max(accepted_at) FROM bt4_v1) - INTERVAL '60' DAY
  AND t.price BETWEEN 0.5 * p.close_as_traded AND 1.5 * p.close_as_traded
ORDER BY t.shares * t.price DESC
LIMIT 20
