
CSL Sale Advance Multi Payment Term
===================================

Invoice a confirmed sales order in approved Terms of Payment (TOP). Each TOP is a
percentage of the order total; it is submitted, approved by a manager, approved by an
admin, and only then a **draft** customer invoice is created for its share.

Setup
=====

* Technical module name: ``csl_sale_advance_multi_payment_term``.
* Install: ``odoo-bin -d <db> -i csl_sale_advance_multi_payment_term``
* Grant users *Sales / TOP Manager Approver* and *Sales / TOP Admin Approver*
  (Settings › Users). Admin approvers also get *Invoicing / Billing* to create invoices.

Usage
=====

1. On a sales order, open the **TOP Schedule** tab and add the TOPs (description, term
   note, estimated invoice date, payment terms, customer bank account, percentage).
2. Confirm the order (no invoice is created), then **Submit** each TOP.
3. Managers approve or reject (*Sales › To Invoice › TOP Approvals*), then admins approve
   or reject. The admin approval creates and opens the draft invoice.

Run the tests
=============

::

    createdb top_test
    odoo-bin -c /dev/null --addons-path=addons,<path>/custom/odoo_apps -d top_test \
        -i csl_sale_advance_multi_payment_term --without-demo=all --test-enable \
        --test-tags=/csl_sale_advance_multi_payment_term --stop-after-init

Technical note
==============

Data model
----------

* ``sale.order.top`` (One2many ``sale.order.top_ids``): sequence, description, term
  note, estimated invoice date, payment terms, beneficiary bank account, percentage,
  computed ``amount``, ``submitted_amount`` snapshot, state, manager approver, invoice,
  approval history, chatter. Customer, invoice address and currency are *not* copied:
  they are read from the order. The bank account must belong to the order's commercial
  customer (domain + constraint, rechecked at submit/approval).
* ``sale.order.top.history``: append-only audit log (action, user, date, reason). Users
  only have read access; rows are written by the workflow methods.
* ``account.move.sale_top_id`` links the invoice to its TOP; invoice lines keep
  ``sale_line_ids`` so the standard sale ↔ invoice relations (``invoice_ids``,
  ``qty_invoiced``, ``invoice_status``) keep working. Such invoices cannot be deleted
  or reset to draft. Other invoices retain the standard Odoo behavior.
* The order shows the TOP percentage total, scheduled, invoiced and remaining amounts.

State transitions
-----------------

::

    draft ─submit─▶ submitted ─manager approve─▶ manager_approved ─admin approve─▶ invoiced
      ▲                │  reject (reason)              │  reject (reason)
      └── rejected ◀───┴───────────────────────────────┘
    draft / rejected / submitted / manager_approved ─cancel─▶ cancel (no invoice only)

* Every transition is a server-side method checking state, group and record access;
  ``state``, ``invoice_id``, ``manager_approver_id`` and ``submitted_amount`` cannot be
  written directly by users (``AccessError``).
* Submit validates the whole schedule: confirmed order, at least one active TOP, sum of
  percentages = 100 % (2 decimals), percentages in ]0 %, 100 %] (SQL constraint), estimated
  dates not before the order date, positive order total, no down payments, no other
  invoices/credit notes on the order.
* Approval stages revalidate the schedule and compare the frozen amount with a fresh
  computation (detects changed order values). Admin approval additionally checks
  over-invoicing and that the residual (last) TOP is invoiced last.
* The manager who approved a TOP cannot give its admin approval (no exception).
* Invoiced TOPs cannot be cancelled, reset or deleted; only never-submitted drafts can be
  deleted, other TOPs are cancelled to preserve the trail.

Change control
--------------

Once any TOP of an order is submitted or invoiced, ``sale.order.write`` blocks changes
to customer, invoice address, currency, pricelist, fiscal position, payment terms and
company, and ``sale.order.line`` blocks create/unlink and changes to product, quantity,
UoM, price, discount and taxes. Submitted TOPs are read-only (``write`` override). While
all TOPs are draft/rejected, amounts are recomputed automatically from the order.
Standard invoicing (*Create Invoice* wizard, down payments, ``_create_invoices``) is
refused for orders with an active TOP schedule.

Amounts, allocation and rounding
--------------------------------

* ``percentage`` is stored as a ratio (``0.3`` = 30 %, standard ``percentage`` widget).
* TOP amount = order total (taxes included) × percentage, rounded with the order
  currency. The last TOP by sequence gets ``total − sum(other TOPs)`` so the schedule
  reconciles exactly.
* Invoice lines are built with ``sale.order.line._prepare_invoice_line`` (product,
  description, UoM, discount, taxes, link to the order line). For every
  invoiceable line (no section/note, down payment or combo parent):

  - quantity = ordered quantity × percentage, rounded to the *Product Unit of Measure*
    precision; the last TOP takes the remaining ``product_uom_qty − qty_invoiced`` so the
    order ends fully invoiced;
  - unit price = order price × exact quantity / rounded quantity, so quantity rounding
    does not change the line value (the unit price on the invoice may then differ
    slightly from the order price).

* Remaining cents (tax rounding, unit-price precision) are booked on the invoice tax line,
  the same technique Odoo uses for fixed down payments, so each invoice equals its TOP
  amount and the last one equals ``order total − previous TOP invoices``. A difference
  larger than one currency unit of rounding per invoice line is refused instead of being
  hidden.

Concurrency and failures
------------------------

Admin approval locks all TOP rows of the order (``SELECT … FOR UPDATE NOWAIT``) and then
re-reads the state: a running concurrent approval gets a clear "try again" message, a
committed one triggers Odoo's serialization retry and the retried request sees the TOP as
invoiced. Invoice creation and state change happen in the same transaction, so a failure
leaves the TOP in *Waiting Admin Approval* without an invoice.

Security
--------

* Salesmen: CRUD on their own orders' TOPs (record rules mirror ``sale.order``), read the
  history. *All Documents* salesmen, approvers and billing users see all TOPs. Multi-company
  rules on TOP and history.
* *TOP Manager Approver* implies *Sales / All Documents*; *TOP Admin Approver* implies
  *Sales / All Documents* and *Invoicing / Billing*, so invoices are created with the
  approver's own rights. ``sudo`` is only used to write workflow fields and history rows
  after the explicit checks.

Limitations / trade-offs
------------------------

* Orders with down payments, other invoices or credit notes cannot use a TOP schedule;
  a cancelled TOP invoice or a refund stops the schedule (handle manually).
* TOP invoicing ignores the product invoicing policy (ordered quantities are invoiced).
* The beneficiary bank account is the customer's account, kept on the TOP and shown on the
  invoice (*TOP Customer Bank Account*). The invoice ``partner_bank_id`` (recipient bank of a
  customer invoice) stays the company's account.
* Early-payment-discount payment terms with *mixed* tax computation are not specifically
  handled.
