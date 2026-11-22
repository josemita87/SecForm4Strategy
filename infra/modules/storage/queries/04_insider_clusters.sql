-- Clusters: companies where 3 or more distinct insiders sold in the same week
SELECT ticker, date_trunc('week', accepted_at) AS week,
       count(DISTINCT insider_cik) AS insiders,
       round(sum(shares * price) / 1e6, 1) AS value_musd
FROM bt4_v1
WHERE coding = 'S' AND NOT derivative AND ticker IS NOT NULL
GROUP BY 1, 2
HAVING count(DISTINCT insider_cik) >= 3
ORDER BY week DESC, insiders DESC
LIMIT 25
