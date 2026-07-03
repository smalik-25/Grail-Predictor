-- purpose: retail shelf-price observations with prices normalized to USD
with fx as (
    select currency, usd_per_unit from {{ ref('fx_rates') }}
)

select
    r.retail_price_id,
    r.item_id,
    p.platform_name,
    r.brand,
    r.product_name,
    r.product_url,
    r.observed_date,
    r.retail_price,
    trim(r.currency) as currency,
    round(r.retail_price * fx.usd_per_unit, 2) as retail_price_usd,
    r.in_stock
from {{ source('grail', 'fact_retail_prices') }} r
join {{ source('grail', 'dim_platforms') }} p using (platform_id)
left join fx on fx.currency = trim(r.currency)
