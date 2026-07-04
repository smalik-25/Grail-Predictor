-- purpose: the forecasting grain: brand -> model-line -> era, typed and passed through
select
    family_id,
    brand,
    model_line,
    era,
    colorway_tiers,
    first_seen_date
from {{ source('grail', 'dim_style_families') }}
