-- purpose: realized sale events per style-family with colorway tier, the resale ground truth
select
    family_id,
    colorway_tier,
    platform_name,
    sold_date,
    sold_price_usd
from {{ ref('stg_listings') }}
where family_id is not null
  and is_sold
  and sold_price_usd is not null
  and sold_date is not null
