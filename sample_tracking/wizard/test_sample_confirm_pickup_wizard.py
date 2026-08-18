from odoo import models, fields, api
from odoo.exceptions import UserError
from odoo.tools.translate import _
from markupsafe import Markup, escape


# Popup form so logistics can capture an optional customer signature when confirming a pickup.
class TestSampleConfirmPickupWizard(models.TransientModel):
    _name = "test.sample.confirm.pickup.wizard"
    _description = "Confirm Customer Pickup"

    line_id = fields.Many2one("test.sample.line", required=True, ondelete="cascade", help="The item being collected by the customer.")
    product_name = fields.Char(string="Product / Item", readonly=True)
    customer_signature = fields.Binary(string="Customer Signature", help="Optional. Draw or capture the customer's signature as proof of collection.")
    customer_signature_name = fields.Char(default="signature.png")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        line_id = res.get("line_id") or self._context.get("default_line_id")
        if line_id:
            line = self.env["test.sample.line"].browse(line_id)
            res["product_name"] = line.product_name
        return res

    def action_confirm(self):
        self.ensure_one()
        line = self.line_id
        if line.state != "pending_return" or line.return_method != "pickup":
            raise UserError(_("Only items pending return via customer pickup can be confirmed here."))
        vals = {
            "state": "returned",
            "date_closed": fields.Date.today(),
        }
        if self.customer_signature:
            vals["customer_signature"] = self.customer_signature
            vals["customer_signature_name"] = self.customer_signature_name
        line.write(vals)
        line.message_post(
            body=Markup(_("<b>%(user)s</b> confirmed the customer collected the item.")) % {"user": escape(self.env.user.name)},
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )
        return {"type": "ir.actions.act_window_close"}
