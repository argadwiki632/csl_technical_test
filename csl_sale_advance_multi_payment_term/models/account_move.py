from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    sale_top_id = fields.Many2one(
        "sale.order.top", string="Sales Order TOP", readonly=True, copy=False, index="btree_not_null",
    )
    top_partner_bank_id = fields.Many2one(
        related="sale_top_id.partner_bank_id", string="Cust. Bank Account TOP",
    )

    @api.ondelete(at_uninstall=False)
    def _unlink_except_sale_top(self):
        if self.sale_top_id:
            raise UserError(_("Invoices created from a Sales Order TOP schedule cannot be deleted."))

    def button_draft(self):
        if self.sale_top_id:
            raise UserError(_("Invoices created from a Sales Order TOP schedule cannot be reset to draft."))
        return super().button_draft()
