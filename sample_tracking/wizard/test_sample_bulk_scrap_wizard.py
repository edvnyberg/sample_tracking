from odoo import models, fields, api, _
from odoo.exceptions import UserError
from markupsafe import Markup, escape


# Popup form to submit scrap approval requests for multiple tested items at once.
# Managers can scrap items immediately; regular users must go through the approval flow.
# Allows specifying moving assistance details (location, pallet status, notes) for all items.
# Mirrors the single-item scrap wizard but operates on a set of lines selected from a list view.
class TestSampleBulkScrapWizard(models.TransientModel):
    _name = "test.sample.bulk.scrap.wizard"
    _description = "Bulk Scrap Items"

    sample_id = fields.Many2one(
        "test.sample",
        string="Sample",
        readonly=True,
        help="The batch this wizard was opened from. Only set when opened from the sample form.",
    )
    approver_id = fields.Many2one(
        "res.users",
        string="Approver",
        domain=lambda self: [("groups_id", "in", self.env.ref("sample_tracking.group_sample_manager").ids)],
        help="Manager who must approve all scrap requests in this batch. Required for non-manager users. "
             "Managers can skip this field to scrap items immediately.",
    )
    
    requires_help_moving = fields.Boolean(string="Requires Help Moving", help="Tick if logistics needs to physically move these items and may need extra help.")
    moving_location_id = fields.Many2one("test.sample.location", string="Current Location", help="Where the items currently are. Pre-filled from the first item's current location.")
    moving_notes = fields.Text(string="Moving Notes", help="Free-text instructions for logistics about moving these items (e.g. size, fragility, access constraints).")
    on_pallet = fields.Selection([("yes", "Yes"), ("no", "No")], string="On a Pallet?", help="Whether the items are on a pallet.")

    line_ids = fields.One2many(
        "test.sample.bulk.scrap.wizard.line",
        "wizard_id",
        string="Items to Scrap",
        help="Items pre-selected for scrapping. Uncheck any item you want to skip.",
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
            eligible = sample.line_ids.filtered(
                lambda l: l.state == "tested" and not l.pending_scrap_request_id and not l.has_pending_transfer
            )
        elif active_model == "test.sample.line" and active_ids:
            # Opened from a line list: use the selected rows that are eligible.
            eligible = self.env["test.sample.line"].browse(active_ids).filtered(
                lambda l: l.state == "tested" and not l.pending_scrap_request_id and not l.has_pending_transfer
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
        if not res.get("approver_id"):
            approver_id = self.env["ir.config_parameter"].sudo().get_param(
                "sample_tracking.default_approver_id"
            )
            if approver_id:
                res["approver_id"] = int(approver_id)
        
        # Prefill moving location from first eligible item
        if eligible:
            first_line = eligible[0]
            if first_line.current_location_id:
                res["moving_location_id"] = first_line.current_location_id.id
        
        return res

    def action_confirm(self):
        self.ensure_one()
        selected = self.line_ids.filtered(lambda l: l.include)
        if not selected:
            raise UserError(_("Select at least one item to scrap."))

        is_manager = self.env.user.has_group("sample_tracking.group_sample_manager")

        # Non-managers must specify an approver.
        if not is_manager and not self.approver_id:
            raise UserError(_("Please select an approver for the scrap requests."))

        # Validate all lines before making any changes.
        for wline in selected:
            line = wline.line_id
            if line.state != "tested":
                raise UserError(
                    _("%(item)s is no longer in Tested state. Refresh and try again.") % {"item": line.product_name}
                )
            if line.pending_scrap_request_id:
                raise UserError(
                    _("%(item)s already has a pending scrap request.") % {"item": line.product_name}
                )

        if is_manager:
            # Managers scrap immediately, no approval needed.
            for wline in selected:
                line = wline.line_id
                line.write({"state": "scrapped", "date_closed": fields.Date.today()})

            affected_samples = selected.mapped("line_id.sample_id")
            for sample in affected_samples:
                batch_count = len(selected.filtered(lambda wl: wl.line_id.sample_id == sample))
                sample.message_post(
                    body=Markup(
                        _("<b>Bulk scrap</b>: %(count)d item(s) scrapped immediately by %(user)s.")
                    ) % {"count": batch_count, "user": self.env.user.name},
                    message_type="comment",
                    subtype_xmlid="mail.mt_comment",
                )
            
            # Send moving notification if needed
            if self.requires_help_moving:
                moving_info = {
                    "location": self.moving_location_id.display_name if self.moving_location_id else "",
                    "notes": self.moving_notes or "",
                    "on_pallet": dict(self._fields["on_pallet"].selection).get(self.on_pallet, "") if self.on_pallet else "",
                }
                self._send_bulk_moving_notification(selected, moving_info)
        else:
            # Regular users create an approval request per item.
            for wline in selected:
                line = wline.line_id
                self.env["test.sample.scrap.request"].create({
                    "line_id": line.id,
                    "approver_id": self.approver_id.id,
                })
                line.write({"state": "scrap_pending"})

            # Send ONE consolidated email to the approver listing all items awaiting approval.
            self._send_bulk_scrap_approval_request(selected)

            affected_samples = selected.mapped("line_id.sample_id")
            for sample in affected_samples:
                batch_count = len(selected.filtered(lambda wl: wl.line_id.sample_id == sample))
                sample.message_post(
                    body=Markup(
                        _("<b>Bulk scrap requested</b>: %(count)d item(s) submitted for scrap approval "
                          "by %(user)s. Awaiting approval from %(approver)s.")
                    ) % {
                        "count": batch_count,
                        "user": self.env.user.name,
                        "approver": self.approver_id.name,
                    },
                    message_type="comment",
                    subtype_xmlid="mail.mt_comment",
                )
            
            # Send moving notification if needed
            if self.requires_help_moving:
                moving_info = {
                    "location": self.moving_location_id.display_name if self.moving_location_id else "",
                    "notes": self.moving_notes or "",
                    "on_pallet": dict(self._fields["on_pallet"].selection).get(self.on_pallet, "") if self.on_pallet else "",
                }
                self._send_bulk_moving_notification(selected, moving_info)

        return {"type": "ir.actions.act_window_close"}

    def _send_bulk_scrap_approval_request(self, selected):
        # Send one email to the approver listing all items awaiting scrap approval.
        approver = self.approver_id
        email_to = (approver.partner_id.email or approver.login or "").strip()
        if not email_to:
            return
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
        body_html = Markup(
            "<div style='font-family: Arial, sans-serif; font-size: 14px; color: #222;'>"
            "<p>Hi {approver},</p>"
            "<p><strong>{sender}</strong> has submitted a scrap approval request for "
            "<strong>{count} item(s)</strong>. Please review and approve or reject each request.</p>"
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
            approver=escape(approver.name),
            sender=escape(self.env.user.name),
            count=len(selected),
            rows=item_rows,
        )
        self.env["mail.mail"].sudo().create({
            "subject": "Scrap Approval Request: %d Item(s) Awaiting Your Approval" % len(selected),
            "email_from": self.env.company.email or "",
            "email_to": email_to,
            "body_html": body_html,
            "auto_delete": True,
        }).send()

    def _send_bulk_moving_notification(self, selected, moving_info):
        # Send one email to logistics team for scrap items requiring moving assistance.
        logistics_email = self.env["ir.config_parameter"].sudo().get_param(
            "sample_tracking.logistics_email"
        )
        if not logistics_email:
            return
        
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        row_parts = []
        for wline in selected:
            line = wline.line_id
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
            "<p style='margin-bottom: 16px;'>{count} sample item(s) are being scrapped and require assistance with moving.</p>"
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
            "</div>"
        ).format(
            count=len(selected),
            moving_info_table=(
                "<table style='border-collapse: collapse; margin-bottom: 16px;'>{extra_rows}</table>".format(extra_rows=Markup(extra_rows))
                if extra_rows else ""
            ),
            rows=item_rows,
        )
        self.env["mail.mail"].sudo().create({
            "subject": "Scrap Items - Moving Assistance Needed - %d Item(s)" % len(selected),
            "email_from": self.env.company.email or "",
            "email_to": logistics_email,
            "body_html": body_html,
            "auto_delete": True,
        }).send()


class TestSampleBulkScrapWizardLine(models.TransientModel):
    _name = "test.sample.bulk.scrap.wizard.line"
    _description = "Bulk Scrap Item Line"

    wizard_id = fields.Many2one(
        "test.sample.bulk.scrap.wizard",
        required=True,
        ondelete="cascade",
    )
    line_id = fields.Many2one("test.sample.line", string="Item", required=True, help="The item to be scrapped.")
    product_name = fields.Char(string="Item", help="Name of the item.")
    customer_name = fields.Char(string="Customer", help="Customer who owns this item.")
    include = fields.Boolean(string="Include", default=True, help="Uncheck to skip this item.")
