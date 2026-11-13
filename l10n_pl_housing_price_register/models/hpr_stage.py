# Copyright 2026 Leszek Szczepkowski
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import requests

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from .hpr_approval_mixin import check_no_formula

MAX_REDIRECTS = 3
REDIRECT_CODES = (301, 302, 303, 307, 308)

STAGE_FIELDS = (
    "name",
    "property_type",
    "escrow_open",
    "escrow_account",
    "escrow_bank",
    "escrow_date",
    "prospectus_url",
    "part_type",
    "part_ref",
    "part_price",
    "part_date",
    "rights_desc",
    "rights_value",
    "rights_date",
    "other_desc",
    "other_value",
    "other_date",
)


def public_https(url):
    """True when the URL is https and its host resolves to public addresses only.

    The prospectus URL is typed by a user and checked by the server, so internal
    hosts (loopback, private networks, cloud metadata) are never contacted.
    Known limit: requests resolves the name again when it connects (DNS
    rebinding). The URL is typed by a person of the Approval group, so the risk
    is accepted rather than pinning the address in a custom adapter.
    """
    parts = urlsplit(url or "")
    if parts.scheme != "https" or not parts.hostname:
        return False
    try:
        infos = socket.getaddrinfo(
            parts.hostname, parts.port or 443, proto=socket.IPPROTO_TCP
        )
        return bool(infos) and all(
            ipaddress.ip_address(info[4][0]).is_global for info in infos
        )
    except (OSError, UnicodeError, ValueError):
        return False


