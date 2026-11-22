-- How much data the lakehouse holds: rows and time span per table
SELECT 'bt4_v1 (transactions)' AS table_name, count(*) AS rows_, min(accepted_at) AS first_ts, max(accepted_at) AS last_ts FROM bt4_v1
UNION ALL SELECT 'p_v1 (daily prices)', count(*), min(date), max(date) FROM p_v1
UNION ALL SELECT 'rt4_v1 (training rows)', count(*), min(accepted_at), max(accepted_at) FROM rt4_v1
UNION ALL SELECT 'bi4_v1 (live transactions)', count(*), min(accepted_at), max(accepted_at) FROM bi4_v1
UNION ALL SELECT 'bir4_v1 (live scoring rows)', count(*), min(accepted_at), max(accepted_at) FROM bir4_v1
