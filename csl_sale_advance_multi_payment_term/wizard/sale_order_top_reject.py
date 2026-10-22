from odoo import fields, models


class SaleOrderTopReject(models.TransientModel):
    _name = "sale.order.top.reject"
    _description = "Reject Sales Order TOP"

    top_id = fields.Many2one("sale.order.top", required=True, ondelete="cascade")
    reason = fields.Text(required=True)

    def action_reject(self):
        self.ensure_one()
        self.top_id.action_reject(self.reason)
        return {"type": "ir.actions.act_window_close"}
