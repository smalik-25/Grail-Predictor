-- purpose: platform dimension, typed and passed through
select
    platform_id,
    platform_name,
    platform_type
from {{ source('grail', 'dim_platforms') }}
