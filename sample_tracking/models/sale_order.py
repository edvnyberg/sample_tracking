import json
from odoo import models, fields, api


# Extends the standard sale order to automatically create a test sample batch
# when the order is confirmed. Each ordered product becomes one sample item to track.
class SaleOrder(models.Model):
    _inherit = "sale.order"

    sample_ids = fields.One2many("test.sample", "sale_order_id", string="Test Samples")
    sample_count = fields.Integer(compute="_compute_sample_count")

    @api.depends("sample_ids")
    def _compute_sample_count(self):
        # Powers the "Samples" smart button counter on the sale order form
        for order in self:
            order.sample_count = len(order.sample_ids)

    def action_confirm(self):
        # Run the standard order confirmation, then create the sample batch from the order lines
        result = super().action_confirm()
        auto_create = self.env["ir.config_parameter"].sudo().get_param(
            "sample_tracking.auto_create_samples", "True"
        )
        if auto_create != "False":
            self._create_test_samples()
        return result

    def _create_test_samples(self):
        # Create one sample batch per order with one line per ordered product
        allowed_ids_str = self.env["ir.config_parameter"].sudo().get_param(
            "sample_tracking.sample_product_ids", ""
        )
        try:
            allowed_tmpl_ids = set(json.loads(allowed_ids_str)) if allowed_ids_str else set()
        except (ValueError, TypeError):
            allowed_tmpl_ids = set()
        for order in self:
            if order.sample_ids:
                continue  # Samples already exist, avoid duplicates on re-confirm
            lines = [
                {
                    "product_name": sol.product_id.name,
                    "quantity": sol.product_uom_qty,
                    "ordered_quantity": sol.product_uom_qty,
                    "description": sol.name,
                    "sale_order_line_id": sol.id,
                }
                for sol in order.order_line
                if sol.product_id
                and (not allowed_tmpl_ids or sol.product_id.product_tmpl_id.id in allowed_tmpl_ids)
            ]
            if not lines:
                continue
            self.env["test.sample"].create({
                "customer_id": order.partner_id.id,
                "sale_order_id": order.id,
                "line_ids": [(0, 0, line) for line in lines],
            })

    def action_view_samples(self):
        # Opens the list of sample batches linked to this order (triggered by the smart button)
        return {
            "name": "Test Samples",
            "type": "ir.actions.act_window",
            "res_model": "test.sample",
            "view_mode": "list,form",
            "domain": [("sale_order_id", "=", self.id)],
            "context": {
                "default_sale_order_id": self.id,
                "default_customer_id": self.partner_id.id,
            },
        }
