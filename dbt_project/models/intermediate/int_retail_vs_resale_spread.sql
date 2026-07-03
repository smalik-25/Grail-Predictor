-- purpose: the spread signal: what a piece trades for relative to what it costs new.
-- spread_basis is 'brand_proxy' until retail-to-item linkage exists; when it does,
-- item-linked rows switch basis to 'item' without consumers changing.
select
    d.item_id,
    d.sold_date,
    d.sold_price_usd,
    br.brand_retail_median_usd as retail_reference_usd,
    case
        when br.brand_retail_median_usd > 0
        then round((d.sold_price_usd / br.brand_retail_median_usd)::numeric, 3)
    end as spread_ratio,
    'brand_proxy' as spread_basis
from {{ ref('int_item_daily_resale') }} d
join {{ ref('stg_items') }} i using (item_id)
left join {{ ref('int_brand_retail_baseline') }} br on br.brand = i.brand
