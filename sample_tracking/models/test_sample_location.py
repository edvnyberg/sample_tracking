from odoo import models, fields


# A physical location or department where sample items are stored,
# e.g. "Lab A", "Receiving Bay". Used to track where a sample item is at any given time.
class TestSampleLocation(models.Model):
    _name = "test.sample.location"
    _description = "Sample Location / Department"
    _order = "name"

    name = fields.Char(string="Name", required=True)
    description = fields.Text(string="Description")
    active = fields.Boolean(default=True)
