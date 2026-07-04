-- purpose: latest snapshot per style-family; the watchlist and the model's inference path read this row
with families as (
    select * from {{ ref('stg_style_families') }}
),

liquidity as (
    select * from {{ ref('int_family_liquidity') }}
),

latest_sale as (
    select distinct on (family_id)
        family_id,
        sold_date as last_sold_date,
        sold_price_usd as latest_sold_price_usd
    from {{ ref('int_family_daily_resale') }}
    order by family_id, sold_date desc
),

median_sale as (
    select
        family_id,
        percentile_cont(0.5) within group (order by sold_price_usd) as median_sold_price_usd
    from {{ ref('int_family_sales') }}
    group by family_id
),

tier_premium as (
    -- the hot-colorway premium: best-tier median over family median, the
    -- sub-attribute signal the grain exists to preserve
    select
        family_id,
        percentile_cont(0.5) within group (order by sold_price_usd)
            filter (where colorway_tier = 'rare') as rare_tier_median_usd
    from {{ ref('int_family_sales') }}
    group by family_id
),

brand_retail as (
    select * from {{ ref('int_brand_retail_baseline') }}
),

brand_scarcity as (
    select
        families.brand,
        avg(liquidity.n_active) as brand_avg_active
    from liquidity
    join families using (family_id)
    group by families.brand
)

select
    families.family_id,
    families.brand,
    families.model_line,
    families.era,
    families.colorway_tiers,
    families.first_seen_date,
    coalesce(liquidity.n_listings, 0) as n_listings,
    coalesce(liquidity.n_active, 0) as n_active,
    coalesce(liquidity.n_sold, 0) as n_sold,
    liquidity.sold_through_rate,
    latest_sale.last_sold_date,
    latest_sale.latest_sold_price_usd,
    round(median_sale.median_sold_price_usd::numeric, 2) as median_sold_price_usd,
    case
        when median_sale.median_sold_price_usd > 0
        then round((tier_premium.rare_tier_median_usd / median_sale.median_sold_price_usd)::numeric, 3)
    end as rare_tier_premium_ratio,
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
from families
left join liquidity using (family_id)
left join latest_sale using (family_id)
left join median_sale using (family_id)
left join tier_premium using (family_id)
left join brand_retail on brand_retail.brand = families.brand
left join brand_scarcity on brand_scarcity.brand = families.brand
