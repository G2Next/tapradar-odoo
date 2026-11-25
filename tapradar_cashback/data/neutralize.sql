-- Copies of the database (Odoo.sh staging, restored backups) must not talk to
-- TapRadar with the production integration key: test sales would reach real
-- customers' apps, and the copy would send the same customer numbers again.
-- Disconnect every company; connect again with a test key when needed.
UPDATE res_company
   SET tapradar_api_key = NULL,
       tapradar_link_secret = NULL,
       tapradar_integration_id = NULL,
       tapradar_shop_name = NULL,
       tapradar_link_base = NULL;

-- Nothing queued in production is sent from the copy.
UPDATE tapradar_cashback_event
   SET state = 'failed',
       last_error = 'Database neutralized'
 WHERE state = 'pending';
