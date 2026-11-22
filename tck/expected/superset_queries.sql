-- ============================================================================
-- CA-S8: EXACT Superset dashboard queries (functional spec §4.2,
-- screens F1/F2/F3). The TCK runs them as-is in role `reader` against
-- the PostgreSQL gold matviews. {from} / {to} placeholders are replaced by the
-- harness (recent ISO 8601 window). No query modification allowed.
-- ============================================================================

-- F1 Manager dashboard: city summary
SELECT * FROM gold.city_dashboard_summary
WHERE updated_at >= '{from}'::timestamptz AND updated_at <= '{to}'::timestamptz
ORDER BY updated_at DESC LIMIT 5000;

-- F1/F2: hourly grid KPIs per site
SELECT * FROM gold.net_grid_hourly
WHERE hour >= '{from}'::timestamptz AND hour <= '{to}'::timestamptz
ORDER BY hour DESC LIMIT 5000;

-- F2 Building detail: cumulative cost
SELECT * FROM gold.energy_cost_daily
WHERE day >= '{from}'::date AND day <= '{to}'::date
ORDER BY day DESC LIMIT 5000;

-- F2: PV performance (capacity_factor)
SELECT * FROM gold.pv_performance_hourly
WHERE hour >= '{from}'::timestamptz AND hour <= '{to}'::timestamptz
ORDER BY hour DESC LIMIT 5000;

-- F3 Active anomalies
SELECT * FROM gold.anomaly_candidates
WHERE event_timestamp >= '{from}'::timestamptz AND event_timestamp <= '{to}'::timestamptz
ORDER BY event_timestamp DESC LIMIT 5000;
