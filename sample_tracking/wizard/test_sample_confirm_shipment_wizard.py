from odoo import models, fields
from odoo.exceptions import UserError
from odoo.tools.translate import _


# Popup form so logistics can record the courier tracking number when confirming a shipment.
class TestSampleConfirmShipmentWizard(models.TransientModel):
    _name = "test.sample.confirm.shipment.wizard"
    _description = "Confirm Shipment"

    line_id = fields.Many2one("test.sample.line", required=True, ondelete="cascade", help="The item being confirmed as shipped.")
    tracking_number = fields.Char(string="Tracking Number", help="Courier tracking number for this shipment. Optional but strongly recommended. It will be saved on the item and visible in the chatter.")

    def action_confirm(self):
        self.ensure_one()
        line = self.line_id
        if line.state != "pending_return" or line.return_method != "ship":
            raise UserError(_("Only items pending return via shipment can be confirmed here."))
        line.write({
            "state": "returned",
            "date_closed": fields.Date.today(),
            "tracking_number": self.tracking_number,
        })
        line.message_post(
            body=_("%s confirmed the item was handed to the courier.%s") % (
                self.env.user.name,
                _(" Tracking number: %s") % self.tracking_number if self.tracking_number else "",
            ),
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )
        return {"type": "ir.actions.act_window_close"}
