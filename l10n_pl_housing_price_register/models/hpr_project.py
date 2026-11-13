# Copyright 2026 Leszek Szczepkowski
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import json
import logging
import re

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from . import structure
from .hpr_approval_mixin import check_manager, check_no_formula, today_pl
from .hpr_developer import SLUG_RE

_logger = logging.getLogger(__name__)
PRICE_TOLERANCE = 1.0  # PLN; net to gross rounding gives a few grosze
ALARM_HOUR = 6  # after this hour (Polish time) a missing file for today = activity
PROJECT_FIELDS = (
    "name",
    "site_state",
    "site_county",
    "site_municipality",
    "site_city",
    "site_street",
    "site_house_no",
    "site_zip",
)


class HprProject(models.Model):
    _name = "l10n_pl.hpr.project"
    _description = "Housing price register - development project"
    _inherit = ["mail.thread", "mail.activity.mixin", "l10n_pl.hpr.approval.mixin"]
    _order = "sequence, name"
    _hpr_data_fields = PROJECT_FIELDS

    name = fields.Char(required=True, tracking=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    developer_id = fields.Many2one(
        "l10n_pl.hpr.developer",
        string="Developer",
        required=True,
        ondelete="restrict",
        index=True,
    )
    company_id = fields.Many2one(
        related="developer_id.company_id", store=True, index=True
    )
    slug = fields.Char(
        "URL name",
        required=True,
        tracking=True,
        help="Part of the file name, e.g. phase-2. "
        "Do not change it after the first file.",
    )
    site_state = fields.Char("Voivodeship (D1)", tracking=True)
    site_county = fields.Char("County (D2)", tracking=True)
    site_municipality = fields.Char("Municipality (D3)", tracking=True)
    site_city = fields.Char("City (D4)", tracking=True)
    site_street = fields.Char("Street (D5)", tracking=True)
    site_house_no = fields.Char(
        "Building number (D6)", tracking=True, help="Only if a number was assigned."
    )
    site_zip = fields.Char("Postal code (D7)", tracking=True)

    stage_ids = fields.One2many("l10n_pl.hpr.stage", "project_id", "Stages")
    unit_ids = fields.One2many("l10n_pl.hpr.unit", "project_id", "Units")
    file_ids = fields.One2many("l10n_pl.hpr.file", "project_id", "Daily files")
    sending = fields.Boolean("Data is sent", compute="_compute_sending")
    units_in_file = fields.Integer("Units in today's file", compute="_compute_sending")
    last_file_id = fields.Many2one(
        "l10n_pl.hpr.file", string="Last file", compute="_compute_sending"
    )
    issues = fields.Html(readonly=True, copy=False, sanitize=True)
    issues_key = fields.Char(readonly=True, copy=False)
    issue_count = fields.Integer("Number of issues", readonly=True, copy=False)
    issues_checked_on = fields.Datetime("Issues checked on", readonly=True, copy=False)
    prospectus_checked_on = fields.Date(
        "Prospectuses checked on", readonly=True, copy=False
    )
    prospectus_issues = fields.Text(readonly=True, copy=False)
    missing_file_alarm_on = fields.Date(
        "Missing file alarm given on", readonly=True, copy=False
    )
    preview = fields.Html(
        "Today's file preview", compute="_compute_preview", sanitize=False
    )

    _slug_uniq = models.Constraint(
        "unique(developer_id, slug)",
        "This URL name is already taken for this developer.",
    )

    def _hpr_check_archive(self, vals):
        """A project that sends data cannot be archived by a stray click: the cron
        skips archived projects and the daily publication would stop silently."""
        if (
            "active" in vals
            and not vals["active"]
            and self.stage_ids.filtered("sending_active")
        ):
            raise UserError(
                self.env._(
                    "Turn off sending on the stages first: an archived project "
                    "gets no daily file and no alarm."
                )
            )

    def write(self, vals):
        self._hpr_check_archive(vals)
        if "slug" in vals and self.filtered(
            lambda p: p.file_ids and p.slug != vals["slug"]
        ):
            raise UserError(
                self.env._("The URL name cannot change after the first file.")
            )
        if "developer_id" in vals and self.filtered(
            lambda p: p.file_ids and p.developer_id.id != vals["developer_id"]
        ):
            raise UserError(
                self.env._(
                    "The developer cannot change after the first file: the file "
                    "addresses contain its URL name."
                )
            )
        return super().write(vals)

    @api.constrains("slug", *PROJECT_FIELDS)
    def _check_formats(self):
        for project in self:
            check_no_formula(project, PROJECT_FIELDS)
            if not re.fullmatch(SLUG_RE, project.slug or ""):
                raise ValidationError(
                    self.env._("URL name: lowercase letters, digits and dashes only.")
                )
            if project.site_zip and not re.fullmatch(r"\d\d-\d{3}", project.site_zip):
                raise ValidationError(self.env._("Project postal code: format NN-NNN."))

    def _compute_sending(self):
        day = today_pl()
        for project in self:
            project.sending = any(project.stage_ids.mapped("sending_active"))
            project.units_in_file = len(project._hpr_units_for_file(day)[0])
            project.last_file_id = project.file_ids.sorted("day", reverse=True)[:1]

    def _hpr_check_before_approval(self):
        rec = {k: self[k] for k in self._hpr_data_fields}
        missing = [
            sym
            for (key, sym, _n, _t, req) in structure.COLUMNS
            if req and sym[0] == "D" and not rec.get(key)
        ]
        if missing:
            return [self.env._("Missing required fields: %s", ", ".join(missing))]
        return []

    # ---- daily file -------------------------------------------------------------
    def _hpr_units_for_file(self, day):
        """Return (records, [(unit, reason)]).

        Only approved snapshots are used; the price is the last approved price
        valid on that day.
        """
        self.ensure_one()
        records, skipped = [], []
        developer = self.developer_id._hpr_developer_record()
        project = self._hpr_snapshot()
        base = {}
        if developer.get("company_name") and project:
            base = dict(developer, **{k: v for k, v in project.items() if k != "name"})
        units = self.unit_ids.filtered("in_register").sorted(
            lambda u: (u.stage_id.sequence, u.name or "")
        )
        for unit in units:
            stage = unit.stage_id
            stage_snap = stage._hpr_snapshot()
            price = unit._hpr_price_on(day)
            if not stage.sending_active:
                skipped.append(
                    (
                        unit,
                        self.env._(
                            "stage “%(stage)s”: %(reason)s",
                            stage=stage.name,
                            reason=stage.sending_reason,
                        ),
                    )
                )
            elif (
                not stage_snap
                or not stage_snap.get("escrow_open")
                or not stage_snap.get("prospectus_url")
            ):
                skipped.append(
                    (
                        unit,
                        self.env._(
                            "stage “%s”: escrow account and prospectus never approved",
                            stage.name,
                        ),
                    )
                )
            elif unit._hpr_is_sold():
                skipped.append((unit, self.env._("sold, no longer on offer")))
            elif not price:
                skipped.append(
                    (
                        unit,
                        self.env._(
                            "no approved price valid on %s",
                            fields.Date.to_string(day),
                        ),
                    )
                )
            elif not base:
                skipped.append(
                    (
                        unit,
                        self.env._("developer or project data has never been approved"),
                    )
                )
            else:
                rec = dict(base, **price._hpr_record(stage_snap))
                missing = structure.missing(rec)
                if missing:
                    skipped.append(
                        (
                            unit,
                            self.env._(
                                "missing required fields: %s", ", ".join(missing)
                            ),
                        )
                    )
                else:
                    records.append(rec)
        return records, skipped

    def _compute_preview(self):
        day = today_pl()
        for project in self:
            records, skipped = project._hpr_units_for_file(day)
            rows = Markup("").join(
                Markup(
                    "<tr><td>%s</td><td>%s</td><td class='text-end'>%s</td>"
                    "<td class='text-end'>%s</td><td>%s</td>"
                    "<td><a href='%s' target='_blank'>%s</a></td></tr>"
                )
                % (
                    r["unit_number"],
                    r["property_type"],
                    structure.amount(r["price"]),
                    structure.amount(r["price_m2"]),
                    r["price_date"],
                    r["prospectus_url"],
                    self.env._("prospectus"),
                )
                for r in records
            )
            html = Markup(
                "<p><b>%s</b></p><table class='table table-sm'><thead><tr>"
                "<th>%s</th><th>%s</th><th class='text-end'>%s</th>"
                "<th class='text-end'>%s</th><th>%s</th><th></th></tr></thead>"
                "<tbody>%s</tbody></table>"
            ) % (
                self.env._(
                    "Today's file contains %s units (approved data):", len(records)
                ),
                self.env._("Unit"),
                self.env._("Type"),
                self.env._("Price"),
                self.env._("Price per m²"),
                self.env._("Valid from"),
                rows,
            )
            if skipped:
                html += Markup("<p><b>%s</b></p><ul>%s</ul>") % (
                    self.env._("Not included (marked “in register”):"),
                    Markup("").join(
                        Markup("<li>%s — %s</li>") % (u.name, reason)
                        for u, reason in skipped
                    ),
                )
            project.preview = html

    def _hpr_generate(self, day, from_cron=False):
        """File for a given day: created once, never changed.

        The cron does not create a file without units (it would freeze an empty
        day); the morning alarm reports it instead.
        """
        self.ensure_one()
        # files and attachments are created by the module only; users can read them
        File = self.env["l10n_pl.hpr.file"].sudo()
        existing = File.search(
            [("project_id", "=", self.id), ("day", "=", day)], limit=1
        )
        if existing:
            return existing
        records, skipped = self._hpr_units_for_file(day)
        developer = self.developer_id
        url_issue = developer._hpr_base_url_issue()
        if url_issue:
            if from_cron:
                return File  # the morning alarm reports it
            raise UserError(url_issue)
        if not self.sending:
            if from_cron:
                return File
            raise UserError(
                self.env._(
                    "No stage of this project sends data (escrow account, "
                    "prospectus and the switch are all needed)."
                )
            )
        if not records:
            if from_cron:
                return File
            raise UserError(
                self.env._(
                    "No unit goes to the file:\n%s",
                    "\n".join(f"{u.name} — {reason}" for u, reason in skipped) or "-",
                )
            )
        data = structure.csv_bytes(records)
        day_str = fields.Date.to_string(day)
        name = structure.file_name(developer.slug, self.slug, day_str)
        attachment = (
            self.env["ir.attachment"]
            .sudo()
            .create(
                {
                    "name": name,
                    "raw": data,
                    "mimetype": "text/csv",
                    "res_model": "l10n_pl.hpr.file",
                    "res_id": 0,
                    "company_id": self.company_id.id,
                }
            )
        )
        units = ", ".join(
            f"{r['unit_number']} {structure.amount(r['price'])}" for r in records
        )
        new_file = File.create(
            {
                "project_id": self.id,
                "day": day,
                "name": name,
                "attachment_id": attachment.id,
                "md5": structure.md5(data),
                "record_count": len(records),
                "structure_version": structure.STRUCTURE_VERSION,
                "url": (
                    f"{developer._hpr_base_url()}/rejestr-cen/{developer.slug}/{name}"
                ),
                "ext_ident": structure.resource_ext_ident(
                    developer.ext_ident, self.slug, day_str
                ),
                "units": units,
            }
        )
        attachment.write({"res_id": new_file.id})
        self.sudo().message_post(
            body=self.env._(
                "Price register file for %(day)s: %(count)s units (%(units)s).",
                day=day_str,
                count=len(records),
                units=units or "-",
            ),
            subtype_xmlid="mail.mt_note",
        )
        return new_file

    def action_generate_today(self):
        self.ensure_one()
        check_manager(self.env)
        new_file = self._hpr_generate(today_pl())
        return {
            "type": "ir.actions.act_window",
            "res_model": "l10n_pl.hpr.file",
            "res_id": new_file.id,
            "view_mode": "form",
        }

    def action_preview_csv(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": f"/rejestr-cen/preview/{self.id}.csv",
            "target": "new",
        }

    # ---- issues and alarms ----------------------------------------------------
    def _hpr_first_run_today(self):
        return self.prospectus_checked_on != today_pl()

    def _hpr_issues(self):
        """[(unit or stage or False, text)]: what differs between offer and register.

        The prospectus HTTP check is separate (`_hpr_prospectus_issues`), it runs
        once a day.
        """
        self.ensure_one()
        out = []
        url_issue = self.sending and self.developer_id._hpr_base_url_issue()
        if url_issue:
            out.append((False, url_issue))
        if not self.developer_id.approved:
            out.append(
                (
                    False,
                    self.env._(
                        "Developer data (A-C) changed or not approved; the file uses "
                        "the last approved version."
                    ),
                )
            )
        if not self.approved:
            out.append(
                (
                    False,
                    self.env._(
                        "Project data (D) changed or not approved; the file uses "
                        "the last approved version."
                    ),
                )
            )
        for stage in self.stage_ids:
            in_register = stage.unit_ids.filtered("in_register")
            if in_register and not stage.sending_active:
                out.append(
                    (
                        stage,
                        self.env._(
                            "Stage “%(stage)s”: %(count)s units are marked “in "
                            "register”, but the stage sends no data (%(reason)s).",
                            stage=stage.name,
                            count=len(in_register),
                            reason=stage.sending_reason,
                        ),
                    )
                )
            if stage.sending_active and not stage.approved:
                out.append(
                    (
                        stage,
                        self.env._(
                            "Stage “%s”: data changed, waiting for approval.",
                            stage.name,
                        ),
                    )
                )
        for unit in self.unit_ids:
            out += [(unit, text) for text in unit._hpr_issues()]
        return out

    def _hpr_prospectus_issues(self):
        self.ensure_one()
        out = []
        for stage in self.stage_ids.filtered("sending_active"):
            code = stage._hpr_prospectus_http_code()
            if code != 200:
                out.append(
                    (
                        stage,
                        self.env._(
                            "Stage “%(stage)s”: prospectus does not respond "
                            "(%(code)s) - %(url)s",
                            stage=stage.name,
                            code=f"HTTP {code}" if isinstance(code, int) else code,
                            url=stage.prospectus_url,
                        ),
                    )
                )
        return out

    def _hpr_notify_issues(self, check_prospectus=True, prospectus=None):
        """Store the issues; a new set gives one activity to the responsible user.

        `prospectus` are results of `_hpr_prospectus_issues` made earlier (the
        cron runs the HTTP check outside its savepoints). Without a new check the
        last prospectus results are kept, so they do not blink every hour. Only
        the module's own activities are removed.
        """
        self.ensure_one()
        issues = self._hpr_issues()
        if prospectus is None and (
            check_prospectus and not self.env.context.get("hpr_no_http")
        ):
            prospectus = self._hpr_prospectus_issues()
        if prospectus is not None:
            # sudo: check results are stored for readers too; only the module writes
            self.sudo().write(
                {
                    "prospectus_checked_on": today_pl(),
                    "prospectus_issues": json.dumps(
                        [t for _r, t in prospectus], ensure_ascii=False
                    ),
                }
            )
        else:
            prospectus = [
                (False, t) for t in json.loads(self.prospectus_issues or "[]")
            ]
        issues += prospectus
        texts = [t for _r, t in issues]
        key = json.dumps(texts, ensure_ascii=False)
        for unit in self.unit_ids:
            new = "\n".join(t for r, t in issues if r == unit) or False
            if unit.issue != new:
                unit.sudo().issue = new  # sudo: stored check result, not user data
        changed = key != (self.issues_key or "[]")
        # sudo: the cron and readers refresh the stored check, not user data
        html = (
            Markup("<ul>%s</ul>")
            % Markup("").join(Markup("<li>%s</li>") % t for t in texts)
            if texts
            else False
        )
        self.sudo().write(
            {
                "issues": html,
                "issue_count": len(texts),
                "issues_key": key,
                "issues_checked_on": fields.Datetime.now(),
            }
        )
        if changed:
            self._hpr_activity(
                "l10n_pl_housing_price_register.mail_activity_type_hpr_issue",
                html if texts else None,
                self.env._("Price register: %s issues to check", len(texts)),
            )
        return issues

    def _hpr_activity(self, type_xmlid, html, summary):
        """Replace the module's own activity of this type; never touch others."""
        activity_type = self.env.ref(type_xmlid)
        # sudo: the cron user may not see activities of other users
        self.env["mail.activity"].sudo().search(
            [
                ("res_model", "=", self._name),
                ("res_id", "=", self.id),
                ("activity_type_id", "=", activity_type.id),
            ]
        ).unlink()
        user = self.developer_id.responsible_id
        if html and user and user.active:
            try:
                with self.env.cr.savepoint():
                    self.sudo().activity_schedule(
                        activity_type_id=activity_type.id,
                        date_deadline=today_pl(),
                        summary=summary,
                        note=html,
                        user_id=user.id,
                    )
            except (AccessError, UserError):
                _logger.warning(
                    "Housing price register: cannot give activity %s to %s",
                    summary,
                    user.login,
                )

    def _hpr_alarm_missing_file(self, now):
        """After 6:00 Polish time a sending project without today's file gets an
        activity with the reasons."""
        self.ensure_one()
        xmlid = "l10n_pl_housing_price_register.mail_activity_type_hpr_missing_file"
        if now.hour < ALARM_HOUR:
            return
        # a project that published before and still has units marked “in
        # register” also gets the alarm when its stages stopped sending
        stopped = self.file_ids and self.unit_ids.filtered("in_register")
        if not (self.sending or stopped):
            return
        if self.file_ids.filtered(lambda f: f.day == now.date()):
            self._hpr_activity(xmlid, None, "")
            return
        if self.missing_file_alarm_on == now.date():
            return  # at most one alarm a day, also after it was marked done
        _records, skipped = self._hpr_units_for_file(now.date())
        reasons = Markup("").join(
            Markup("<li>%s — %s</li>") % (u.name, reason) for u, reason in skipped
        ) or Markup("<li>%s</li>") % self.env._("no units")
        url_issue = self.developer_id._hpr_base_url_issue()
        if url_issue:
            reasons = Markup("<li>%s</li>") % url_issue + reasons
        html = Markup("<p>%s</p><ul>%s</ul>") % (
            self.env._(
                "No price register file was created today, so dane.gov.pl gets "
                "no data. Reasons:"
            ),
            reasons,
        )
        # replaces yesterday's alarm, so the title always shows today's date
        self._hpr_activity(
            xmlid,
            html,
            self.env._("Price register: no file for %s", now.strftime("%d.%m.%Y")),
        )
        self.sudo().missing_file_alarm_on = now.date()  # sudo: module bookkeeping

    def action_check_issues(self):
        check_manager(self.env)
        for project in self:
            # the language of the responsible user, as in the cron: the stored
            # texts and the change detection do not depend on who clicked
            project.with_context(
                lang=project.developer_id._hpr_lang()
            )._hpr_notify_issues()
        return True
