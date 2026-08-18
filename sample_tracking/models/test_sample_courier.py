from odoo import models, fields


# A delivery company used for shipping samples back to customers.
# Managed under Configuration > Couriers so the list can be updated without code changes.
class TestSampleCourier(models.Model):
    _name = "test.sample.courier"
    _description = "Courier"
    _order = "name"

    name = fields.Char(string="Name", required=True)
    active = fields.Boolean(default=True)
