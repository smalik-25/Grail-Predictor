-- purpose: rate of change of realized resale price per family, normalized to a 30-day pace
with ordered as (
    select
        family_id,
        sold_date,
        sold_price_usd,
        lag(sold_price_usd) over w as prev_price_usd,
        lag(sold_date) over w as prev_sold_date
    from {{ ref('int_family_daily_resale') }}
    window w as (partition by family_id order by sold_date)
)

select
    family_id,
    sold_date,
    sold_price_usd,
    prev_price_usd,
    sold_date - prev_sold_date as days_between_sales,
    case
        when prev_price_usd > 0 and sold_date > prev_sold_date
        then round(((sold_price_usd - prev_price_usd) / prev_price_usd
                    / (sold_date - prev_sold_date) * 30)::numeric, 4)
    end as monthly_price_velocity
from ordered
