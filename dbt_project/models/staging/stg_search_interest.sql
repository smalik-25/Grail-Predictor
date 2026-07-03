-- purpose: Google Trends weekly relative interest, typed and passed through
select
    search_interest_id,
    item_id,
    brand,
    keyword,
    observed_date,
    interest_index
from {{ source('grail', 'fact_search_interest') }}
