from odoo import fields, models


class SaleOrderTopHistory(models.Model):
    """Append-only audit trail of TOP workflow actions.

    Users only get read access; rows are written by the workflow methods of
    ``sale.order.top`` after their own access and role checks.
    """

    _name = "sale.order.top.history"
    _description = "Sales Order TOP Approval History"
    _order = "date desc, id desc"

    top_id = fields.Many2one("sale.order.top", required=True, ondelete="cascade", index=True)
    order_id = fields.Many2one(related="top_id.order_id", store=True)
    company_id = fields.Many2one(related="top_id.company_id", store=True)
    action = fields.Selection(
        [
            ("submit", "Submitted"),
            ("approve_manager", "Manager Approved"),
            ("approve_admin", "Admin Approved"),
            ("reject", "Rejected"),
            ("cancel", "Cancelled"),
        ],
        required=True,
    )
    user_id = fields.Many2one("res.users", string="User", required=True)
    date = fields.Datetime(required=True, default=fields.Datetime.now)
    reason = fields.Text()
