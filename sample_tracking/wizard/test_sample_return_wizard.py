import base64
from odoo import models, fields, api
from odoo.exceptions import UserError
from odoo.tools.translate import _
from markupsafe import Markup


# Popup form to record how a tested sample item is returned to the customer:
# either the customer collects it (pickup) or it is shipped out via a courier.
# Allows specifying courier account number and moving assistance details (location, pallet status, notes).
# When shipping, the user picks a courier and confirms the delivery address,
# a PDF shipping document is generated and saved, and logistics are notified with moving details.
class TestSampleReturnWizard(models.TransientModel):
    _name = "test.sample.return.wizard"
    _description = "Return Sample to Customer"

    line_id = fields.Many2one(
        "test.sample.line",
        required=True,
        ondelete="cascade",
        help="The item being returned.",
    )
    product_name = fields.Char(string="Product / Item", readonly=True, help="Name of the item, shown for reference.")
    return_method = fields.Selection(
        [("pickup", "Customer Pickup"), ("ship", "Ship to Customer")],
        string="Return Method",
        required=True,
        help="How the item will be returned. Pickup means the customer collects it in person. Ship sends it via a courier and generates a shipping document.",
    )

    # Shipping fields, only used when return_method is "ship"
    courier_id = fields.Many2one("test.sample.courier", string="Courier", help="Courier to use for shipping. Optional but recommended for shipped returns.")
    sale_order_id = fields.Many2one(
        "sale.order",
        string="Sale Order",
        related="line_id.sample_id.sale_order_id",
        readonly=True,
    )
    ship_name = fields.Char(string="Recipient Name", help="Full name of the person or company the package should be addressed to.")
    ship_street = fields.Char(string="Street", help="First line of the delivery address.")
    ship_street2 = fields.Char(string="Street 2", help="Second line of the delivery address, for example apartment or floor number.")
    ship_city = fields.Char(string="City", help="City for the delivery address.")
    ship_zip = fields.Char(string="ZIP / Postal Code", help="Postal or ZIP code for the delivery address.")
    ship_state_id = fields.Many2one(
        "res.country.state",
        string="State / Province",
        domain="[('country_id', '=', ship_country_id)]",
        help="State or province for the delivery address.",
    )
    ship_country_id = fields.Many2one("res.country", string="Country", help="Country for the delivery address.")
    
    courier_account_number = fields.Char(string="Customer Number at Courier", help="Customer's own account number with the courier, if they have one. Included in the logistics notification email.")
    requires_help_moving = fields.Boolean(string="Requires Help Moving", help="Tick if logistics needs to physically move this item and may need extra help.")
    moving_location_id = fields.Many2one("test.sample.location", string="Current Location", help="Where the item currently is. Pre-filled from the item's current location.")
    moving_notes = fields.Text(string="Moving Notes", help="Free-text instructions for logistics about moving this item (e.g. size, fragility, access constraints).")
    on_pallet = fields.Selection([("yes", "Yes"), ("no", "No")], string="On a Pallet?", help="Whether the item is on a pallet.")

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        line_id = res.get("line_id") or self._context.get("default_line_id")
        if line_id:
            line = self.env["test.sample.line"].browse(line_id)
            if line.current_location_id:
                res["moving_location_id"] = line.current_location_id.id
        return res

    @api.onchange("return_method")
    def _onchange_return_method(self):
        # When the user picks "Ship to Customer", auto-fill the address from the customer record
        if self.return_method == "ship" and self.line_id:
            partner = self.line_id.sample_id.customer_id
            if partner:
                self.ship_name = partner.name
                self.ship_street = partner.street
                self.ship_street2 = partner.street2
                self.ship_city = partner.city
                self.ship_zip = partner.zip
                self.ship_state_id = partner.state_id
                self.ship_country_id = partner.country_id

    def action_confirm(self):
        # Store the return details on the item and move it directly to Pending Return.
        # Logistics are notified immediately. For shipments a shipping PDF is also generated.
        self.ensure_one()
        line = self.line_id
        if line.state != "tested":
            raise UserError(_("Only tested items can be returned to the customer."))
        is_manager = self.env.user.has_group("sample_tracking.group_sample_manager")
        if line.responsible_id and line.responsible_id != self.env.user and not is_manager:
            raise UserError(
                _("Only the responsible user (%s) can return this item.") % line.responsible_id.name
            )
        if self.return_method == "ship":
            self._post_shipping_document(line)
        
        # Build moving_info dict if help is needed
        moving_info = None
        if self.requires_help_moving:
            moving_info = {
                "location": self.moving_location_id.display_name if self.moving_location_id else "",
                "notes": self.moving_notes or "",
                "on_pallet": dict(self._fields["on_pallet"].selection).get(self.on_pallet, "") if self.on_pallet else "",
            }
        
        line.write({
            "return_method": self.return_method,
            "return_courier_id": self.courier_id.id if self.courier_id else False,
            "customer_courier_account": self.courier_account_number or "",
            "state": "pending_return",
            "responsible_id": False,  # Clear so logistics team can claim the return
        })
        if self.return_method == "ship":
            line._send_logistics_notification(courier_account=self.courier_account_number, moving_info=moving_info)
        elif self.return_method == "pickup":
            line._send_pickup_notification(moving_info=moving_info)
        return {"type": "ir.actions.act_window_close"}

    def _post_shipping_document(self, line):
        # Render the QWeb report to PDF, attach it to the item (line) record,
        # and post a chatter message on the item so it shows up in its log notes.
        report = self.env.ref("sample_tracking.action_report_shipping_label")
        pdf_content, _mime = report._render_qweb_pdf(
            report_ref="sample_tracking.action_report_shipping_label",
            res_ids=[self.id],
        )
        attachment = self.env["ir.attachment"].create({
            "name": _("Shipping Document - %s.pdf") % line.product_name,
            "type": "binary",
            "datas": base64.b64encode(pdf_content),
            "res_model": "test.sample.line",
            "res_id": line.id,
            "mimetype": "application/pdf",
        })
        # Build a compact address string for the chatter message body
        address_parts = [p for p in [
            self.ship_name,
            self.ship_street,
            self.ship_street2,
            self.ship_city,
            " ".join(filter(None, [self.ship_zip, self.ship_state_id.name if self.ship_state_id else ""])),
            self.ship_country_id.name if self.ship_country_id else "",
        ] if p and p.strip()]
        line.message_post(
            body=Markup(_(
                "<b>Ship to customer</b>%(courier_part)s.<br/>"
                "Delivery address: %(address)s<br/>"
                "See attached shipping document."
            )) % {
                "courier_part": Markup(" via <em>%s</em>") % self.courier_id.name if self.courier_id else "",
                "address": ", ".join(address_parts),
            },
            attachment_ids=[attachment.id],
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )

