-- purpose: canonical items, typed and passed through; the spine every mart joins to
select
    item_id,
    canonical_title,
    brand,
    category,
    season,
    first_seen_date
from {{ source('grail', 'dim_items') }}
