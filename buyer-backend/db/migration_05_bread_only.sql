-- Demo scope: one product line. Keep the three Bread (5005) suppliers and
-- drop the other fifteen.
--
-- purchase_orders holds a RESTRICT foreign key to sellers, so anything those
-- suppliers already won has to go first. Everything else (contacts, logins,
-- invitations, quotes, negotiations) cascades on its own.

BEGIN;

CREATE TEMP TABLE doomed ON COMMIT DROP AS
SELECT s.seller_id
FROM sellers s
WHERE NOT EXISTS (
    SELECT 1 FROM seller_products sp
    WHERE sp.seller_id = s.seller_id AND sp.product_id = 5005
);

DELETE FROM payments
 WHERE po_id IN (SELECT po_id FROM purchase_orders
                  WHERE seller_id IN (SELECT seller_id FROM doomed));

DELETE FROM purchase_orders
 WHERE seller_id IN (SELECT seller_id FROM doomed);

DELETE FROM sellers
 WHERE seller_id IN (SELECT seller_id FROM doomed);

COMMIT;

-- what is left
SELECT s.seller_id, s.company_name, s.tier, su.email
FROM sellers s
LEFT JOIN seller_users su USING (seller_id)
ORDER BY s.seller_id;
