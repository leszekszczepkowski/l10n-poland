# Copyright 2026 Leszek Szczepkowski
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import logging
import re
import uuid

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import ormcache, str2bool

from . import structure
from .hpr_approval_mixin import check_no_formula, now_pl

_logger = logging.getLogger(__name__)
RESERVED_SLUGS = {"preview", "podglad", "dane"}
SLUG_RE = r"[a-z0-9]+(-[a-z0-9]+)*"
BASE_URL_RE = r"https://[A-Za-z0-9.-]+(:\d+)?(/[A-Za-z0-9._~/-]*)?"
LOCAL_HOST_RE = (
    r"^https://(localhost|127\.|0\.|10\.|192\.168\.|169\.254\."
    r"|172\.(1[6-9]|2\d|3[01])\.|\[?::1|\[?f[cd])"
)
EMAIL_RE = r"[^@\s]+@[^@\s]+\.[^@\s]+"
NIP_WEIGHTS = (6, 5, 7, 2, 3, 4, 5, 6, 7)
REGON_WEIGHTS = {
    9: (8, 9, 2, 3, 4, 5, 6, 7),
    14: (2, 4, 8, 5, 0, 9, 7, 3, 6, 1, 2, 4, 8),
}


def checksum_ok(digits, weights):
    """Polish NIP and REGON check digit: weighted sum modulo 11 (10 counts as 0
    for REGON, it is never valid for NIP)."""
    total = sum(int(d) * w for d, w in zip(digits, weights, strict=False)) % 11
    if len(weights) == len(NIP_WEIGHTS):
        return total != 10 and total == int(digits[-1])
    return total % 10 == int(digits[-1])


DEVELOPER_FIELDS = (
    "name",
    "legal_form",
    "krs",
    "ceidg",
    "nip",
    "regon",
    "phone",
    "email",
    "website",
    "seat_state",
    "seat_county",
    "seat_municipality",
    "seat_city",
    "seat_street",
    "seat_house_no",
    "seat_flat_no",
    "seat_zip",
    "sales_state",
    "sales_county",
    "sales_municipality",
    "sales_city",
    "sales_street",
    "sales_house_no",
    "sales_flat_no",
    "sales_zip",
    "sales_other_locations",
    "sales_contact_method",
    "dataset_url",
)


