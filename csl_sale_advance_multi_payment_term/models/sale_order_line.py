from odoo import api, models

# Line fields that change the order total or the TOP invoice lines.
TOP_LOCKED_LINE_FIELDS = {
    "order_id", "product_id", "product_uom_qty", "product_uom", "price_unit", "discount", "tax_id",
}


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    @api.model_create_multi
    def create(self, vals_list):
        order_ids = {vals.get("order_id") for vals in vals_list if not vals.get("display_type")}
        self.env["sale.order"].browse(order_ids - {None, False})._check_top_unlocked()
        return super().create(vals_list)

    def write(self, vals):
        if TOP_LOCKED_LINE_FIELDS & set(vals):
            self.filtered(lambda line: not line.display_type).order_id._check_top_unlocked()
            if vals.get("order_id"):
                self.env["sale.order"].browse(vals["order_id"])._check_top_unlocked()
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_top_locked(self):
        self.filtered(lambda line: not line.display_type).order_id._check_top_unlocked()
