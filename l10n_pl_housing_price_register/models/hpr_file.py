# Copyright 2026 Leszek Szczepkowski
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import api, fields, models
from odoo.exceptions import UserError


class HprFile(models.Model):
    """Daily file - never changed: dane.gov.pl copies it, so its address and
    content must stay the same."""

    _name = "l10n_pl.hpr.file"
    _description = "Housing price register - daily file"
    _order = "day desc, id desc"

    project_id = fields.Many2one(
        "l10n_pl.hpr.project",
        string="Project",
        required=True,
        ondelete="restrict",
        index=True,
        readonly=True,
    )
    developer_id = fields.Many2one(
        related="project_id.developer_id", store=True, index=True
    )
    company_id = fields.Many2one(
        related="project_id.company_id", store=True, index=True
    )
    day = fields.Date(required=True, readonly=True, index=True)
    name = fields.Char("File", required=True, readonly=True, index=True)
    url = fields.Char("Address", required=True, readonly=True)
    ext_ident = fields.Char(
        "Resource identifier (extIdent)", required=True, readonly=True
    )
    attachment_id = fields.Many2one(
        "ir.attachment",
        string="Content",
        required=True,
        readonly=True,
        ondelete="restrict",
    )
    md5 = fields.Char("MD5", readonly=True)
    record_count = fields.Integer("Units", readonly=True)
    units = fields.Char("Units and prices", readonly=True)
    structure_version = fields.Char("Structure", readonly=True)

    _day_uniq = models.Constraint(
        "unique(project_id, day)", "A file for this day already exists."
    )

    def write(self, vals):
        if vals:
            raise UserError(
                self.env._(
                    "A daily file cannot be changed: dane.gov.pl already has a copy."
                )
            )
        return super().write(vals)

    @api.ondelete(at_uninstall=False)
    def _unlink_never(self):
        raise UserError(self.env._("A daily file cannot be deleted."))
