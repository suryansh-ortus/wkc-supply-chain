-- Clean slate for the demo.
--
-- Two problems on screen right now:
--   * the same product appears several times, one card per agent run
--   * products whose suppliers were deleted still show, with "0 sellers"
--
-- From here on the engines supersede their own older cards automatically.
-- This clears what is already there.

BEGIN;

-- anything for a product that has no suppliers left
UPDATE analysis_results r SET status = 'superseded'
 WHERE r.status = 'pending_approval'
   AND NOT EXISTS (SELECT 1 FROM seller_products sp
                    WHERE sp.product_id = r.product_id);

-- and, per product, everything except the newest card
UPDATE analysis_results r SET status = 'superseded'
 WHERE r.status = 'pending_approval'
   AND r.result_id <> (
       SELECT x.result_id FROM analysis_results x
        WHERE x.product_id = r.product_id
          AND x.status = 'pending_approval'
        ORDER BY x.created_at DESC
        LIMIT 1);

COMMIT;

-- what the console will show now
SELECT p.name, r.recommended_quantity, r.hold_days, r.created_at
FROM analysis_results r JOIN products p USING (product_id)
WHERE r.status = 'pending_approval'
ORDER BY r.created_at DESC;
