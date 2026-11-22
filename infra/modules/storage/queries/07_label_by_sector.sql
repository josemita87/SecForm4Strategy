-- What the model learns from: average next-print return after insider sales vs buys, per sector group
SELECT sic_group, sale, count(*) AS rows_,
       round(avg(pct_change) * 100, 3) AS avg_pct_change,
       round(approx_percentile(pct_change, 0.5) * 100, 3) AS median_pct_change
FROM rt4_v1
WHERE pct_change IS NOT NULL
GROUP BY 1, 2
HAVING count(*) >= 500
ORDER BY sic_group, sale
