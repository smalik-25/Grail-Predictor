-- purpose: how thin and how fast each item's market is: listing counts and sold-through
select
    item_id,
    count(*) as n_listings,
    count(*) filter (where not is_sold) as n_active,
    count(*) filter (where is_sold) as n_sold,
    round((count(*) filter (where is_sold))::numeric / count(*), 3) as sold_through_rate
from {{ ref('stg_listings') }}
group by item_id
