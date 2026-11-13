# Copyright 2026 Leszek Szczepkowski
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import json
import re
from datetime import datetime

import pytz

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.http import request

TZ = "Europe/Warsaw"
APPROVAL_FIELDS = ("approved", "approved_by_id", "approved_on", "approved_snapshot")
MANAGER_GROUP = "l10n_pl_housing_price_register.group_hpr_manager"
USER_GROUP = "l10n_pl_housing_price_register.group_hpr_user"
# Context marker of the approve buttons. A Python object cannot come through
# JSON-RPC or XML-RPC, so a client cannot switch the approval guard off.
APPROVING = object()


def approving(env):
    return env.context.get("hpr_approving") is APPROVING


def changed_fields(records, vals, field_names):
    """Names from `field_names` whose new value differs on at least one record.

    A write that sends the same values again (an import, an integration sending
    every field) does not remove the approval.
    """
    out = set()
    for name in set(vals) & set(field_names):
        field = records._fields[name]
        new = field.convert_to_cache(vals[name], records[:0], validate=False)
        for rec in records:
            old = field.convert_to_cache(rec[name], rec, validate=False)
            if (old or False) != (new or False):
                out.add(name)
                break
    return out


def now_pl():
    return datetime.now(pytz.timezone(TZ))


def today_pl():
    return now_pl().date()


def check_button_call(env):
    """Approve only through the button route of the web client.

    The register is a legal statement of the developer. Calls through
    JSON-RPC/XML-RPC or other routes are refused, so an integration cannot
    approve on its own. It is a guard against RPC clients, not a proof that a
    person clicked. Without an HTTP request (tests, odoo shell) the call is
    allowed.
    """
    try:
        path = request.httprequest.path if request else None
    except RuntimeError:
        path = None
    if path is not None and not path.startswith("/web/dataset/call_button"):
        raise UserError(
            env._(
                "Only a person can approve data for the price register, "
                "with the button in Odoo."
            )
        )


def check_manager(env):
    if not env.user.has_group(MANAGER_GROUP):
        raise UserError(
            env._(
                "Only members of the group “Housing price register: Approval” "
                "can do this."
            )
        )


FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def check_no_formula(record, field_names, skip=()):
    """Refuse text that a spreadsheet would run as a formula.

    The daily file is public, people open it in spreadsheets and it can never be
    corrected, so such text is stopped when it is typed.
    """
    for name in field_names:
        field = record._fields[name]
        if name in skip or field.type not in ("char", "text"):
            continue
        # same normalisation as the cell written to the file
        value = re.sub(r"\s+", " ", record[name] or "").strip()
        if value.startswith(FORMULA_START):
            raise ValidationError(
                record.env._(
                    "%(field)s: the text cannot start with = + - or @ "
                    "(spreadsheets would read it as a formula).",
                    field=field.string,
                )
            )


def to_json(record, field_names):
    out = {}
    for name in field_names:
        value = record[name]
        if hasattr(value, "isoformat"):
            value = value.isoformat()
        out[name] = value if value is not False else None
    return json.dumps(out, ensure_ascii=False, sort_keys=True)


class HprApprovalMixin(models.AbstractModel):
    """Data goes to the register only after a person approves it.

    Approval stores a snapshot of `_hpr_data_fields`. Changing one of these
    fields removes the approved flag, but the snapshot stays: the daily file keeps
    using the last approved version until the next approval.
    """

    _name = "l10n_pl.hpr.approval.mixin"
    _description = "Housing price register - approval"
    _hpr_data_fields = ()

    approved = fields.Boolean(readonly=True, copy=False, tracking=True)
    approved_by_id = fields.Many2one(
        "res.users", string="Approved by", readonly=True, copy=False
    )
    approved_on = fields.Datetime(readonly=True, copy=False)
    approved_snapshot = fields.Text(readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        if not approving(self.env):
            vals_list = [
                {k: v for k, v in vals.items() if k not in APPROVAL_FIELDS}
                for vals in vals_list
            ]
        return super().create(vals_list)

    def write(self, vals):
        if not approving(self.env):
            vals = {k: v for k, v in vals.items() if k not in APPROVAL_FIELDS}
            if changed_fields(self, vals, self._hpr_data_fields):
                vals["approved"] = False
        return super(HprApprovalMixin, self.with_context(hpr_approving=False)).write(
            vals
        )

    def _hpr_check_before_approval(self):
        return []

    def _hpr_snapshot(self):
        self.ensure_one()
        return json.loads(self.approved_snapshot) if self.approved_snapshot else {}

    def action_approve(self):
        check_manager(self.env)
        check_button_call(self.env)
        for rec in self:
            errors = rec._hpr_check_before_approval()
            if errors:
                raise UserError(
                    self.env._(
                        "Cannot approve “%(name)s”:\n%(errors)s",
                        name=rec.display_name,
                        errors="\n".join(errors),
                    )
                )
            rec.with_context(hpr_approving=APPROVING).write(
                {
                    "approved": True,
                    "approved_by_id": self.env.uid,
                    "approved_on": fields.Datetime.now(),
                    "approved_snapshot": to_json(rec, rec._hpr_data_fields),
                }
            )
        return True
