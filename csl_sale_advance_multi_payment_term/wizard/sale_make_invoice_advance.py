from odoo import models


class SaleAdvancePaymentInv(models.TransientModel):
    _inherit = "sale.advance.payment.inv"

    def _create_invoices(self, sale_orders):
        sale_orders._check_no_top_schedule()
        return super()._create_invoices(sale_orders)
