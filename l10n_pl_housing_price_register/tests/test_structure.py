# Copyright 2026 Leszek Szczepkowski
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
import csv
import io
from datetime import date

from odoo.tests import BaseCase

from ..models import structure


class TestStructure(BaseCase):
    def test_57_columns_and_header(self):
        self.assertEqual(len(structure.COLUMNS), 57)
        groups = [c[1][0] for c in structure.COLUMNS]
        self.assertEqual(
            [groups.count(g) for g in "ABCDEFGHIJ"], [9, 8, 10, 7, 8, 4, 4, 3, 3, 1]
        )
        data = structure.csv_bytes([])
        self.assertFalse(data.startswith(b"\xef\xbb\xbf"), "no BOM")
        header = next(csv.reader(io.StringIO(data.decode("utf-8")), delimiter=";"))
        self.assertEqual(len(header), 57)
        self.assertEqual(header[0], "Firma")
        self.assertTrue(header[-1].startswith("Adres strony internetowej, pod którym"))
        self.assertTrue(data.endswith(b"\r\n"))

    def test_cells(self):
        self.assertEqual(structure.cell("price", "n", 779000), "779000.00")
        self.assertEqual(structure.cell("price", "n", None), "X")
        self.assertEqual(
            structure.cell("price_date", "d", date(2026, 11, 5)), "2026-11-05"
        )
        self.assertEqual(
            structure.cell("phone", "t", "+48 000-000-000"), "+48000000000"
        )
        self.assertEqual(structure.cell("company_name", "t", " A  B "), "A B")

    def test_missing_and_ids(self):
        self.assertIn("A1", structure.missing({}))
        self.assertEqual(
            structure.slugify("Zielone Wzgórze – etap Ł"), "zielone-wzgorze-etap-l"
        )
        a = structure.resource_ext_ident("x", "phase-1", "2026-11-05")
        self.assertEqual(len(a), 36)
        self.assertEqual(a, structure.resource_ext_ident("x", "phase-1", "2026-11-05"))
        self.assertNotEqual(
            a, structure.resource_ext_ident("x", "phase-1", "2026-11-06")
        )
