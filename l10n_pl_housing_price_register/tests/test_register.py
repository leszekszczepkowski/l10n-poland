# Copyright 2026 Leszek Szczepkowski
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Price register: only approved data, nothing without an escrow account, files
never change, XML valid against the dane.gov.pl XSD, issues with the offer.

All data is fictitious."""

import base64
import csv
import io
import os
import socket
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytz

from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged

from ..models import hpr_stage, structure
from ..models.hpr_approval_mixin import today_pl
from .common import READER, HprCommon


@tagged("post_install", "-at_install")
class TestHousingPriceRegister(HprCommon, TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(
            context=dict(cls.env.context, hpr_no_http=True, tracking_disable=True)
        )
        cls._hpr_setup()

    def test_nothing_without_escrow(self):
        with self.assertRaises(ValidationError):
            self.b1.in_register = True
        with self.assertRaises(ValidationError):
            self.stage_b.write(
                {"sending_enabled": True, "prospectus_url": "https://example.com/b.pdf"}
            )
        self.a2.in_register = True
        with self.assertRaises(ValidationError):
            self.stage_a.escrow_open = False  # a unit in register keeps the account

    def test_only_approved_snapshots(self):
        self._ready()
        day = date(2026, 11, 5)
        records, _skipped = self.project._hpr_units_for_file(day)
        self.assertEqual(
            [(r["unit_number"], r["price"]) for r in records], [("A2", 598000.0)]
        )
        self.a2.price = 610000.0  # an unapproved change does not go out...
        self.assertEqual(self.a2.state, "draft")
        self.developer.phone = "000000002"  # ...nor a change of developer data
        self.assertFalse(self.developer.approved)
        records, _skipped = self.project._hpr_units_for_file(day)
        self.assertEqual(records[0]["price"], 598000.0)
        self.assertEqual(records[0]["phone"], "000-000-001")
        self.a2.date_from = date(2026, 12, 1)  # price approved in advance
        self._as_manager(self.a2).action_approve()
        self.assertEqual(self.project._hpr_units_for_file(day)[0][0]["price"], 598000.0)
        self.assertEqual(
            self.project._hpr_units_for_file(date(2026, 12, 1))[0][0]["price"],
            610000.0,
        )

    def test_approval_fields_protected(self):
        self.a2.write(
            {
                "in_register": True,
                "price": 598000.0,
                "date_from": date(2026, 10, 12),
                "state": "approved",
            }
        )
        self.assertEqual(self.a2.state, "draft", "state cannot be written")
        self.developer.write({"approved": True})
        self.assertFalse(self.developer.approved)

    def test_approval_cannot_be_forged_through_the_context(self):
        # an RPC client can send any context, but only JSON values
        forged = {"hpr_approving": True}
        self.developer.with_context(**forged).write(
            {"approved": True, "approved_snapshot": '{"name": "X"}'}
        )
        self.assertFalse(self.developer.approved)
        self.assertFalse(self.developer.approved_snapshot)
        self.a2.with_context(**forged).write({"state": "approved"})
        self.assertEqual(self.a2.state, "draft")
        stage = (
            self.env["l10n_pl.hpr.stage"]
            .with_context(**forged)
            .create({"name": "C", "project_id": self.project.id, "approved": True})
        )
        self.assertFalse(stage.approved)

    def test_same_values_keep_the_approval(self):
        self._ready()
        self.developer.write(
            {"phone": self.developer.phone, "name": self.developer.name}
        )
        self.assertTrue(self.developer.approved)
        self.a2.write({"price": 598000.0, "date_from": date(2026, 10, 12)})
        self.assertEqual(self.a2.state, "approved")
        self.a2.price = 598001.0
        self.assertEqual(self.a2.state, "draft")

    def test_stage_and_unit_cannot_move_after_prices(self):
        self._ready()
        other = self.env["l10n_pl.hpr.project"].create(
            {
                "name": "Inna inwestycja",
                "developer_id": self.developer.id,
                "slug": "inna",
            }
        )
        with self.assertRaises(UserError):
            self.stage_a.project_id = other
        with self.assertRaises(UserError):
            self.a2.stage_id = self.stage_b
        self.b1.stage_id = self.stage_a  # no approved price yet: allowed
        self.stage_b.project_id = other  # no unit with a price: allowed

    def test_latest_approval_wins_over_later_date(self):
        self._ready()
        manager_a2 = self._as_manager(self.a2)
        # planned price approved first, a correction approved later
        self.a2.write({"price": 500000.0, "date_from": date(2026, 12, 1)})
        manager_a2.action_approve()
        self.a2.write({"price": 470000.0, "date_from": date(2026, 11, 20)})
        manager_a2.action_approve()
        self.assertEqual(self.a2._hpr_price_on(date(2026, 11, 25)).price, 470000.0)
        self.assertEqual(
            self.a2._hpr_price_on(date(2026, 12, 5)).price,
            470000.0,
            "the file shows the price on the card, not the older plan",
        )
        self.assertEqual(self.a2._hpr_price_on(date(2026, 11, 10)).price, 598000.0)

    def test_checksums_email_and_negative_amounts(self):
        for fname, value in (
            ("nip", "1234567890"),
            ("regon", "123456789"),
            ("email", "biuro.example.com"),
        ):
            with self.assertRaises(ValidationError, msg=fname):
                self.developer[fname] = value
        self.developer.regon = "12345678512347"  # 14 digits, valid check digit
        with self.assertRaises(ValidationError):
            self.a1.price = -1.0
        with self.assertRaises(ValidationError):
            self.stage_a.part_price = -1.0
        with self.assertRaises(ValidationError):
            self.stage_a.prospectus_url = "https://example.com/a b.pdf"

    def test_stage_not_sending_with_units_in_register_is_an_issue(self):
        self._ready()
        self.stage_a.sending_enabled = False
        texts = [t for _r, t in self.project._hpr_issues()]
        self.assertTrue(
            any("sends no data" in t for t in texts), "a silent stop is reported"
        )

    def test_texts_in_the_responsible_users_language(self):
        lang = self.developer._hpr_lang()
        self.assertEqual(lang, self.manager.lang or self.env.lang or "en_US")

    def test_sold_unit_leaves_and_cron_makes_no_empty_file(self):
        self._ready()
        self.a2.offer_state = "sold"
        records, skipped = self.project._hpr_units_for_file(date(2026, 11, 5))
        self.assertFalse(records)
        self.assertEqual(skipped[0][0], self.a2)
        self.assertFalse(self.project._hpr_generate(date(2026, 11, 5), from_cron=True))
        self.assertFalse(self.developer._hpr_xml(), "no files, no XML")
        with self.assertRaises(UserError):
            self.project._hpr_generate(date(2026, 11, 5))

    def test_only_group_approves_and_no_missing_fields(self):
        reader = self._hpr_user("hpr_reader", READER)
        with self.assertRaises(UserError):
            self.a2.with_user(reader).action_approve()
        self.developer.sales_contact_method = False
        with self.assertRaises(UserError):
            self._as_manager(self.developer).action_approve()

    def test_file_never_changes_and_content(self):
        self._ready()
        new_file = self.project._hpr_generate(date(2026, 11, 5))
        with self.assertRaises(UserError):
            new_file.attachment_id.write({"raw": b"changed"})
        self.assertEqual(self.project._hpr_generate(date(2026, 11, 5)), new_file)
        self.assertEqual(
            new_file.name,
            "ceny-ofertowe-mieszkan-dewelopera-przykladowy-deweloper-"
            "zielone-wzgorze-2026-11-05.csv",
        )
        self.assertEqual(len(new_file.ext_ident), 36)
        with self.assertRaises(UserError):
            new_file.write({"record_count": 5})
        with self.assertRaises(UserError):
            new_file.unlink()
        content = new_file.attachment_id.raw.decode("utf-8")
        rows = list(csv.reader(io.StringIO(content), delimiter=";"))
        self.assertEqual(len(rows), 2)
        row = dict(zip(structure.KEYS, rows[1], strict=True))
        self.assertEqual(row["phone"], "000000001")
        self.assertEqual(row["property_type"], "lokal mieszkalny")
        self.assertEqual(row["price"], "598000.00")
        self.assertEqual(row["price_m2"], "9329.17")
        self.assertEqual(row["price_date"], "2026-10-12")
        self.assertEqual(row["part_price"], "5000.00")
        self.assertEqual(row["total_price"], "X")
        self.assertEqual(row["seat_flat_no"], "X")
        self.assertEqual(row["annex_type"], "X")
        self.assertEqual(row["prospectus_url"], "https://example.com/prospekt-a.pdf")
        self.assertNotIn("A1", [r[structure.KEYS.index("unit_number")] for r in rows])

    def test_xml_valid_against_xsd(self):
        from lxml import etree

        self._ready()
        self.project._hpr_generate(date(2026, 11, 5))
        self.project._hpr_generate(date(2026, 11, 6))
        xml = self.developer._hpr_xml()
        # harvester schema 1.13 as published by dane.gov.pl (public data portal of
        # the Polish government), https://dane.gov.pl/static/xml/otwarte_dane_latest.xsd
        xsd_path = os.path.join(
            os.path.dirname(__file__), "data", "otwarte_dane_latest.xsd"
        )
        with open(xsd_path, "rb") as xsd_file:
            xsd = etree.XMLSchema(etree.parse(xsd_file))
        doc = etree.fromstring(xml)
        xsd.assertValid(doc)
        self.assertEqual(len(doc.findall(".//resource")), 2, "XML keeps growing")
        self.assertEqual(doc.find(".//dataset/extIdent").text, self.developer.ext_ident)
        self.assertTrue(
            doc.find(".//resource/url").text.startswith(
                "https://example.com/rejestr-cen/przykladowy-deweloper/"
            )
        )

    def test_price_history_append_only(self):
        self._ready()
        history = self.a2.price_ids
        self.assertEqual(len(history), 1)
        with self.assertRaises(UserError):
            history.write({"price": 1.0})
        with self.assertRaises(UserError):
            history.unlink()

    def test_issues_and_one_activity(self):
        self._ready()
        self.a2.product_id.list_price = 610000.0  # offer differs from register
        texts = [t for _r, t in self.project._hpr_issues()]
        self.assertTrue(any(t.startswith("A2") and "≠" in t for t in texts))
        self.assertTrue(any(t.startswith("A1") for t in texts), "A1 on offer, not sent")
        self.project._hpr_notify_issues()
        activities = self.project.activity_ids.filtered(
            lambda a: a.user_id == self.manager
        )
        self.assertEqual(len(activities), 1)
        self.a2.product_id.list_price = 620000.0  # new set, still one activity
        self.project._hpr_notify_issues()
        activities = self.project.activity_ids.filtered(
            lambda a: a.user_id == self.manager
        )
        self.assertEqual(len(activities), 1)
        self.assertTrue(self.a2.issue)

    def test_extra_columns_g_h_i(self):
        self._ready()
        self.stage_a.write(
            {
                "rights_desc": "służebność przejazdu",
                "rights_value": 1000.0,
                "rights_date": date(2026, 8, 1),
            }
        )
        self._as_manager(self.stage_a).action_approve()
        self.a2.write(
            {
                "annex_type": "komórka lokatorska",
                "annex_ref": "K2",
                "annex_price": 15000.0,
                "annex_date": date(2026, 10, 12),
            }
        )
        self._as_manager(self.a2).action_approve()
        rec = self.project._hpr_units_for_file(date(2026, 11, 5))[0][0]
        row = dict(zip(structure.KEYS, structure.row(rec), strict=True))
        self.assertEqual(row["annex_ref"], "K2")
        self.assertEqual(row["annex_price"], "15000.00")
        self.assertEqual(row["rights_value"], "1000.00")
        self.assertEqual(row["other_desc"], "X")

    def test_fill_from_company(self):
        developer = self.env["l10n_pl.hpr.developer"].create(
            {
                "name": "Inna",
                "slug": "inna",
                "company_id": self.env["res.company"]
                .create({"name": "Inna spółka", "zip": "00-000", "city": "Przykładowo"})
                .id,
            }
        )
        developer.action_fill_from_company()
        self.assertEqual(developer.seat_city, "Przykładowo")
        self.assertEqual(developer.seat_zip, "00-000")
        self.assertFalse(developer.approved)

    # ---- fixes after review --------------------------------------------------
    def test_base_url_must_be_public_https(self):
        params = self.env["ir.config_parameter"].sudo()
        self._ready()
        day = date(2026, 11, 5)
        params.set_param("l10n_pl_housing_price_register.base_url", False)
        params.set_param("web.base.url", "https://erp.example.com")
        params.set_param("web.base.url.freeze", False)
        with self.assertRaises(UserError):  # unfrozen web.base.url is not used
            self.project._hpr_generate(day)
        self.assertFalse(self.project._hpr_generate(day, from_cron=True))
        self.assertTrue(
            any("base_url" in t for _r, t in self.project._hpr_issues()),
            "the issue list says why no file is made",
        )
        for wrong in ("http://example.com", "https://localhost:8069"):
            params.set_param("l10n_pl_housing_price_register.base_url", wrong)
            with self.assertRaises(UserError):
                self.project._hpr_generate(day)
        params.set_param("l10n_pl_housing_price_register.base_url", False)
        params.set_param("web.base.url.freeze", True)
        new_file = self.project._hpr_generate(day)
        self.assertTrue(new_file.url.startswith("https://erp.example.com/rejestr-cen/"))

    def test_developer_and_company_locked_after_first_file(self):
        self._ready()
        other = self.env["l10n_pl.hpr.developer"].create(
            {
                "name": "Inny",
                "slug": "inny",
                "company_id": self.env["res.company"]
                .create({"name": "Inna spółka"})
                .id,
            }
        )
        self.project.developer_id = other  # no file yet: allowed
        self.project.developer_id = self.developer
        self.project._hpr_generate(date(2026, 11, 5))
        with self.assertRaises(UserError):
            self.project.developer_id = other
        with self.assertRaises(UserError):
            self.developer.company_id = other.company_id
        self.project.write({"developer_id": self.developer.id})  # same value: fine

    def test_reader_changes_nothing(self):
        reader = self._hpr_user("hpr_reader2", READER)
        self.assertTrue(self.project.with_user(reader).name)
        with self.assertRaises(AccessError):
            self.project.with_user(reader).write({"name": "X"})
        with self.assertRaises(AccessError):
            self.a1.with_user(reader).write({"price": 1.0})
        with self.assertRaises(UserError):
            self.project.with_user(reader).action_check_issues()
        with self.assertRaises(UserError):
            self.project.with_user(reader).action_generate_today()

    def test_text_that_looks_like_a_formula_is_refused(self):
        for record, fname in (
            (self.a1, "name"),
            (self.a1, "annex_type"),
            (self.stage_a, "part_type"),
            (self.project, "site_street"),
            (self.developer, "seat_street"),
        ):
            for value in (
                "=HYPERLINK(1)",
                "@SUM(1)",
                "-1+1",
                " +1",
                "\u00a0=1+1",
                "\n=1+1",
            ):
                with self.assertRaises(ValidationError, msg=f"{fname} {value}"):
                    record[fname] = value
        self.developer.phone = "+48000000001"  # a phone number may start with +

    def test_prospectus_check_contacts_public_https_only(self):
        stage = self.stage_a
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443))]
        private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))]

        def answer(code, location=None):
            response = MagicMock(status_code=code, headers={"Location": location})
            response.__enter__.return_value = response
            return response

        with (
            patch.object(hpr_stage.socket, "getaddrinfo", return_value=private),
            patch.object(hpr_stage.requests, "head") as head,
        ):
            self.assertIsInstance(stage._hpr_prospectus_http_code(), str)
            head.assert_not_called()
        with (
            patch.object(hpr_stage.socket, "getaddrinfo", return_value=public),
            patch.object(hpr_stage.requests, "head", return_value=answer(200)),
        ):
            self.assertEqual(stage._hpr_prospectus_http_code(), 200)
        with (
            patch.object(hpr_stage.socket, "getaddrinfo", return_value=public),
            patch.object(
                hpr_stage.requests,
                "head",
                return_value=answer(302, "http://169.254.169.254/latest"),
            ) as head,
        ):
            self.assertIsInstance(stage._hpr_prospectus_http_code(), str)
            self.assertEqual(head.call_count, 1, "redirect to http is not followed")
        with (
            patch.object(hpr_stage.socket, "getaddrinfo", return_value=public),
            patch.object(hpr_stage.requests, "head", return_value=answer(405)),
            patch.object(hpr_stage.requests, "get", return_value=answer(200)) as get,
        ):
            self.assertEqual(stage._hpr_prospectus_http_code(), 200)
            get.assert_called_once()

    def test_attachment_same_content_can_be_rewritten(self):
        self._ready()
        attachment = self.project._hpr_generate(date(2026, 11, 5)).attachment_id
        raw = attachment.raw
        attachment.write({"raw": raw})  # what force_storage() does
        attachment.write({"datas": base64.b64encode(raw)})
        for vals in (
            {"raw": raw + b"x"},
            {"datas": base64.b64encode(b"changed")},
            {"db_datas": b"changed"},
        ):
            with self.assertRaises(UserError):
                attachment.write(vals)
        self.assertEqual(attachment.raw, raw)

    def test_issues_compare_with_price_valid_today(self):
        self._ready()
        self.a2.write({"price": 650000.0, "date_from": date(2099, 1, 1)})
        self._as_manager(self.a2).action_approve()
        texts = [t for _r, t in self.project._hpr_issues() if t.startswith("A2")]
        self.assertFalse(
            [t for t in texts if "≠" in t],
            "a price approved in advance gives no false alarm",
        )

    def test_archived_developer_xml_and_cache(self):
        self._ready()
        self.project._hpr_generate(date(2026, 11, 5))
        xml = self.developer._hpr_xml()
        self.assertEqual(self.developer._hpr_xml(), xml)
        self.project._hpr_generate(date(2026, 11, 6))
        self.assertNotEqual(self.developer._hpr_xml(), xml, "new file, new XML")
        self.project.name = "Zielone Wzgórze II"
        self.assertNotIn(
            "Zielone Wzgórze II".encode(),
            self.developer._hpr_xml(),
            "an unapproved project name does not go out",
        )
        self._as_manager(self.project).action_approve()
        self.assertIn("Zielone Wzgórze II".encode(), self.developer._hpr_xml())
        with self.assertRaises(UserError, msg="a sending project is not archived"):
            self.developer.active = False
        self.stage_a.sending_enabled = False
        self.developer.active = False
        self.assertTrue(self.developer._hpr_xml())

    def test_empty_slug_refused(self):
        with self.assertRaises(ValidationError):
            self.project.slug = " "
        with self.assertRaises(ValidationError):
            self.developer.slug = " "

    def test_cron_and_morning_alarm(self):
        self._ready()
        xmlid = "l10n_pl_housing_price_register.mail_activity_type_hpr_missing_file"
        alarm = self.env.ref(xmlid)
        morning = pytz.timezone("Europe/Warsaw").localize(datetime(2026, 11, 5, 7, 0))

        def alarms():
            return self.project.activity_ids.filtered(
                lambda a: a.activity_type_id == alarm
            )

        self.project._hpr_alarm_missing_file(morning.replace(hour=5))
        self.assertFalse(alarms(), "no alarm before 6:00")
        self.project._hpr_alarm_missing_file(morning)
        self.assertEqual(len(alarms()), 1)
        self.assertEqual(alarms().user_id, self.manager)
        alarms().action_feedback()  # marked done
        self.project._hpr_alarm_missing_file(morning.replace(hour=8))
        self.assertFalse(alarms(), "at most one alarm a day")
        next_day = morning.replace(day=6)
        self.project._hpr_alarm_missing_file(next_day)
        self.assertIn("06.11.2026", alarms().summary, "today's date in the title")
        self.project._hpr_generate(next_day.date())
        self.project._hpr_alarm_missing_file(next_day)
        self.assertFalse(alarms(), "the file is there, the alarm goes away")
        with patch.object(
            type(self.stage_a), "_hpr_prospectus_http_code", return_value=200
        ):
            self.env["l10n_pl.hpr.developer"]._cron_price_register()
        self.assertIn(
            today_pl(),
            self.project.file_ids.mapped("day"),
            "the cron made today's file",
        )
        self.assertTrue(self.project.issues_checked_on)
        self.assertEqual(self.project.prospectus_issues, "[]")