class HprDeveloper(models.Model):
    _name = "l10n_pl.hpr.developer"
    _description = "Housing price register - developer"
    _inherit = ["mail.thread", "mail.activity.mixin", "l10n_pl.hpr.approval.mixin"]
    _order = "name"
    _hpr_data_fields = DEVELOPER_FIELDS

    name = fields.Char(
        "Company name (A1)",
        required=True,
        tracking=True,
        help="Full company name as in the National Court Register (KRS).",
    )
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda s: s.env.company, tracking=True
    )
    active = fields.Boolean(default=True)
    responsible_id = fields.Many2one(
        "res.users",
        string="Responsible",
        default=lambda s: s.env.user,
        tracking=True,
        help="Gets activities about inconsistencies and missing daily files.",
    )
    legal_form = fields.Char(
        "Legal form (A2)",
        tracking=True,
        default="spółka z ograniczoną odpowiedzialnością",
    )
    krs = fields.Char("KRS (A3)", tracking=True)
    ceidg = fields.Char("CEIDG (A4)", tracking=True)
    nip = fields.Char("NIP (A5)", tracking=True)
    regon = fields.Char("REGON (A6)", tracking=True)
    phone = fields.Char(
        "Phone (A7)",
        tracking=True,
        help="9 digits without spaces or dashes; a + sign and prefix are allowed.",
    )
    email = fields.Char("Email (A8)", tracking=True)
    website = fields.Char("Website (A9)", tracking=True)
    seat_state = fields.Char("Voivodeship (B1)", tracking=True)
    seat_county = fields.Char("County (B2)", tracking=True)
    seat_municipality = fields.Char("Municipality (B3)", tracking=True)
    seat_city = fields.Char("City (B4)", tracking=True)
    seat_street = fields.Char("Street (B5)", tracking=True)
    seat_house_no = fields.Char("Building number (B6)", tracking=True)
    seat_flat_no = fields.Char("Unit number (B7)", tracking=True)
    seat_zip = fields.Char("Postal code (B8)", tracking=True)
    sales_state = fields.Char("Voivodeship (C1)", tracking=True)
    sales_county = fields.Char("County (C2)", tracking=True)
    sales_municipality = fields.Char("Municipality (C3)", tracking=True)
    sales_city = fields.Char("City (C4)", tracking=True)
    sales_street = fields.Char("Street (C5)", tracking=True)
    sales_house_no = fields.Char("Building number (C6)", tracking=True)
    sales_flat_no = fields.Char("Unit number (C7)", tracking=True)
    sales_zip = fields.Char("Postal code (C8)", tracking=True)
    sales_other_locations = fields.Char("Other sales locations (C9)", tracking=True)
    sales_contact_method = fields.Char("How buyers contact you (C10)", tracking=True)

    slug = fields.Char(
        "URL name",
        required=True,
        tracking=True,
        help="Part of the file addresses, e.g. green-hill. Do not change it after "
        "the first file: dane.gov.pl requires addresses that never change.",
    )
    ext_ident = fields.Char(
        "Dataset identifier (extIdent)",
        required=True,
        readonly=True,
        copy=False,
        default=lambda s: str(uuid.uuid4()),
        help="36 characters, set once. Never change it.",
    )
    dataset_url = fields.Char(
        "Price list page",
        tracking=True,
        help="Page with information about the dataset, e.g. the price list on the "
        "project website.",
    )
    project_ids = fields.One2many("l10n_pl.hpr.project", "developer_id", "Projects")
    file_ids = fields.One2many("l10n_pl.hpr.file", "developer_id", "Daily files")
    xml_url = fields.Char("XML for dane.gov.pl", compute="_compute_urls")
    md5_url = fields.Char("XML checksum (MD5)", compute="_compute_urls")

    _company_uniq = models.Constraint(
        "unique(company_id)", "This company already has a price register."
    )
    _slug_uniq = models.Constraint("unique(slug)", "This URL name is already taken.")

    def _hpr_base_url(self):
        """Public address of the files, used in every daily file for good.

        System parameter l10n_pl_housing_price_register.base_url wins. Without it
        web.base.url is used only when frozen (web.base.url.freeze), because Odoo
        rewrites an unfrozen web.base.url when an administrator logs in, and a
        file created at that moment would keep a wrong address forever.
        """
        params = self.env["ir.config_parameter"].sudo()
        explicit = params.get_param("l10n_pl_housing_price_register.base_url")
        if explicit:
            return explicit.strip().rstrip("/")
        if str2bool(params.get_param("web.base.url.freeze") or "False", False):
            return (params.get_param("web.base.url") or "").strip().rstrip("/")
        return ""

    def _hpr_base_url_issue(self):
        """Text explaining why the files cannot get a public address, or None."""
        base = self._hpr_base_url()
        if not base:
            return self.env._(
                "Set the system parameter l10n_pl_housing_price_register.base_url "
                "(or freeze web.base.url): daily files keep their address forever."
            )
        if not re.fullmatch(BASE_URL_RE, base) or re.search(LOCAL_HOST_RE, base):
            return self.env._(
                "The public address %s is not a public https address; dane.gov.pl "
                "accepts only https.",
                base,
            )
        return None

    @api.depends("slug")
    def _compute_urls(self):
        base = self._hpr_base_url()
        for dev in self:
            dev.xml_url = (
                f"{base}/rejestr-cen/{dev.slug}/dane.xml" if dev.slug else False
            )
            dev.md5_url = (
                f"{base}/rejestr-cen/{dev.slug}/dane.md5" if dev.slug else False
            )

    @api.constrains("slug", *DEVELOPER_FIELDS)
    def _check_formats(self):
        digits_10 = self.env._("10 digits")
        hints = {
            "krs": digits_10,
            "nip": digits_10,
            "regon": self.env._("9 or 14 digits"),
            "phone": self.env._("9 digits"),
            "seat_zip": "NN-NNN",
            "sales_zip": "NN-NNN",
            "slug": self.env._("lowercase letters, digits and dashes"),
            "website": self.env._("URL starting with http(s)://"),
        }
        patterns = {
            "krs": r"\d{10}",
            "nip": r"\d{10}",
            "regon": r"\d{9}|\d{14}",
            "phone": r"\+?\d{9,15}",
            "seat_zip": r"\d\d-\d{3}",
            "sales_zip": r"\d\d-\d{3}",
            "slug": SLUG_RE,
            "website": r"https?://\S+",
        }
        checks = [(f, patterns[f], hints[f]) for f in patterns]
        for dev in self:
            for fname, pattern, hint in checks:
                value = dev[fname]
                if fname in ("krs", "nip", "regon", "phone") and value:
                    value = re.sub(r"[\s-]", "", value)
                if value and not re.fullmatch(pattern, value):
                    raise ValidationError(
                        self.env._(
                            "%(field)s: wrong format (%(hint)s).",
                            field=dev._fields[fname].string,
                            hint=hint,
                        )
                    )
            nip = re.sub(r"[\s-]", "", dev.nip or "")
            if nip and not checksum_ok(nip, NIP_WEIGHTS):
                raise ValidationError(self.env._("NIP: wrong check digit."))
            regon = re.sub(r"[\s-]", "", dev.regon or "")
            if regon and not checksum_ok(regon, REGON_WEIGHTS[len(regon)]):
                raise ValidationError(self.env._("REGON: wrong check digit."))
            if dev.email and not re.fullmatch(EMAIL_RE, dev.email.strip()):
                raise ValidationError(self.env._("Email: wrong format."))
            if dev.dataset_url and not re.fullmatch(r"https?://\S+", dev.dataset_url):
                raise ValidationError(
                    self.env._("Price list page: URL starting with http(s)://.")
                )
            if not (dev.slug or "").strip():
                raise ValidationError(self.env._("The URL name is required."))
            check_no_formula(dev, DEVELOPER_FIELDS, skip=("phone",))
            if dev.slug in RESERVED_SLUGS:
                raise ValidationError(
                    self.env._("The URL name “%s” is reserved.", dev.slug)
                )

    def _hpr_lang(self):
        """Language of the stored check texts and activities: the responsible
        user's, else the company's. The cron has no language of its own."""
        self.ensure_one()
        return (
            self.responsible_id.lang
            or self.company_id.partner_id.lang
            or self.env.lang
            or "en_US"
        )

    def write(self, vals):
        if "active" in vals and not vals["active"]:
            self.project_ids._hpr_check_archive(vals)
        if "slug" in vals and self.filtered(
            lambda d: d.file_ids and d.slug != vals["slug"]
        ):
            raise UserError(
                self.env._(
                    "The URL name cannot change after the first file: "
                    "dane.gov.pl already has these addresses."
                )
            )
        if "company_id" in vals and self.filtered(
            lambda d: d.file_ids and d.company_id.id != vals["company_id"]
        ):
            raise UserError(
                self.env._(
                    "The company cannot change after the first file: the files "
                    "belong to it."
                )
            )
        if "ext_ident" in vals and self.filtered(
            lambda d: d.ext_ident != vals["ext_ident"]
        ):
            raise UserError(self.env._("The dataset identifier cannot change."))
        return super().write(vals)

    def action_fill_from_company(self):
        """Copy what Odoo already knows about the company. Nothing is approved."""
        for dev in self:
            partner = dev.company_id.partner_id
            vals = {
                "name": dev.name or dev.company_id.name,
                "nip": dev.nip or re.sub(r"^PL", "", partner.vat or "") or False,
                "phone": dev.phone or partner.phone or False,
                "email": dev.email or partner.email or False,
                "website": dev.website or partner.website or False,
                "seat_city": dev.seat_city or partner.city or False,
                "seat_street": dev.seat_street or partner.street or False,
                "seat_zip": dev.seat_zip or partner.zip or False,
                "seat_state": dev.seat_state or partner.state_id.name or False,
            }
            if "company_registry" in partner._fields and not dev.krs:
                vals["krs"] = partner.company_registry or False
            dev.write({k: v for k, v in vals.items() if v != dev[k]})
        return True

    def _hpr_developer_record(self, from_snapshot=True):
        """Fields A-C of the file: from the approved snapshot (default) or from the
        current values (check before approval)."""
        self.ensure_one()
        if from_snapshot:
            rec = dict(self._hpr_snapshot())
        else:
            rec = {k: self[k] for k in self._hpr_data_fields}
        rec["company_name"] = rec.pop("name", None)
        return rec

    def _hpr_check_before_approval(self):
        rec = self._hpr_developer_record(from_snapshot=False)
        missing = [
            sym
            for (key, sym, _n, _t, req) in structure.COLUMNS
            if req and sym[0] in "ABC" and not rec.get(key)
        ]
        if missing:
            return [self.env._("Missing required fields: %s", ", ".join(missing))]
        return []

    def _hpr_xml(self):
        """Harvester XML with all daily files so far (it keeps growing).

        Returns None when there is no file yet: the XSD needs at least one resource.
        The public routes call it on every request, so the result is cached under
        a key made of everything it is built from (daily files never change).
        """
        self.ensure_one()
        # sudo: public XML, only metadata of the daily files and approved A data
        dev = self.sudo()
        [(count, max_id)] = (
            self.env["l10n_pl.hpr.file"]
            .sudo()
            ._read_group(
                [("developer_id", "=", dev.id)], aggregates=["id:count", "id:max"]
            )
        )
        if not count:
            return None
        projects = dev.with_context(active_test=False).project_ids
        # everything the XML is built from; files never change, so count + max id
        key = (
            count,
            max_id,
            dev.ext_ident,
            dev.name,
            dev.dataset_url,
            dev.approved_snapshot,
            tuple(projects.sorted("id").mapped("approved_snapshot")),
        )
        return dev._hpr_xml_cached(key)

    @ormcache("self.id", "key")
    def _hpr_xml_cached(self, key):
        files = self.file_ids.sorted(lambda f: (f.day, f.project_id.id))
        resources = [
            {
                "ext_ident": f.ext_ident,
                "url": f.url,
                "day": fields.Date.to_string(f.day),
                # approved name only, like every other value that goes out
                "project": f.project_id._hpr_snapshot().get("name")
                or f.project_id.name,
            }
            for f in files
        ]
        snap = self._hpr_developer_record()
        return structure.harvester_xml(
            {
                "ext_ident": self.ext_ident,
                "company": snap.get("company_name") or self.name,
                "year": files[-1].day.year,
                "url": snap.get("dataset_url") or snap.get("website"),
            },
            resources,
        )

    @api.model
    def _cron_price_register(self):
        """Hourly: today's file (once, never changed, only with at least one unit),
        inconsistencies once a day and on change, alarm when there is no file in
        the morning. Every step runs in its own savepoint."""
        now = now_pl()
        for dev in self.search([("active", "=", True)]):
            # texts stored by the cron in the responsible user's language
            dev = dev.with_context(lang=dev._hpr_lang())
            for project in dev.project_ids.filtered("active"):
                prospectus = None
                if project._hpr_first_run_today():
                    # HTTP outside the savepoints: it only reads, writes come later
                    try:
                        prospectus = project._hpr_prospectus_issues()
                    except Exception:
                        _logger.exception(
                            "Housing price register: prospectus check failed for %s",
                            project.display_name,
                        )
                steps = (
                    lambda p=project: p._hpr_generate(now.date(), from_cron=True),
                    lambda p=project, pr=prospectus: p._hpr_notify_issues(
                        check_prospectus=False, prospectus=pr
                    ),
                    lambda p=project: p._hpr_alarm_missing_file(now),
                )
                for step in steps:
                    try:
                        with self.env.cr.savepoint():
                            step()
                    except Exception:
                        _logger.exception(
                            "Housing price register: error for %s", project.display_name
                        )
