-- purpose: listings with platform names resolved and prices normalized to USD via the fx seed
with fx as (
    select currency, usd_per_unit from {{ ref('fx_rates') }}
)

select
    l.listing_id,
    l.item_id,
    p.platform_name,
    l.listing_url,
    l.listed_date,
    l.asking_price,
    trim(l.currency) as currency,
    round(l.asking_price * fx.usd_per_unit, 2) as asking_price_usd,
    l.size_normalized,
    l.condition_ordinal,
    l.is_sold,
    l.sold_date,
    l.sold_price,
    round(l.sold_price * fx.usd_per_unit, 2) as sold_price_usd
from {{ source('grail', 'fact_listings') }} l
join {{ source('grail', 'dim_platforms') }} p using (platform_id)
left join fx on fx.currency = trim(l.currency)
