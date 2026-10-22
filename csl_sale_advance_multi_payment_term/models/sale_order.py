from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

# Order fields that change TOP amounts or invoice data once a TOP is submitted.
TOP_LOCKED_ORDER_FIELDS = {
    "partner_id", "partner_invoice_id", "currency_id", "pricelist_id",
    "fiscal_position_id", "payment_term_id", "company_id",
}


class SaleOrder(models.Model):
    _inherit = "sale.order"

    top_ids = fields.One2many("sale.order.top", "order_id", string="TOP Schedule")
    top_percentage_total = fields.Float(
        string="TOP Percentage", compute="_compute_top_totals", digits=(16, 4),
    )
    top_amount_total = fields.Monetary(string="TOP Scheduled", compute="_compute_top_totals")
    top_invoiced_amount = fields.Monetary(string="TOP Invoiced", compute="_compute_top_totals")
    top_remaining_amount = fields.Monetary(string="TOP Remaining", compute="_compute_top_totals")

    @api.depends("amount_total", "top_ids.state", "top_ids.percentage", "top_ids.amount",
                 "top_ids.invoice_id.amount_total", "top_ids.invoice_id.state")
    def _compute_top_totals(self):
        for order in self:
            tops = order._get_active_tops()
            invoices = tops.invoice_id.filtered(lambda move: move.state != "cancel")
            order.top_percentage_total = sum(tops.mapped("percentage"))
            order.top_amount_total = sum(tops.mapped("amount"))
            order.top_invoiced_amount = sum(invoices.mapped("amount_total"))
            order.top_remaining_amount = order.amount_total - order.top_invoiced_amount

    def write(self, vals):
        if TOP_LOCKED_ORDER_FIELDS & set(vals):
            self._check_top_unlocked()
        return super().write(vals)

    def _create_invoices(self, grouped=False, final=False, date=None):
        self._check_no_top_schedule()
        return super()._create_invoices(grouped=grouped, final=final, date=date)

    def _get_active_tops(self):
        self.ensure_one()
        return self.top_ids.filtered(lambda top: top.state != "cancel").sorted(
            lambda top: (top.sequence, top._origin.id or float("inf"))
        )

    def _get_top_amounts(self):
        """Return ``{top: amount}``: each share of the total (taxes included) is rounded in
        the order currency and the last TOP absorbs the residual once the schedule is 100%."""
        self.ensure_one()
        tops = self._get_active_tops()
        currency = self.currency_id
        amounts = {top: currency.round(self.amount_total * top.percentage) for top in tops}
        if tops and not float_compare(sum(tops.mapped("percentage")), 1, precision_digits=4):
            amounts[tops[-1]] = self.amount_total - sum(amounts[top] for top in tops[:-1])
        return amounts

    def _get_top_invoiceable_lines(self):
        return self.order_line.filtered(
            lambda line: not line.display_type and not line.is_downpayment
            and line.product_id.type != "combo" and line.product_uom_qty
        )

    def _check_top_unlocked(self):
        locked = self.filtered(lambda order: any(
            top.state in ("submitted", "manager_approved", "invoiced") for top in order.top_ids
        ))
        if locked:
            raise UserError(_(
                "Sales order %s has submitted or invoiced TOPs. Reject or cancel them before "
                "changing the customer, currency, pricelist, taxes, payment terms or order lines.",
                ", ".join(locked.mapped("name")),
            ))

    def _check_no_top_schedule(self):
        orders = self.filtered(lambda order: order.top_ids.filtered(lambda t: t.state != "cancel"))
        if orders:
            raise UserError(_(
                "Sales order %s is invoiced based solely on its approved TOP (Terms of Payment) schedule.",
                ", ".join(orders.mapped("name")),
            ))

    def _check_top_schedule(self):
        for order in self:
            tops = order._get_active_tops()
            if order.state != "sale":
                raise UserError(_("Sales order %s must be confirmed to process its TOPs.", order.name))
            if not tops:
                raise UserError(_("Sales order %s needs at least one TOP.", order.name))
            total_percentage = sum(tops.mapped("percentage"))
            if float_compare(total_percentage, 1, precision_digits=4):
                raise UserError(_(
                    "The TOP percentages of %(order)s total %(total)s%%; they must total exactly 100%%.",
                    order=order.name, total=round(total_percentage * 100, 2),
                ))
            if order.currency_id.compare_amounts(order.amount_total, 0) <= 0:
                raise UserError(_("Sales order %s must have a positive total.", order.name))
            if order.order_line.filtered("is_downpayment"):
                raise UserError(_(
                    "Sales order %s has down payments, which cannot be combined with a TOP schedule.",
                    order.name,
                ))
            other_moves = order.invoice_ids.filtered(lambda move: move.state != "cancel") - tops.invoice_id
            if other_moves:
                raise UserError(_(
                    "Sales order %(order)s has invoices or credit notes not created from its TOP "
                    "schedule (%(moves)s).",
                    order=order.name, moves=", ".join(other_moves.mapped("display_name")),
                ))
            if tops.invoice_id.filtered(lambda move: move.state == "cancel"):
                raise UserError(_(
                    "A TOP invoice of %s was cancelled; the schedule cannot continue automatically.",
                    order.name,
                ))
            for top in tops:
                if top.estimated_invoice_date < order.date_order.date():
                    raise UserError(_(
                        "The estimated invoice date of TOP %s cannot be before the order date.",
                        top.name,
                    ))
            tops._check_partner_bank()
