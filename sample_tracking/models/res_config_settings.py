import json
from odoo import models, fields, api


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    sample_tracking_logistics_email = fields.Char(
        string="Logistics Email",
        config_parameter="sample_tracking.logistics_email",
        help="Email address used as recipient for logistics notifications (pickup, return, cancellation).",
    )
    sample_tracking_auto_create_samples = fields.Boolean(
        string="Auto-Create Samples on Order Confirmation",
        config_parameter="sample_tracking.auto_create_samples",
        default=True,
        help="When enabled, confirming a sale order automatically creates a sample batch for that order.",
    )
    sample_tracking_sample_product_ids = fields.Many2many(
        "product.template",
        "sample_tracking_config_product_rel",
        "config_id",
        "product_id",
        string="Products That Create Samples",
        help="Only these products will trigger sample creation on order confirmation. Leave empty to include all products.",
    )
    sample_tracking_default_approver_id = fields.Many2one(
        "res.users",
        string="Default Scrap Approver",
        domain=lambda self: [("groups_id", "in", self.env.ref("sample_tracking.group_sample_manager").ids)],
        help="Pre-filled as the approver when users open a scrap request. Can be overridden per request.",
    )

    def get_values(self):
        res = super().get_values()
        param = self.env["ir.config_parameter"].sudo()
        approver_id = param.get_param("sample_tracking.default_approver_id")
        res["sample_tracking_default_approver_id"] = int(approver_id) if approver_id else False
        product_ids_str = param.get_param("sample_tracking.sample_product_ids", "")
        if product_ids_str:
            try:
                res["sample_tracking_sample_product_ids"] = [(6, 0, json.loads(product_ids_str))]
            except (json.JSONDecodeError, TypeError, ValueError):
                res["sample_tracking_sample_product_ids"] = [(5, 0, 0)]
        else:
            res["sample_tracking_sample_product_ids"] = [(5, 0, 0)]
        return res

    def set_values(self):
        super().set_values()
        param = self.env["ir.config_parameter"].sudo()
        approver = self.sample_tracking_default_approver_id
        param.set_param(
            "sample_tracking.default_approver_id",
            str(approver.id) if approver else "",
        )
        param.set_param(
            "sample_tracking.sample_product_ids",
            json.dumps(self.sample_tracking_sample_product_ids.ids) if self.sample_tracking_sample_product_ids else "",
        )
