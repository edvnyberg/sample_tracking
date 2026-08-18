import base64
from odoo import models, fields, api
from odoo.exceptions import UserError
from odoo.tools.translate import _
from markupsafe import Markup, escape


# Popup form to submit a return request for multiple tested items at once.
# Works identically to the single-item return wizard but operates on a set of lines.
# Allows specifying courier account number and moving assistance details (location, pallet status, notes).
# Each item is moved to Pending Return and logistics are notified with moving details.
class TestSampleBulkReturnWizard(models.TransientModel):
    _name = "test.sample.bulk.return.wizard"
    _description = "Bulk Return Items to Customer"

    sample_id = fields.Many2one(
        "test.sample",
        string="Sample",
        readonly=True,
        help="The batch this wizard was opened from. Only set when opened from the sample form.",
    )
    return_method = fields.Selection(
        [("pickup", "Customer Pickup"), ("ship", "Ship to Customer")],
        string="Return Method",
        required=True,
        help="How the items will be returned. The same method is applied to all selected items.",
    )
    courier_id = fields.Many2one(
        "test.sample.courier",
        string="Courier",
        help="Courier to use for all shipped items. Optional but recommended.",
    )

    # Shipping address fields, only used when return_method is "ship".
    # The same address is used for all items in the batch.
    ship_name = fields.Char(string="Recipient Name", help="Full name of the person or company the packages should be addressed to.")
    ship_street = fields.Char(string="Street", help="First line of the delivery address.")
    ship_street2 = fields.Char(string="Street 2", help="Second line of the delivery address.")
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
    requires_help_moving = fields.Boolean(string="Requires Help Moving", help="Tick if logistics needs to physically move these items and may need extra help.")
    moving_location_id = fields.Many2one("test.sample.location", string="Current Location", help="Where the items currently are. Pre-filled from the first item's current location.")
    moving_notes = fields.Text(string="Moving Notes", help="Free-text instructions for logistics about moving these items (e.g. size, fragility, access constraints).")
    on_pallet = fields.Selection([("yes", "Yes"), ("no", "No")], string="On a Pallet?", help="Whether the items are on a pallet.")

    line_ids = fields.One2many(
        "test.sample.bulk.return.wizard.line",
        "wizard_id",
        string="Items to Return",
        help="Items pre-selected for return. Uncheck any item you want to skip.",
    )

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        sample_id = self._context.get("default_sample_id")
        active_model = self._context.get("active_model")
        active_ids = self._context.get("active_ids", [])

        if sample_id:
            # Opened from a sample form: pre-fill with all tested items in that batch.
            res["sample_id"] = sample_id
            sample = self.env["test.sample"].browse(sample_id)
            eligible = sample.line_ids.filtered(lambda l: l.state == "tested")
        elif active_model == "test.sample.line" and active_ids:
            # Opened from a line list: use the selected rows that are tested.
            eligible = self.env["test.sample.line"].browse(active_ids).filtered(
                lambda l: l.state == "tested"
            )
        else:
            eligible = self.env["test.sample.line"]

        res["line_ids"] = [
            (0, 0, {
                "line_id": l.id,
                "product_name": l.product_name,
                "customer_name": l.sample_id.customer_id.name or "",
                "include": True,
            })
            for l in eligible
        ]
        
        # Prefill moving location from first eligible item
        if eligible:
            first_line = eligible[0]
            if first_line.current_location_id:
                res["moving_location_id"] = first_line.current_location_id.id
        
        return res

    @api.onchange("return_method")
    def _onchange_return_method(self):
        # Auto-fill shipping address from the customer when ship is selected.
        if self.return_method != "ship":
            return
        # Use sample_id customer if opened from a sample form, otherwise use the first item's customer.
        partner = None
        if self.sample_id:
            partner = self.sample_id.customer_id
        else:
            first_line = self.line_ids[:1]
            if first_line:
                partner = first_line.line_id.sample_id.customer_id
        if partner:
            self.ship_name = partner.name
            self.ship_street = partner.street
            self.ship_street2 = partner.street2
            self.ship_city = partner.city
            self.ship_zip = partner.zip
            self.ship_state_id = partner.state_id
            self.ship_country_id = partner.country_id

    def action_confirm(self):
        self.ensure_one()
        selected = self.line_ids.filtered(lambda l: l.include)
        if not selected:
            raise UserError(_("Select at least one item to return."))

        method_label = dict(
            self._fields["return_method"].selection
        ).get(self.return_method, self.return_method)

        # Validate all lines before making any changes.
        for wline in selected:
            line = wline.line_id
            if line.state != "tested":
                raise UserError(
                    _("%(item)s is no longer in Tested state. Refresh and try again.") % {"item": line.product_name}
                )

        # Generate shipping documents before changing state so the PDF captures the current data.
        if self.return_method == "ship":
            for wline in selected:
                self._post_shipping_document_for_line(wline.line_id)

        # Build moving_info dict if help is needed
        moving_info = None
        if self.requires_help_moving:
            moving_info = {
                "location": self.moving_location_id.display_name if self.moving_location_id else "",
                "notes": self.moving_notes or "",
                "on_pallet": dict(self._fields["on_pallet"].selection).get(self.on_pallet, "") if self.on_pallet else "",
            }

        for wline in selected:
            line = wline.line_id
            line.write({
                "return_method": self.return_method,
                "return_courier_id": self.courier_id.id if self.courier_id else False,
                "customer_courier_account": self.courier_account_number or "",
                "state": "pending_return",
                "responsible_id": False,  # Clear so logistics team can claim the return
            })

        # Send ONE consolidated logistics email covering all items in this bulk return.
        if self.return_method == "ship":
            self._send_bulk_logistics_notification(selected, courier_account=self.courier_account_number, moving_info=moving_info)
        elif self.return_method == "pickup":
            self._send_bulk_pickup_notification(selected, moving_info=moving_info)

        # Post a summary on each affected batch.
        affected_samples = selected.mapped("line_id.sample_id")
        for sample in affected_samples:
            batch_count = len(selected.filtered(lambda wl: wl.line_id.sample_id == sample))
            sample.message_post(
                body=Markup(
                    _("<b>Bulk return submitted</b>: %(count)d item(s) submitted for return "
                      "(%(method)s) by %(user)s. Logistics have been notified.")
                ) % {
                    "count": batch_count,
                    "method": method_label,
                    "user": self.env.user.name,
                },
                message_type="comment",
                subtype_xmlid="mail.mt_comment",
            )
        return {"type": "ir.actions.act_window_close"}

    def _send_bulk_logistics_notification(self, selected, courier_account=None, moving_info=None):
        # Send one email to the logistics team listing all items to be shipped in this bulk return.
        logistics_email = self.env["ir.config_parameter"].sudo().get_param(
            "sample_tracking.logistics_email"
        )
        if not logistics_email:
            raise UserError(_("Logistics email address is not configured. Set the system parameter 'sample_tracking.logistics_email'."))
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        courier = self.courier_id
        address_parts = [p for p in [
            self.ship_name,
            self.ship_street,
            self.ship_street2,
            self.ship_city,
            " ".join(filter(None, [self.ship_zip, self.ship_state_id.name if self.ship_state_id else ""])),
            self.ship_country_id.name if self.ship_country_id else "",
        ] if p and p.strip()]
        address_html = Markup("<br/>").join(escape(p) for p in address_parts)
        row_parts = []
        for wl in selected:
            line = wl.line_id
            item_url = "%s/web#model=test.sample.line&id=%d&view_type=form" % (base_url, line.id)
            row_parts.append(Markup(
                "<tr>"
                "<td style='padding: 6px 8px; border-bottom: 1px solid #eee;'>"
                "<a href='{url}'>{product}</a>"
                "</td>"
                "<td style='padding: 6px 8px; border-bottom: 1px solid #eee; color: #555;'>{ref}</td>"
                "<td style='padding: 6px 8px; border-bottom: 1px solid #eee; color: #555;'>{sample}</td>"
                "<td style='padding: 6px 8px; border-bottom: 1px solid #eee; color: #555;'>{so}</td>"
                "</tr>"
            ).format(
                url=item_url,
                product=escape(line.product_name),
                ref=escape(line.name or ""),
                sample=escape(line.sample_id.name or ""),
                so=escape(line.sale_order_id.name or ""),
            ))
        item_rows = Markup("").join(row_parts)
        
        # Build extra info rows for courier account and moving help
        extra_rows = ""
        if courier_account:
            extra_rows += (
                "<tr>"
                "<td style='padding: 6px 16px 6px 0; color: #555; white-space: nowrap; vertical-align: top;'>"
                "<strong>Customer Account at Courier</strong>"
                "</td>"
                "<td style='padding: 6px 0; color: #222;'>{account}</td>"
                "</tr>"
            ).format(account=escape(courier_account))
        if moving_info:
            extra_rows += "<tr><td style='padding: 6px 16px 6px 0; color: #555; white-space: nowrap; vertical-align: top;'><strong>Requires Help Moving</strong></td><td style='padding: 6px 0; color: #222;'>Yes</td></tr>"
            if moving_info.get("location"):
                extra_rows += (
                    "<tr>"
                    "<td style='padding: 6px 16px 6px 0; color: #555; white-space: nowrap; vertical-align: top;'>"
                    "<strong>Location</strong>"
                    "</td>"
                    "<td style='padding: 6px 0; color: #222;'>{location}</td>"
                    "</tr>"
                ).format(location=escape(moving_info["location"]))
            if moving_info.get("on_pallet"):
                extra_rows += (
                    "<tr>"
                    "<td style='padding: 6px 16px 6px 0; color: #555; white-space: nowrap; vertical-align: top;'>"
                    "<strong>On a Pallet?</strong>"
                    "</td>"
                    "<td style='padding: 6px 0; color: #222;'>{pallet}</td>"
                    "</tr>"
                ).format(pallet=escape(moving_info["on_pallet"]))
            if moving_info.get("notes"):
                extra_rows += (
                    "<tr>"
                    "<td style='padding: 6px 16px 6px 0; color: #555; white-space: nowrap; vertical-align: top;'>"
                    "<strong>Moving Notes</strong>"
                    "</td>"
                    "<td style='padding: 6px 0; color: #222;'>{notes}</td>"
                    "</tr>"
                ).format(notes=escape(moving_info["notes"]))
        
        body_html = Markup(
            "<div style='font-family: Arial, sans-serif; font-size: 14px; color: #222;'>"
            "<p style='margin-bottom: 16px;'>{count} sample item(s) are to be shipped to the customer.</p>"
            "<table style='border-collapse: collapse; margin-bottom: 16px;'>"
            "<tr>"
            "<td style='padding: 6px 16px 6px 0; color: #555; white-space: nowrap; vertical-align: top;'>"
            "<strong>Courier</strong>"
            "</td>"
            "<td style='padding: 6px 0; color: #222;'>{courier}</td>"
            "</tr>"
            "<tr>"
            "<td style='padding: 6px 16px 6px 0; color: #555; white-space: nowrap; vertical-align: top;'>"
            "<strong>Delivery Address</strong>"
            "</td>"
            "<td style='padding: 6px 0; color: #222;'>{address}</td>"
            "</tr>"
            "<tr>"
            "<td style='padding: 6px 16px 6px 0; color: #555; white-space: nowrap; vertical-align: top;'>"
            "<strong>Date</strong>"
            "</td>"
            "<td style='padding: 6px 0; color: #222;'>{date}</td>"
            "</tr>"
            "{extra_rows}"
            "</table>"
            "<table style='border-collapse: collapse; width: 100%; margin-bottom: 16px;'>"
            "<thead>"
            "<tr style='background: #f5f5f5;'>"
            "<th style='padding: 8px; text-align: left; border-bottom: 2px solid #ddd;'>Item</th>"
            "<th style='padding: 8px; text-align: left; border-bottom: 2px solid #ddd;'>Item Ref</th>"
            "<th style='padding: 8px; text-align: left; border-bottom: 2px solid #ddd;'>Sample Ref</th>"
            "<th style='padding: 8px; text-align: left; border-bottom: 2px solid #ddd;'>Sale Order</th>"
            "</tr>"
            "</thead>"
            "<tbody>{rows}</tbody>"
            "</table>"
            "</div>"
        ).format(
            count=len(selected),
            courier=escape(courier.name) if courier else "Not specified",
            address=address_html,
            date=escape(fields.Date.today().strftime("%d %B %Y")),
            extra_rows=Markup(extra_rows),
            rows=item_rows,
        )
        self.env["mail.mail"].sudo().create({
            "subject": "Sample Return - %d Item(s) to Ship - %s" % (len(selected), fields.Date.today().strftime("%d %B %Y")),
            "email_from": self.env.company.email or "",
            "email_to": logistics_email,
            "body_html": body_html,
            "auto_delete": True,
        }).send()

    def _send_bulk_pickup_notification(self, selected, moving_info=None):
        # Send one email to the logistics team listing all items awaiting customer pickup.
        logistics_email = self.env["ir.config_parameter"].sudo().get_param(
            "sample_tracking.logistics_email"
        )
        if not logistics_email:
            raise UserError(_("Logistics email address is not configured. Set the system parameter 'sample_tracking.logistics_email'."))
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        row_parts = []
        for wl in selected:
            line = wl.line_id
            item_url = "%s/web#model=test.sample.line&id=%d&view_type=form" % (base_url, line.id)
            row_parts.append(Markup(
                "<tr>"
                "<td style='padding: 6px 8px; border-bottom: 1px solid #eee;'>"
                "<a href='{url}'>{product}</a>"
                "</td>"
                "<td style='padding: 6px 8px; border-bottom: 1px solid #eee; color: #555;'>{ref}</td>"
                "<td style='padding: 6px 8px; border-bottom: 1px solid #eee; color: #555;'>{sample}</td>"
                "<td style='padding: 6px 8px; border-bottom: 1px solid #eee; color: #555;'>{so}</td>"
                "</tr>"
            ).format(
                url=item_url,
                product=escape(line.product_name),
                ref=escape(line.name or ""),
                sample=escape(line.sample_id.name or ""),
                so=escape(line.sale_order_id.name or ""),
            ))
        item_rows = Markup("").join(row_parts)
        
        # Build extra info rows for moving help
        extra_rows = ""
        if moving_info:
            extra_rows += "<tr><td style='padding: 6px 16px 6px 0; color: #555; white-space: nowrap; vertical-align: top;'><strong>Requires Help Moving</strong></td><td style='padding: 6px 0; color: #222;'>Yes</td></tr>"
            if moving_info.get("location"):
                extra_rows += (
                    "<tr>"
                    "<td style='padding: 6px 16px 6px 0; color: #555; white-space: nowrap; vertical-align: top;'>"
                    "<strong>Location</strong>"
                    "</td>"
                    "<td style='padding: 6px 0; color: #222;'>{location}</td>"
                    "</tr>"
                ).format(location=escape(moving_info["location"]))
            if moving_info.get("on_pallet"):
                extra_rows += (
                    "<tr>"
                    "<td style='padding: 6px 16px 6px 0; color: #555; white-space: nowrap; vertical-align: top;'>"
                    "<strong>On a Pallet?</strong>"
                    "</td>"
                    "<td style='padding: 6px 0; color: #222;'>{pallet}</td>"
                    "</tr>"
                ).format(pallet=escape(moving_info["on_pallet"]))
            if moving_info.get("notes"):
                extra_rows += (
                    "<tr>"
                    "<td style='padding: 6px 16px 6px 0; color: #555; white-space: nowrap; vertical-align: top;'>"
                    "<strong>Moving Notes</strong>"
                    "</td>"
                    "<td style='padding: 6px 0; color: #222;'>{notes}</td>"
                    "</tr>"
                ).format(notes=escape(moving_info["notes"]))
        
        body_html = Markup(
            "<div style='font-family: Arial, sans-serif; font-size: 14px; color: #222;'>"
            "<p style='margin-bottom: 16px;'>{count} sample item(s) are ready for customer pickup. "
            "Please arrange collection.</p>"
            "{moving_info_table}"
            "<table style='border-collapse: collapse; width: 100%; margin-bottom: 16px;'>"
            "<thead>"
            "<tr style='background: #f5f5f5;'>"
            "<th style='padding: 8px; text-align: left; border-bottom: 2px solid #ddd;'>Item</th>"
            "<th style='padding: 8px; text-align: left; border-bottom: 2px solid #ddd;'>Item Ref</th>"
            "<th style='padding: 8px; text-align: left; border-bottom: 2px solid #ddd;'>Sample Ref</th>"
            "<th style='padding: 8px; text-align: left; border-bottom: 2px solid #ddd;'>Sale Order</th>"
            "</tr>"
            "</thead>"
            "<tbody>{rows}</tbody>"
            "</table>"
            "<p><strong>Date:</strong> {date}</p>"
            "</div>"
        ).format(
            count=len(selected),
            moving_info_table=(
                "<table style='border-collapse: collapse; margin-bottom: 16px;'>{extra_rows}</table>".format(extra_rows=Markup(extra_rows))
                if extra_rows else ""
            ),
            rows=item_rows,
            date=escape(fields.Date.today().strftime("%d %B %Y")),
        )
        self.env["mail.mail"].sudo().create({
            "subject": "Sample Pickup - %d Item(s) Ready for Collection - %s" % (len(selected), fields.Date.today().strftime("%d %B %Y")),
            "email_from": self.env.company.email or "",
            "email_to": logistics_email,
            "body_html": body_html,
            "auto_delete": True,
        }).send()

    def _post_shipping_document_for_line(self, line):
        # Create a temporary single-item return wizard record so we can reuse the existing
        # shipping label report (which is bound to test.sample.return.wizard).
        temp_wizard = self.env["test.sample.return.wizard"].create({
            "line_id": line.id,
            "product_name": line.product_name,
            "return_method": self.return_method,
            "courier_id": self.courier_id.id if self.courier_id else False,
            "ship_name": self.ship_name,
            "ship_street": self.ship_street,
            "ship_street2": self.ship_street2,
            "ship_city": self.ship_city,
            "ship_zip": self.ship_zip,
            "ship_state_id": self.ship_state_id.id if self.ship_state_id else False,
            "ship_country_id": self.ship_country_id.id if self.ship_country_id else False,
        })
        report = self.env.ref("sample_tracking.action_report_shipping_label")
        pdf_content, _mime = report._render_qweb_pdf(
            report_ref="sample_tracking.action_report_shipping_label",
            res_ids=[temp_wizard.id],
        )
        attachment = self.env["ir.attachment"].create({
            "name": _("Shipping Document - %s.pdf") % line.product_name,
            "type": "binary",
            "datas": base64.b64encode(pdf_content),
            "res_model": "test.sample.line",
            "res_id": line.id,
            "mimetype": "application/pdf",
        })
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
        temp_wizard.unlink()


class TestSampleBulkReturnWizardLine(models.TransientModel):
    _name = "test.sample.bulk.return.wizard.line"
    _description = "Bulk Return Item Line"

    wizard_id = fields.Many2one(
        "test.sample.bulk.return.wizard",
        required=True,
        ondelete="cascade",
    )
    line_id = fields.Many2one("test.sample.line", string="Item", required=True, help="The item to be returned.")
    product_name = fields.Char(string="Item", help="Name of the item.")
    customer_name = fields.Char(string="Customer", help="Customer who owns this item.")
    include = fields.Boolean(string="Include", default=True, help="Uncheck to skip this item.")
