# Copyright 2026 Leszek Szczepkowski
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import api, fields, models
from odoo.exceptions import UserError

from . import structure


class HprPrice(models.Model):
    """Log of approved prices - append only. The daily file is built from it."""

    _name = "l10n_pl.hpr.price"
    _description = "Housing price register - approved price"
    _order = "approved_on desc, id desc"

    unit_id = fields.Many2one(
        "l10n_pl.hpr.unit",
        string="Unit",
        required=True,
        ondelete="restrict",
        index=True,
        readonly=True,
    )
    company_id = fields.Many2one(related="unit_id.company_id", store=True, index=True)
    name = fields.Char("Number", readonly=True)
    price = fields.Float("Gross price", digits=(16, 2), readonly=True)
    price_m2 = fields.Float("Price per m²", digits=(16, 2), readonly=True)
    area = fields.Float("Floor area (m²)", digits=(16, 2), readonly=True)
    date_from = fields.Date("Valid from", readonly=True)
    in_register = fields.Boolean(readonly=True)
    total_price = fields.Float(digits=(16, 2), readonly=True)
    annex_type = fields.Char(readonly=True)
    annex_ref = fields.Char(readonly=True)
    annex_price = fields.Float(digits=(16, 2), readonly=True)
    annex_date = fields.Date(readonly=True)
    approved_by_id = fields.Many2one("res.users", string="Approved by", readonly=True)
    approved_on = fields.Datetime(default=fields.Datetime.now, readonly=True)

    def write(self, vals):
        if vals:
            raise UserError(
                self.env._("The history of approved prices cannot be changed.")
            )
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_never(self):
        raise UserError(self.env._("The history of approved prices cannot be deleted."))

    def _hpr_record(self, stage_snapshot):
        """Fields E-J of the file from this price and the approved stage snapshot."""
        self.ensure_one()
        stage = stage_snapshot
        date_from = fields.Date.to_string(self.date_from)
        rec = {
            "property_type": structure.PROPERTY_TYPES.get(stage.get("property_type")),
            "unit_number": self.name,
            "price_m2": self.price_m2,
            "price_m2_date": date_from,
            "price": self.price,
            "price_date": date_from,
            "total_price": self.total_price or None,
            "total_price_date": date_from if self.total_price else None,
            "prospectus_url": stage.get("prospectus_url"),
        }
        if self.annex_type:
            rec.update(
                annex_type=self.annex_type,
                annex_ref=self.annex_ref,
                annex_price=self.annex_price or None,
                annex_date=fields.Date.to_string(self.annex_date)
                if self.annex_date
                else None,
            )
        # F, H, I come from the stage, only together with their description
        for desc, keys in (
            ("part_type", ("part_ref", "part_price", "part_date")),
            ("rights_desc", ("rights_value", "rights_date")),
            ("other_desc", ("other_value", "other_date")),
        ):
            if stage.get(desc):
                rec[desc] = stage[desc]
                for key in keys:
                    rec[key] = stage.get(key) or None
        return rec
