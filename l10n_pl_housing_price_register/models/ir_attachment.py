# Copyright 2026 Leszek Szczepkowski
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import base64

from odoo import models
from odoo.exceptions import UserError

CONTENT_FIELDS = ("raw", "datas", "db_datas")


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    def _hpr_new_content(self, vals):
        """Bytes the write would store, or None when it does not touch content."""
        if "raw" in vals:
            value = vals["raw"]
        elif "datas" in vals:
            value = base64.b64decode(vals["datas"] or b"")
        elif "db_datas" in vals:
            value = vals["db_datas"]
        else:
            return None
        if isinstance(value, str):
            value = value.encode()
        return value or b""

    def write(self, vals):
        # rewriting the same bytes is allowed: force_storage() moves files between
        # the database and the filestore that way
        files = (
            self.filtered(lambda a: a.res_model == "l10n_pl.hpr.file" and a.res_id)
            if set(CONTENT_FIELDS) & set(vals)
            else self.browse()
        )
        new = self._hpr_new_content(vals) if files else None
        if new is not None and files.filtered(lambda a: (a.raw or b"") != new):
            raise UserError(
                self.env._("The content of a daily price register file cannot change.")
            )
        return super().write(vals)
