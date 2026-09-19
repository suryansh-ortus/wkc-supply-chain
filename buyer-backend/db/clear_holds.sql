-- Release every hold. Safe to run as often as you like.
--
--   python db/apply_migration.py db/clear_holds.sql

UPDATE product_holds
   SET released_at = now()
 WHERE released_at IS NULL;

SELECT count(*) AS holds_still_active
  FROM product_holds
 WHERE released_at IS NULL;
