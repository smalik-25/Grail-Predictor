-- purpose: per-family resale time series with rolling stats and the spread; labeling, features, and the dashboard read this
with daily as (
    select * from {{ ref('int_family_daily_resale') }}
),

velocity as (
    select family_id, sold_date, monthly_price_velocity
    from {{ ref('int_family_price_velocity') }}
),

spread as (
    select family_id, sold_date, retail_reference_usd, spread_ratio, spread_basis
    from {{ ref('int_family_spread') }}
)

select
    daily.family_id || ':' || daily.sold_date as price_history_key,
    daily.family_id,
    daily.sold_date,
    daily.sold_price_usd,
    daily.n_sales,
    round(avg(daily.sold_price_usd) over (
        partition by daily.family_id
        order by daily.sold_date
        rows between 2 preceding and current row
    ), 2) as rolling_avg_3_sales_usd,
    daily.sold_price_usd - lag(daily.sold_price_usd) over (
        partition by daily.family_id order by daily.sold_date
    ) as delta_vs_prev_sale_usd,
    velocity.monthly_price_velocity,
    spread.retail_reference_usd,
    spread.spread_ratio,
    spread.spread_basis
from daily
left join velocity using (family_id, sold_date)
left join spread using (family_id, sold_date)
