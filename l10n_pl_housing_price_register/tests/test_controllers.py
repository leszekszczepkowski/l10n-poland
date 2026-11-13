# Copyright 2026 Leszek Szczepkowski
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Public addresses for dane.gov.pl and the preview for logged-in users."""

from datetime import date

from odoo.tests import HttpCase, tagged

from ..models import structure
from .common import READER, HprCommon


@tagged("post_install", "-at_install")
class TestHousingPriceRegisterRoutes(HprCommon, HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(
            context=dict(cls.env.context, hpr_no_http=True, tracking_disable=True)
        )
        cls._hpr_setup()
        cls._ready()
        cls.file = cls.project._hpr_generate(date(2026, 11, 5))
        cls.base = f"/rejestr-cen/{cls.developer.slug}"

    def test_public_files(self):
        xml = self.url_open(f"{self.base}/dane.xml")
        self.assertEqual(xml.status_code, 200)
        self.assertTrue(xml.headers["Content-Type"].startswith("application/xml"))
        self.assertEqual(xml.headers["X-Content-Type-Options"], "nosniff")
        md5 = self.url_open(f"{self.base}/dane.md5")
        self.assertEqual(md5.text, structure.md5(xml.content))
        csv = self.url_open(f"{self.base}/{self.file.name}")
        self.assertEqual(csv.status_code, 200)
        self.assertEqual(csv.content, self.file.attachment_id.raw)
        self.assertTrue(csv.headers["Content-Type"].startswith("text/csv"))
        latest = self.url_open(f"{self.base}/najnowszy-{self.project.slug}.csv")
        self.assertEqual(latest.content, csv.content)
        again = self.url_open(
            f"{self.base}/{self.file.name}",
            headers={"If-None-Match": csv.headers["ETag"]},
        )
        self.assertEqual(again.status_code, 304)

    def test_not_found(self):
        for path in (
            "/rejestr-cen/nobody/dane.xml",
            f"{self.base}/other-file.csv",
            f"{self.base}/najnowszy-nothing.csv",
            f"{self.base}/../dane.xml",
        ):
            self.assertEqual(self.url_open(path).status_code, 404, path)
        empty = self.env["l10n_pl.hpr.developer"].create(
            {
                "name": "Pusty",
                "slug": "pusty",
                "company_id": self.env["res.company"]
                .create({"name": "Pusta spółka"})
                .id,
            }
        )
        self.assertEqual(
            self.url_open(f"/rejestr-cen/{empty.slug}/dane.xml").status_code, 404
        )

    def test_archived_developer_keeps_addresses(self):
        self.stage_a.sending_enabled = False  # archiving needs sending off
        self.developer.active = False
        self.assertEqual(self.url_open(f"{self.base}/dane.xml").status_code, 200)
        self.assertEqual(
            self.url_open(f"{self.base}/{self.file.name}").status_code, 200
        )

    def test_preview_needs_the_group(self):
        path = f"/rejestr-cen/preview/{self.project.id}.csv"
        self._hpr_user("hpr_reader_web", READER)
        self._hpr_user("hpr_nobody_web", None)
        self.authenticate("hpr_nobody_web", "hpr_nobody_web-Password1")
        self.assertEqual(self.url_open(path).status_code, 404)
        self.authenticate("hpr_reader_web", "hpr_reader_web-Password1")
        preview = self.url_open(path)
        self.assertEqual(preview.status_code, 200)
        self.assertIn(b"A2", preview.content)
