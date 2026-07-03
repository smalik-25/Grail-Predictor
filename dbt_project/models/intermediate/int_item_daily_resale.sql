-- purpose: one row per item per day with realized resale price; the base time series
select
    item_id,
    sold_date,
    round(avg(sold_price_usd), 2) as sold_price_usd,
    count(*) as n_sales
from {{ ref('int_item_sales') }}
group by item_id, sold_date
