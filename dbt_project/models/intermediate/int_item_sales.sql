-- purpose: realized sale events per canonical item, the ground truth for resale value
select
    item_id,
    platform_name,
    sold_date,
    sold_price_usd
from {{ ref('stg_listings') }}
where is_sold
  and sold_price_usd is not null
  and sold_date is not null
