-- purpose: Reddit posts from watched subs, typed and passed through
select
    social_mention_id,
    item_id,
    brand,
    subreddit,
    post_id,
    title,
    post_url,
    created_date,
    score,
    num_comments
from {{ source('grail', 'fact_social_mentions') }}
