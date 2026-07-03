-- purpose: brand-level retail reference price; a stated proxy until item-level retail linkage lands
select
    brand,
    percentile_cont(0.5) within group (order by retail_price_usd) as brand_retail_median_usd,
    max(observed_date) as as_of_date
from {{ ref('stg_retail_prices') }}
where retail_price_usd is not null
  and brand is not null
group by brand
