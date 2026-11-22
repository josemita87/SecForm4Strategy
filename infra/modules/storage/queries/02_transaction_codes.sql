-- What a Form 4 reports: transaction codes per year (P = open-market buy, S = open-market sale, M = option exercise, F = tax withholding, G = gift, A = grant)
SELECT year(accepted_at) AS year, coding, count(*) AS transactions
FROM bt4_v1
GROUP BY 1, 2
HAVING count(*) > 1000
ORDER BY year, transactions DESC
