-- purpose: the spread signal at family grain: resale relative to retail, brand proxy until item-level linkage lands.
-- spread_basis flips to 'item' per row when retail-to-family linkage exists; consumers never change.
select
    d.family_id,
    d.sold_date,
    d.sold_price_usd,
    br.brand_retail_median_usd as retail_reference_usd,
    case
        when br.brand_retail_median_usd > 0
        then round((d.sold_price_usd / br.brand_retail_median_usd)::numeric, 3)
    end as spread_ratio,
    'brand_proxy' as spread_basis
from {{ ref('int_family_daily_resale') }} d
join {{ ref('stg_style_families') }} f using (family_id)
left join {{ ref('int_brand_retail_baseline') }} br on br.brand = f.brand
