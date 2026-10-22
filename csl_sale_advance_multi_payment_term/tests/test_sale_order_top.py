from datetime import timedelta
from unittest.mock import patch

from psycopg2 import IntegrityError

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import new_test_user, tagged
from odoo.tools import mute_logger

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestSaleOrderTop(AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        company = cls.company_data["company"]
        user_vals = {"company_id": company.id, "company_ids": [Command.set(company.ids)]}
        cls.salesman = new_test_user(
            cls.env, login="top_salesman", groups="sales_team.group_sale_salesman", **user_vals,
        )
        cls.manager = new_test_user(
            cls.env, login="top_manager",
            groups="sales_team.group_sale_salesman,csl_sale_advance_multi_payment_term.group_top_manager",
            **user_vals,
        )
        cls.admin = new_test_user(
            cls.env, login="top_admin",
            groups="sales_team.group_sale_salesman,csl_sale_advance_multi_payment_term.group_top_admin",
            **user_vals,
        )
        cls.manager_admin = new_test_user(
            cls.env, login="top_manager_admin",
            groups="sales_team.group_sale_salesman,csl_sale_advance_multi_payment_term.group_top_manager,"
                   "csl_sale_advance_multi_payment_term.group_top_admin",
            **user_vals,
        )
        cls.partner_bank = cls.env["res.partner.bank"].create({
            "acc_number": "TOP-123", "partner_id": cls.partner_a.id,
        })
        cls.today = fields.Date.context_today(cls.env.user)

    def _create_order(self, percentages, lines=None, confirm=True):
        lines = lines or [(self.product_a, 10.0, 1000.0)]
        order = self.env["sale.order"].create({
            "partner_id": self.partner_a.id,
            "user_id": self.salesman.id,
            "order_line": [
                Command.create({"product_id": product.id, "product_uom_qty": qty, "price_unit": price})
                for product, qty, price in lines
            ],
            "top_ids": [
                Command.create({
                    "name": f"TOP {index}",
                    "sequence": index,
                    "percentage": percentage / 100,
                    "estimated_invoice_date": self.today + timedelta(days=30 * index),
                    "partner_bank_id": self.partner_bank.id,
                })
                for index, percentage in enumerate(percentages, start=1)
            ],
        })
        if confirm:
            order.action_confirm()
        return order

    def _approve(self, top, manager=None, admin=None):
        top.with_user(self.salesman).action_submit()
        top.with_user(manager or self.manager).action_approve_manager()
        top.with_user(admin or self.admin).action_approve_admin()
        return top.invoice_id

    def test_confirm_does_not_invoice(self):
        order = self._create_order([30, 40, 30])
        self.assertFalse(order.invoice_ids)
        self.assertEqual(order.top_ids.mapped("state"), ["draft"] * 3)

    def test_main_workflow(self):
        order = self._create_order([30, 40, 30])
        self.assertEqual(order.amount_total, 11500.0)
        self.assertEqual(order.top_ids.mapped("amount"), [3450.0, 4600.0, 3450.0])
        self.assertEqual(order.top_amount_total, order.amount_total)

        top = order.top_ids[0]
        top.with_user(self.salesman).action_submit()
        self.assertEqual(top.state, "submitted")
        top.with_user(self.manager).action_approve_manager()
        self.assertEqual(top.state, "manager_approved")
        self.assertFalse(order.invoice_ids)
        top.with_user(self.admin).action_approve_admin()

        invoice = top.invoice_id
        self.assertEqual(top.state, "invoiced")
        self.assertEqual(invoice.state, "draft")
        self.assertEqual(invoice.sale_top_id, top)
        self.assertEqual(invoice.partner_id, order.partner_invoice_id)
        self.assertEqual(invoice.currency_id, order.currency_id)
        self.assertEqual(invoice.top_partner_bank_id, self.partner_bank)
        self.assertEqual(invoice.amount_total, 3450.0)
        self.assertEqual(invoice.invoice_line_ids.sale_line_ids, order.order_line)
        self.assertEqual(invoice.invoice_line_ids.tax_ids, order.order_line.tax_id)
        self.assertEqual(invoice.invoice_line_ids.quantity, 3.0)
        self.assertEqual(order.order_line.qty_invoiced, 3.0)
        self.assertEqual(order.top_invoiced_amount, 3450.0)
        self.assertEqual(order.top_remaining_amount, 8050.0)
        self.assertEqual(
            top.history_ids.sorted("id").mapped("action"),
            ["submit", "approve_manager", "approve_admin"],
        )
        self.assertEqual(
            top.history_ids.sorted("id").user_id, self.salesman | self.manager | self.admin,
        )

        for top in order.top_ids[1:]:
            self._approve(top)
        self.assertEqual(sum(order.top_ids.invoice_id.mapped("amount_total")), order.amount_total)
        self.assertEqual(order.invoice_status, "invoiced")
        self.assertEqual(order.top_remaining_amount, 0.0)

    def test_rounding_residual(self):
        order = self._create_order([33.33, 33.33, 33.34], lines=[
            (self.product_a, 1.0, 100.0),
            (self.product_b, 3.0, 7.77),
        ])
        total = order.amount_total
        amounts = order.top_ids.mapped("amount")
        self.assertEqual(amounts[0], order.currency_id.round(total * 0.3333))
        self.assertAlmostEqual(order.top_percentage_total, 1.0)
        self.assertAlmostEqual(sum(amounts), total, places=2)

        invoices = self.env["account.move"]
        for top in order.top_ids:
            invoices |= self._approve(top)
            self.assertAlmostEqual(top.invoice_id.amount_total, top.amount, places=2)
        self.assertAlmostEqual(sum(invoices.mapped("amount_total")), total, places=2)
        for line in order.order_line:
            self.assertAlmostEqual(line.qty_invoiced, line.product_uom_qty)
        self.assertEqual(order.invoice_status, "invoiced")

    def test_schedule_validation(self):
        order = self._create_order([30, 60])
        with self.assertRaisesRegex(UserError, "100%"):
            order.top_ids[0].with_user(self.salesman).action_submit()

        for percentage in (0.0, -0.1, 1.5):
            with mute_logger("odoo.sql_db"), self.assertRaises(IntegrityError):
                order.top_ids[0].percentage = percentage
                order.top_ids[0].flush_recordset()

        order.top_ids[1].percentage = 0.7
        order.top_ids[1].estimated_invoice_date = self.today - timedelta(days=10)
        with self.assertRaisesRegex(UserError, "before the order date"):
            order.top_ids[0].with_user(self.salesman).action_submit()

        quotation = self._create_order([100], confirm=False)
        with self.assertRaisesRegex(UserError, "confirmed"):
            quotation.top_ids.with_user(self.salesman).action_submit()

    def test_authorization(self):
        order = self._create_order([50, 50])
        top = order.top_ids[0]
        with self.assertRaises(AccessError):
            top.with_user(self.salesman).write({"state": "invoiced"})

        top.with_user(self.salesman).action_submit()
        with self.assertRaises(AccessError):
            top.with_user(self.salesman).action_approve_manager()
        with self.assertRaises(AccessError):
            top.with_user(self.admin).action_approve_manager()
        with self.assertRaisesRegex(UserError, "admin approval"):
            top.with_user(self.manager_admin).action_approve_admin()

        top.with_user(self.manager_admin).action_approve_manager()
        with self.assertRaises(AccessError):
            top.with_user(self.manager).action_approve_admin()
        with self.assertRaisesRegex(AccessError, "another user"):
            top.with_user(self.manager_admin).action_approve_admin()
        self.assertFalse(top.invoice_id)
        top.with_user(self.admin).action_approve_admin()
        self.assertEqual(top.state, "invoiced")

    def test_idempotent_admin_approval(self):
        order = self._create_order([50, 50])
        top = order.top_ids[0]
        self._approve(top)
        with self.assertRaisesRegex(UserError, "not waiting for admin approval"):
            top.with_user(self.admin).action_approve_admin()
        self.assertEqual(len(order.invoice_ids), 1)

    def test_reject_and_resubmit(self):
        order = self._create_order([50, 50])
        top = order.top_ids[0]
        top.with_user(self.salesman).action_submit()
        with self.assertRaisesRegex(UserError, "reason"):
            top.with_user(self.manager).action_reject(" ")
        with self.assertRaises(AccessError):
            top.with_user(self.salesman).action_reject("No")

        wizard = self.env["sale.order.top.reject"].with_user(self.manager).create({
            "top_id": top.id, "reason": "Wrong bank account",
        })
        wizard.action_reject()
        self.assertEqual(top.state, "rejected")
        reject_log = top.history_ids.filtered(lambda h: h.action == "reject")
        self.assertEqual(reject_log.reason, "Wrong bank account")
        self.assertEqual(reject_log.user_id, self.manager)

        top.with_user(self.salesman).partner_bank_id = False
        self._approve(top)
        self.assertEqual(top.state, "invoiced")
        self.assertEqual(len(top.history_ids), 5)
        with self.assertRaises(AccessError):
            top.history_ids[0].with_user(self.manager).write({"reason": "edited"})

    def test_change_control(self):
        order = self._create_order([50, 50])
        line = order.order_line
        line.product_uom_qty = 20.0
        self.assertEqual(order.top_ids.mapped("amount"), [11500.0, 11500.0])

        order.top_ids[0].with_user(self.salesman).action_submit()
        with self.assertRaisesRegex(UserError, "submitted or invoiced TOPs"):
            line.price_unit = 500.0
        with self.assertRaisesRegex(UserError, "submitted or invoiced TOPs"):
            order.write({"order_line": [Command.create({"product_id": self.product_b.id})]})
        with self.assertRaisesRegex(UserError, "submitted or invoiced TOPs"):
            order.partner_id = self.partner_b
        with self.assertRaisesRegex(UserError, "Only draft or rejected"):
            order.top_ids[0].with_user(self.salesman).percentage = 0.6

        # Draft sibling edited after submission: revalidated at approval time.
        order.top_ids[1].percentage = 0.4
        with self.assertRaisesRegex(UserError, "100%"):
            order.top_ids[0].with_user(self.manager).action_approve_manager()

    def test_invoice_failure_keeps_state(self):
        order = self._create_order([50, 50])
        top = order.top_ids[0]
        top.with_user(self.salesman).action_submit()
        top.with_user(self.manager).action_approve_manager()
        with patch.object(type(self.env["sale.order"]), "_prepare_invoice", side_effect=UserError("boom")):
            with self.assertRaisesRegex(UserError, "boom"):
                top.with_user(self.admin).action_approve_admin()
        self.assertEqual(top.state, "manager_approved")
        self.assertFalse(top.invoice_id)
        self.assertFalse(order.invoice_ids)
        self.assertFalse(top.history_ids.filtered(lambda h: h.action == "approve_admin"))

    def test_last_top_invoiced_last(self):
        order = self._create_order([30, 40, 30])
        with self.assertRaisesRegex(UserError, "invoiced last"):
            self._approve(order.top_ids[2])

    def test_cancel_and_delete_rules(self):
        order = self._create_order([50, 50])
        top = order.top_ids[0]
        invoice = self._approve(top)
        with self.assertRaises(UserError):
            top.with_user(self.salesman).action_cancel()
        with self.assertRaises(UserError):
            top.unlink()
        with self.assertRaises(UserError):
            invoice.unlink()

        other = order.top_ids[1]
        other.with_user(self.salesman).action_submit()
        other.with_user(self.salesman).action_cancel()
        self.assertEqual(other.state, "cancel")
        self.assertEqual(other.history_ids.mapped("action"), ["cancel", "submit"])
        with self.assertRaises(UserError):
            other.unlink()

    def test_reset_invoice_to_draft(self):
        order = self._create_order([100])
        invoice = self._approve(order.top_ids)
        invoice.action_post()
        regular_invoice = invoice.copy()
        self.assertFalse(regular_invoice.sale_top_id)
        regular_invoice.action_post()

        with self.assertRaisesRegex(UserError, "cannot be reset to draft"):
            (regular_invoice | invoice).button_draft()
        self.assertEqual(invoice.state, "posted")
        self.assertEqual(regular_invoice.state, "posted")

        regular_invoice.button_draft()
        self.assertEqual(regular_invoice.state, "draft")

    def test_standard_invoicing_blocked(self):
        order = self._create_order([100])
        wizard = self.env["sale.advance.payment.inv"].with_context(
            active_model="sale.order", active_ids=order.ids,
        ).create({"advance_payment_method": "percentage", "amount": 10})
        with self.assertRaisesRegex(UserError, r"approved TOP \(Terms of Payment\) schedule"):
            wizard.create_invoices()
        with self.assertRaisesRegex(UserError, r"approved TOP \(Terms of Payment\) schedule"):
            order._create_invoices()

    def test_cancelled_order_not_invoiced(self):
        order = self._create_order([100])
        top = order.top_ids
        top.with_user(self.salesman).action_submit()
        top.with_user(self.manager).action_approve_manager()
        order._action_cancel()
        with self.assertRaisesRegex(UserError, "confirmed"):
            top.with_user(self.admin).action_approve_admin()
        self.assertFalse(order.invoice_ids)
