-- purpose: one row per family per day with realized resale price; the base time series for forecasting
select
    family_id,
    sold_date,
    round(avg(sold_price_usd), 2) as sold_price_usd,
    count(*) as n_sales
from {{ ref('int_family_sales') }}
group by family_id, sold_date
