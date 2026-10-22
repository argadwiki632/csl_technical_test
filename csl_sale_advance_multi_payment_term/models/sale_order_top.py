from psycopg2.errors import LockNotAvailable

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_compare, float_round

EDITABLE_STATES = ("draft", "rejected")
# Only the workflow actions below may change these fields.
WORKFLOW_FIELDS = {"state", "invoice_id", "manager_approver_id", "submitted_amount"}
SCHEDULE_FIELDS = {
    "order_id", "sequence", "name", "note", "estimated_invoice_date",
    "payment_term_id", "partner_bank_id", "percentage",
}


class SaleOrderTop(models.Model):
    _name = "sale.order.top"
    _description = "Sales Order Term of Payment"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "order_id, sequence, id"
    _check_company_auto = True

    order_id = fields.Many2one(
        "sale.order", string="Sales Order", required=True, ondelete="cascade", index=True,
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(string="Description", required=True)
    note = fields.Text(string="Term Note")
    estimated_invoice_date = fields.Date(string="Invoice Date Est.", required=True)
    payment_term_id = fields.Many2one(
        "account.payment.term", check_company=True,
        help="Payment terms of the TOP invoice. Empty means the sales order payment terms.",
    )
    commercial_partner_id = fields.Many2one(related="order_id.partner_id.commercial_partner_id")
    partner_bank_id = fields.Many2one(
        "res.partner.bank", string="Bank Account",
        domain="[('partner_id', 'child_of', commercial_partner_id)]",
    )
    percentage = fields.Float(string="%", digits=(16, 4), required=True, help="Share of the order total (1.0 = 100%).")
    company_id = fields.Many2one(related="order_id.company_id", store=True, index=True)
    currency_id = fields.Many2one(related="order_id.currency_id")
    amount = fields.Monetary(
        compute="_compute_amount", store=True,
        help="Share of the sales order total (taxes included). "
             "The last TOP takes the rounding residual.",
    )
    submitted_amount = fields.Monetary(
        readonly=True, copy=False, help="Amount frozen when the TOP was submitted for approval.",
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("submitted", "Waiting Manager Approval"),
            ("manager_approved", "Waiting Admin Approval"),
            ("invoiced", "Invoiced"),
            ("rejected", "Rejected"),
            ("cancel", "Cancelled"),
        ],
        default="draft", required=True, readonly=True, copy=False, tracking=True,
    )
    manager_approver_id = fields.Many2one("res.users", readonly=True, copy=False)
    invoice_id = fields.Many2one("account.move", string="Invoice", readonly=True, copy=False)
    history_ids = fields.One2many("sale.order.top.history", "top_id", string="Approval History")

    _sql_constraints = [
        (
            "percentage_range",
            "CHECK(percentage > 0 AND percentage <= 1)",
            "A TOP percentage must be greater than 0% and at most 100%.",
        ),
    ]

    @api.depends("order_id", "percentage", "name")
    def _compute_display_name(self):
        for record in self:
            record.display_name = "%s - %s%% - %s" % (
                record.order_id.name or "",
                record.percentage * 100,
                record.name or "",
            )

    @api.depends(
        "percentage", "sequence", "state", "submitted_amount", "order_id.amount_total", "order_id.currency_id",
        "order_id.top_ids.percentage", "order_id.top_ids.sequence", "order_id.top_ids.state",
    )
    def _compute_amount(self):
        amounts = {}
        for order in self.order_id:
            amounts.update(order._get_top_amounts())
        for top in self:
            # Submitted/processed TOPs keep the amount they were submitted with.
            if top.state in EDITABLE_STATES:
                top.amount = amounts.get(top, 0.0)
            else:
                top.amount = top.submitted_amount

    @api.constrains("partner_bank_id", "order_id")
    def _check_partner_bank(self):
        for top in self:
            if top.partner_bank_id and \
                    top.partner_bank_id.partner_id.commercial_partner_id != top.commercial_partner_id:
                raise ValidationError(_(
                    "The beneficiary bank account of TOP %(top)s must belong to customer %(partner)s.",
                    top=top.name, partner=top.commercial_partner_id.display_name,
                ))

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(WORKFLOW_FIELDS & set(vals) for vals in vals_list):
            raise AccessError(_("TOP workflow fields can only be set by the approval actions."))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            if WORKFLOW_FIELDS & set(vals):
                raise AccessError(_("TOP workflow fields can only be set by the approval actions."))
            if SCHEDULE_FIELDS & set(vals) and any(top.state not in EDITABLE_STATES for top in self):
                raise UserError(_("Only draft or rejected TOPs can be modified."))
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_processed(self):
        if any(top.state != "draft" or top.invoice_id or top.history_ids for top in self):
            raise UserError(_(
                "Only draft TOPs that were never submitted can be deleted. Cancel the TOP instead."
            ))

    # ------------------------------------------------------------------
    # Workflow actions
    # ------------------------------------------------------------------

    def action_submit(self):
        self.check_access("write")
        if any(top.state not in EDITABLE_STATES for top in self):
            raise UserError(_("Only draft or rejected TOPs can be submitted."))
        self.order_id._check_top_schedule()
        for top in self:
            top.sudo().submitted_amount = top.amount
        self._set_workflow_state({"state": "submitted", "manager_approver_id": False}, "submit")
        return True

    def action_approve_manager(self):
        self._check_approver_group("csl_sale_advance_multi_payment_term.group_top_manager")
        for top in self:
            if top.state != "submitted":
                raise UserError(_("TOP %s is not waiting for manager approval.", top.name))
            top._check_still_valid()
        self._set_workflow_state(
            {"state": "manager_approved", "manager_approver_id": self.env.uid}, "approve_manager",
        )
        return True

    def action_approve_admin(self):
        self._check_approver_group("csl_sale_advance_multi_payment_term.group_top_admin")
        for top in self:
            top._lock_schedule()
            if top.state != "manager_approved" or top.invoice_id:
                raise UserError(_(
                    "TOP %s is not waiting for admin approval; it may already have been invoiced.",
                    top.name,
                ))
            if top.manager_approver_id == self.env.user:
                raise AccessError(_(
                    "You approved TOP %s as manager; another user must give the admin approval.",
                    top.name,
                ))
            top._check_still_valid(invoicing=True)
            invoice = top._create_invoice()
            top._set_workflow_state({"state": "invoiced", "invoice_id": invoice.id}, "approve_admin")
        return self.action_view_invoice() if len(self) == 1 else True

    def action_reject(self, reason):
        if not (reason or "").strip():
            raise UserError(_("A rejection reason is required."))
        for top in self:
            if top.state == "submitted":
                top._check_approver_group("csl_sale_advance_multi_payment_term.group_top_manager")
            elif top.state == "manager_approved":
                top._check_approver_group("csl_sale_advance_multi_payment_term.group_top_admin")
            else:
                raise UserError(_("TOP %s is not waiting for approval.", top.name))
        self._set_workflow_state({"state": "rejected", "manager_approver_id": False}, "reject", reason)
        return True

    def action_cancel(self):
        self.check_access("write")
        if any(top.invoice_id or top.state in ("invoiced", "cancel") for top in self):
            raise UserError(_("Invoiced or cancelled TOPs cannot be cancelled."))
        self._set_workflow_state({"state": "cancel"}, "cancel")
        return True

    def action_view_invoice(self):
        return self.invoice_id._get_records_action(name=_("TOP Invoices"))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _check_approver_group(self, group_xmlid):
        if not self.env.user.has_group(group_xmlid):
            group = self.env.ref(group_xmlid)
            raise AccessError(_("Only members of the group “%s” can perform this action.", group.name))

    def _set_workflow_state(self, vals, action, reason=False):
        """Write workflow fields and log the action; callers have done the role/state checks."""
        self.check_access("write")
        self.sudo().write(vals)
        self.env["sale.order.top.history"].sudo().create([{
            "top_id": top.id,
            "action": action,
            "user_id": self.env.uid,
            "reason": reason,
        } for top in self])
        if reason:
            for top in self:
                top.message_post(body=_("Rejected: %s", reason))

    def _lock_schedule(self):
        """Lock all TOP rows of the order so concurrent approvals are serialized.

        A concurrent transaction still running makes NOWAIT fail with a clear message; one
        that committed after our snapshot raises a serialization error which Odoo retries
        with fresh data, so the state checks below always see the latest values.
        """
        self.ensure_one()
        try:
            with self.env.cr.savepoint(flush=False):
                self.env.cr.execute(
                    "SELECT id FROM sale_order_top WHERE order_id = %s FOR UPDATE NOWAIT",
                    [self.order_id.id],
                )
        except LockNotAvailable:
            raise UserError(_(
                "This payment schedule is being processed by another user. Please try again."
            )) from None
        self.order_id.top_ids.invalidate_recordset(["state", "invoice_id"])

    def _check_still_valid(self, invoicing=False):
        """Revalidate the order and schedule at approval time."""
        self.ensure_one()
        order = self.order_id
        order._check_top_schedule()
        currency = order.currency_id
        expected = order._get_top_amounts().get(self, 0.0)
        if currency.compare_amounts(expected, self.amount):
            raise UserError(_(
                "The amount of TOP %(top)s changed from %(old)s to %(new)s since it was submitted. "
                "Reject it and submit it again.",
                top=self.name, old=self.amount, new=expected,
            ))
        if not invoicing:
            return
        tops = order._get_active_tops()
        if self == tops[-1:] and any(top.state != "invoiced" for top in tops[:-1]):
            raise UserError(_(
                "TOP %s carries the rounding residual of the schedule and must be invoiced last.",
                self.name,
            ))
        invoiced = sum(tops.invoice_id.mapped("amount_total"))
        if currency.compare_amounts(invoiced + self.amount, order.amount_total) > 0:
            raise UserError(_(
                "Invoicing TOP %s would exceed the sales order total.", self.name,
            ))

    def _get_invoice_line_commands(self):
        """Allocate the TOP share on every invoiceable order line.

        Quantities are rounded to the UoM precision so ``qty_invoiced`` stays meaningful; the
        unit price is scaled by ``exact / rounded`` quantity to keep the line value equal to the
        exact share. The last TOP invoices all remaining quantities.
        """
        self.ensure_one()
        order = self.order_id
        is_last = self == order._get_active_tops()[-1:]
        precision = self.env["decimal.precision"].precision_get("Product Unit of Measure")
        commands = []
        for line in order._get_top_invoiceable_lines():
            exact_qty = line.product_uom_qty * self.percentage
            if is_last:
                quantity = line.product_uom_qty - line.qty_invoiced
            else:
                quantity = float_round(exact_qty, precision_digits=precision)
            if float_compare(quantity, 0.0, precision_digits=precision) <= 0:
                raise UserError(_(
                    "The share of TOP %(top)s on line “%(line)s” has no quantity left to invoice. "
                    "Adjust the percentages or the unit of measure precision.",
                    top=self.name, line=line.name,
                ))
            commands.append(Command.create(line._prepare_invoice_line(
                quantity=quantity,
                price_unit=line.price_unit * exact_qty / quantity,
            )))
        return commands

    def _create_invoice(self):
        self.ensure_one()
        order = self.order_id
        invoice_vals = order._prepare_invoice()
        invoice_vals.update({
            "invoice_payment_term_id": (self.payment_term_id or order.payment_term_id).id,
            "sale_top_id": self.id,
            "invoice_line_ids": self._get_invoice_line_commands(),
        })
        invoice = self.env["account.move"].with_company(order.company_id).create(invoice_vals)
        if self == order._get_active_tops()[-1:]:
            other_invoices = order.top_ids.invoice_id - invoice
            target = order.amount_total - sum(other_invoices.mapped("amount_total"))
        else:
            target = self.amount
        self._adjust_invoice_rounding(invoice, target)
        invoice.message_post_with_source(
            "mail.message_origin_link",
            render_values={"self": invoice, "origin": order},
            subtype_xmlid="mail.mt_note",
        )
        return invoice

    def _adjust_invoice_rounding(self, invoice, target):
        """Book the remaining cents (tax/price rounding) on the tax line, like Odoo does for
        fixed down payments, so the invoice matches the scheduled amount exactly."""
        currency = invoice.currency_id
        delta = target - invoice.amount_total
        if currency.is_zero(delta):
            return
        tax_line = invoice.line_ids.filtered(lambda aml: aml.display_type == "tax")[:1]
        max_delta = currency.rounding * len(invoice.invoice_line_ids)
        if not tax_line or currency.compare_amounts(abs(delta), max_delta) > 0:
            raise UserError(_(
                "The invoice total %(total)s of TOP %(top)s cannot be reconciled with its "
                "scheduled amount %(amount)s.",
                total=invoice.amount_total, top=self.name, amount=target,
            ))
        invoice.line_ids = [Command.update(tax_line.id, {
            "amount_currency": tax_line.amount_currency + delta * invoice.direction_sign,
        })]
