-- ============================================================================
-- CA-S8 : requêtes EXACTES des dashboards Superset (spec fonctionnelle §4.2,
-- écrans F1/F2/F3). Le TCK les exécute telles quelles en rôle `reader` contre
-- les matviews gold PostgreSQL. Placeholders {from} / {to} remplacés par le
-- harness (fenêtre ISO 8601 récente). Aucune modification de requête autorisée.
-- ============================================================================

-- F1 Dashboard gestionnaire : synthèse ville
SELECT * FROM gold.city_dashboard_summary
WHERE updated_at >= '{from}'::timestamptz AND updated_at <= '{to}'::timestamptz
ORDER BY updated_at DESC LIMIT 5000;

-- F1/F2 : KPIs réseau horaires par site
SELECT * FROM gold.net_grid_hourly
WHERE hour >= '{from}'::timestamptz AND hour <= '{to}'::timestamptz
ORDER BY hour DESC LIMIT 5000;

-- F2 Détail bâtiment : coût cumulé
SELECT * FROM gold.energy_cost_daily
WHERE day >= '{from}'::date AND day <= '{to}'::date
ORDER BY day DESC LIMIT 5000;

-- F2 : performance PV (capacity_factor)
SELECT * FROM gold.pv_performance_hourly
WHERE hour >= '{from}'::timestamptz AND hour <= '{to}'::timestamptz
ORDER BY hour DESC LIMIT 5000;

-- F3 Anomalies actives
SELECT * FROM gold.anomaly_candidates
WHERE event_timestamp >= '{from}'::timestamptz AND event_timestamp <= '{to}'::timestamptz
ORDER BY event_timestamp DESC LIMIT 5000;
