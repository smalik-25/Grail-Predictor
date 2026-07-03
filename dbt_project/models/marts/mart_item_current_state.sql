-- purpose: latest snapshot per item; the single row the dashboard and the model's inference path read
with items as (
    select * from {{ ref('stg_items') }}
),

liquidity as (
    select * from {{ ref('int_listing_liquidity') }}
),

latest_sale as (
    select distinct on (item_id)
        item_id,
        sold_date as last_sold_date,
        sold_price_usd as latest_sold_price_usd
    from {{ ref('int_item_daily_resale') }}
    order by item_id, sold_date desc
),

median_sale as (
    select
        item_id,
        percentile_cont(0.5) within group (order by sold_price_usd) as median_sold_price_usd
    from {{ ref('int_item_sales') }}
    group by item_id
),

brand_retail as (
    select * from {{ ref('int_brand_retail_baseline') }}
),

brand_scarcity as (
    select
        items.brand,
        avg(liquidity.n_active) as brand_avg_active
    from liquidity
    join items using (item_id)
    where items.brand is not null
    group by items.brand
)

select
    items.item_id,
    items.canonical_title,
    items.brand,
    items.category,
    items.season,
    items.first_seen_date,
    coalesce(liquidity.n_listings, 0) as n_listings,
    coalesce(liquidity.n_active, 0) as n_active,
    coalesce(liquidity.n_sold, 0) as n_sold,
    liquidity.sold_through_rate,
    latest_sale.last_sold_date,
    latest_sale.latest_sold_price_usd,
    round(median_sale.median_sold_price_usd::numeric, 2) as median_sold_price_usd,
    round(brand_retail.brand_retail_median_usd::numeric, 2) as brand_retail_median_usd,
    case
        when brand_retail.brand_retail_median_usd > 0
        then round((latest_sale.latest_sold_price_usd / brand_retail.brand_retail_median_usd)::numeric, 3)
    end as spread_ratio_latest,
    'brand_proxy' as spread_basis,
    case
        when brand_scarcity.brand_avg_active > 0
        then round(liquidity.n_active / brand_scarcity.brand_avg_active, 3)
    end as scarcity_vs_brand
from items
left join liquidity using (item_id)
left join latest_sale using (item_id)
left join median_sale using (item_id)
left join brand_retail on brand_retail.brand = items.brand
left join brand_scarcity on brand_scarcity.brand = items.brand