class HprStage(models.Model):
    """Stage of a development project (zadanie inwestycyjne).

    The stage holds the escrow account (mieszkaniowy rachunek powierniczy) and
    the information prospectus. Units of a stage are sent only when the escrow
    account is open, the prospectus is published and the switch is on.
    """

    _name = "l10n_pl.hpr.stage"
    _description = "Housing price register - stage"
    _inherit = ["mail.thread", "l10n_pl.hpr.approval.mixin"]
    _order = "project_id, sequence, id"
    _check_company_auto = True
    _hpr_data_fields = STAGE_FIELDS

    name = fields.Char(required=True, tracking=True)
    sequence = fields.Integer(default=10)
    project_id = fields.Many2one(
        "l10n_pl.hpr.project",
        string="Project",
        required=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(
        related="project_id.company_id", store=True, index=True
    )
    property_type = fields.Selection(
        [("flat", "Residential unit"), ("house", "Single-family house")],
        string="Property type (E1)",
        required=True,
        default="flat",
        tracking=True,
    )
    escrow_open = fields.Boolean(
        "Escrow account open",
        tracking=True,
        help="Without an open housing escrow account no unit of this stage goes to "
        "the register. Requires the account number, bank and date.",
    )
    escrow_account = fields.Char("Escrow account number", tracking=True)
    escrow_bank = fields.Char("Bank", tracking=True)
    escrow_date = fields.Date("Escrow account since", tracking=True)
    prospectus_url = fields.Char("Prospectus URL (J1)", tracking=True)
    part_type = fields.Char(
        "Part of the property - type (F1)",
        tracking=True,
        help="E.g. a share in an internal road, if it is sold with the unit and is "
        "not included in the unit price.",
    )
    part_ref = fields.Char("Part of the property - reference (F2)", tracking=True)
    part_price = fields.Float(
        "Part of the property - gross price (F3)", digits=(16, 2), tracking=True
    )
    part_date = fields.Date("Part of the property - price from (F4)", tracking=True)
    rights_desc = fields.Char("Rights needed to use the unit (H1)", tracking=True)
    rights_value = fields.Float(
        "Rights needed to use the unit - value (H2)", digits=(16, 2), tracking=True
    )
    rights_date = fields.Date(
        "Rights needed to use the unit - value from (H3)", tracking=True
    )
    other_desc = fields.Char("Other payments of the buyer (I1)", tracking=True)
    other_value = fields.Float(
        "Other payments of the buyer - value (I2)", digits=(16, 2), tracking=True
    )
    other_date = fields.Date("Other payments of the buyer - from (I3)", tracking=True)
    sending_enabled = fields.Boolean("Sending enabled", tracking=True, copy=False)
    sending_active = fields.Boolean(
        "Data is sent", compute="_compute_sending", store=True
    )
    sending_reason = fields.Char("Sending status", compute="_compute_sending_reason")
    unit_ids = fields.One2many("l10n_pl.hpr.unit", "stage_id", "Units")
    unit_count = fields.Integer("Unit count", compute="_compute_counts")
    in_register_count = fields.Integer("In register", compute="_compute_counts")

    @api.depends("sending_enabled", "escrow_open", "prospectus_url")
    def _compute_sending(self):
        for stage in self:
            stage.sending_active = (
                stage.escrow_open
                and bool(stage.prospectus_url)
                and stage.sending_enabled
            )

    @api.depends("sending_enabled", "escrow_open", "prospectus_url")
    @api.depends_context("lang")
    def _compute_sending_reason(self):
        # not stored: the text follows the language of the reader
        for stage in self:
            reasons = []
            if not stage.escrow_open:
                reasons.append(self.env._("no escrow account"))
            if not stage.prospectus_url:
                reasons.append(self.env._("no prospectus"))
            if not stage.sending_enabled:
                reasons.append(self.env._("switch is off"))
            stage.sending_reason = (
                ", ".join(reasons) if reasons else self.env._("data is sent")
            )

    @api.depends("unit_ids.in_register")
    def _compute_counts(self):
        for stage in self:
            stage.unit_count = len(stage.unit_ids)
            stage.in_register_count = len(stage.unit_ids.filtered("in_register"))

    @api.constrains(
        "sending_enabled",
        "escrow_open",
        "prospectus_url",
        "escrow_account",
        "escrow_bank",
        "escrow_date",
    )
    def _check_sending(self):
        for stage in self:
            if stage.sending_enabled and not (
                stage.escrow_open and stage.prospectus_url
            ):
                raise ValidationError(
                    self.env._(
                        "Stage “%s”: sending can be enabled only with an open escrow "
                        "account and a prospectus.",
                        stage.name,
                    )
                )
            if stage.escrow_open and not (
                stage.escrow_account and stage.escrow_bank and stage.escrow_date
            ):
                raise ValidationError(
                    self.env._(
                        "Stage “%s”: with an open escrow account enter its number, "
                        "bank and date.",
                        stage.name,
                    )
                )
            if not stage.escrow_open and stage.unit_ids.filtered("in_register"):
                raise ValidationError(
                    self.env._(
                        "Stage “%s”: without an escrow account no unit can be "
                        "“in register”.",
                        stage.name,
                    )
                )

    @api.constrains(*STAGE_FIELDS)
    def _check_url(self):
        for stage in self:
            check_no_formula(stage, STAGE_FIELDS)
            url = stage.prospectus_url
            if url and (
                not url.startswith("https://") or any(c.isspace() for c in url)
            ):
                raise ValidationError(
                    self.env._(
                        "The prospectus URL must start with https:// and contain no "
                        "spaces."
                    )
                )
            for fname in ("part_price", "rights_value", "other_value"):
                if stage[fname] < 0:
                    raise ValidationError(
                        self.env._(
                            "Stage “%(stage)s”: %(field)s cannot be negative.",
                            stage=stage.name,
                            field=stage._fields[fname].string,
                        )
                    )

    def write(self, vals):
        if "project_id" in vals and self.filtered(
            lambda s: s.project_id.id != vals["project_id"]
            and (s.project_id.file_ids or s.unit_ids.price_ids)
        ):
            raise UserError(
                self.env._(
                    "The project of a stage cannot change after the first file or "
                    "the first approved price: its units would appear in the file "
                    "of another project without approval."
                )
            )
        return super().write(vals)

    def _hpr_check_before_approval(self):
        errors = []
        if self.escrow_open and not self.prospectus_url:
            errors.append(self.env._("prospectus URL is missing"))
        labels = {
            "part": self.env._("part of the property"),
            "rights": self.env._("rights needed to use the unit"),
            "other": self.env._("other payments"),
        }
        for prefix in ("part", "rights", "other"):
            label = labels[prefix]
            value_field = "part_price" if prefix == "part" else f"{prefix}_value"
            desc = self[f"{prefix}_type" if prefix == "part" else f"{prefix}_desc"]
            if bool(desc) != bool(self[value_field]):
                errors.append(
                    self.env._(
                        "%s: enter the description and the amount, or nothing", label
                    )
                )
            if self[value_field] and not self[f"{prefix}_date"]:
                errors.append(self.env._("%s: the “from” date is missing", label))
        return errors

    def _hpr_prospectus_http_code(self):
        """HTTP status of the prospectus (once a day from the cron).

        Redirects are followed by hand, each target must again be a public https
        address. Errors come back as text, which goes to the issue list.
        """
        url = self.prospectus_url
        for _hop in range(MAX_REDIRECTS + 1):
            if not public_https(url):
                return self.env._("not a public https address")
            try:
                with requests.head(url, allow_redirects=False, timeout=8) as r:
                    code, location = r.status_code, r.headers.get("Location")
                if code in (403, 405):
                    with requests.get(
                        url, stream=True, allow_redirects=False, timeout=8
                    ) as r:
                        code, location = r.status_code, r.headers.get("Location")
            except requests.RequestException as e:  # no network, DNS, TLS
                return type(e).__name__
            if code not in REDIRECT_CODES or not location:
                return code
            url = urljoin(url, location)
        return self.env._("too many redirects")
