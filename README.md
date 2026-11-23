# TapRadar Odoo Add-ons

TapRadar add-ons for Odoo.sh and the Odoo Apps Store. Each Odoo version has its own
branch, used as an add-ons folder (one folder per add-on at the root):

- `19.0`: Odoo 19 (this branch)
- `17.0`: Odoo 17

## tapradar_cashback: Cashback Gift Cards

During a campaign, every purchase at the Odoo Point of Sale earns the customer a
personal gift card for their next visit.

- **Value:** chosen by the Odoo administrator, either a percentage of the purchase
  (e.g. 5% or 10%) or a fixed amount (e.g. €5 for every purchase over €50).
- **Right after the purchase** the customer's gift card is created. Every further
  purchase during the campaign increases the same gift card; refunds reduce it.
- The gift card **cannot be used during the campaign**. If it is scanned at the
  till, the cashier sees "can only be used from …", and the server refuses it too.
- **Valid until** is the date the administrator enters. Validity always starts the
  day after the last campaign day.
- When the gift card is scanned at the till, Odoo deducts the balance (redeemed).

**Usage:** Point of Sale → Products → Cashback Campaigns

1. Under *Point of Sale → Products → Gift cards & eWallet*, create a program of type
   **Gift Card** for this campaign only. The add-on sets the program's dates itself.
2. New campaign: dates, percentage or fixed amount, minimum, excluded categories
   (e.g. deposits), gift card program, valid until → **Activate**.
3. **Preview** shows who gets how much.

Rules:
- Gross amount (taxes included), without products of the excluded categories.
- Only orders with a customer count (customer number = *Reference* on the contact).
- The POS loads the program list when it opens: reload the POS on the day after the
  campaign, otherwise the gift card shows as "invalid".
- An hourly job catches missed orders and closes finished campaigns. After a
  campaign has ended, existing gift cards are not touched.

The interface is in English; the German translation is in `i18n/de.po`.
Dependency: `pos_loyalty` (Odoo 19 Community + Enterprise). Does not work on Odoo Online.

## Local development

```bash
docker compose up -d          # http://localhost:8069
```

Tests:

```bash
docker compose run --rm odoo odoo -d cashback_test -i tapradar_cashback \
  --test-tags /tapradar_cashback --stop-after-init --without-demo
```

## TapRadar connection

- **Point of Sale → Configuration → Settings → TapRadar:** enter the integration key
  created in the TapRadar business account (Dashboard → "Kassensysteme" (till systems) → Odoo), agree
  to share the gift card data, then **Connect**.
- Gift cards are sent to TapRadar automatically when they are created or change
  (the hourly job catches up on anything missed). **Point of Sale → Products →
  TapRadar Sync** lists every update and lets you retry failed ones.
- **QR code on receipts** (`https://www.tapradar.app/l/odoo?...`): the customer scans
  it with the TapRadar app, which links the customer number to their account.
- **Code at the till:** the customer opens "Mein TapRadar-Code" (my TapRadar code) in the app, the
  cashier scans the code into the "TapRadar-Code" field on the customer and saves.
- The customer number is the *Reference* on the contact, or `odoo-<id>` when it is
  empty. No names, e-mail addresses or purchase data are sent to TapRadar.

### TapRadar card (TapRadar Platinum)

- The customer adds the shop under "Karten" (cards) in the TapRadar app. The card shows the
  card number plus a 6-digit code that changes every 30 seconds, so a photo or
  screenshot does not work at the till.
- **First scan:** the till asks which customer the card belongs to and links it.
  **Later scans** select that customer automatically once TapRadar has confirmed
  the live code.
- On the payment screen, a badge under **Invoice** shows whether the chosen customer
  is linked with TapRadar. It never shows a name or nickname.

### Loyalty points in the TapRadar app

- Setting **Loyalty points in the TapRadar app** (off by default). When it is on,
  linked customers see the points of the shop's own loyalty programs on their
  TapRadar card, with the next reward.
- Sent per loyalty card: customer number, program name, balance, point unit and up
  to 10 reward thresholds. No names. Turning the setting off, archiving the
  program, deleting the card or removing its customer hides the points in the app.
- Points are earned and spent only in Odoo; TapRadar only shows them.

### Spending points needs the live TapRadar card

- Customers with a TapRadar card can only spend loyalty points in an order where
  their live card was scanned. Otherwise the payment screen stops with a message.
- The scan gives the order a signed token for that customer (valid for 3 hours).
  Even if the till skips the check, the server does not deduct the points without
  it, keeps any points earned in the same order and notes it on the order.
- Customers without a TapRadar card are not affected.
