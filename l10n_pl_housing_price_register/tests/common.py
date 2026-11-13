# Copyright 2026 Leszek Szczepkowski
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
"""Shared fixture. All data is fictitious (example.com, zero numbers)."""

from datetime import date

from odoo import Command

DEVELOPER = {
    "name": "Przykładowy Deweloper spółka z ograniczoną odpowiedzialnością",
    "krs": "0000000001",
    "nip": "1234563218",
    "regon": "123456785",
    "phone": "000-000-001",
    "email": "biuro@example.com",
    "website": "https://example.com",
    "seat_state": "mazowieckie",
    "seat_county": "Przykładowy",
    "seat_municipality": "Przykładowo",
    "seat_city": "Przykładowo",
    "seat_street": "ul. Testowa",
    "seat_house_no": "1",
    "seat_zip": "00-000",
    "sales_state": "mazowieckie",
    "sales_county": "Przykładowy",
    "sales_municipality": "Przykładowo",
    "sales_city": "Przykładowo",
    "sales_street": "ul. Testowa",
    "sales_house_no": "1",
    "sales_zip": "00-000",
    "sales_contact_method": "telefonicznie, e-mail",
    "slug": "przykladowy-deweloper",
}
MANAGER = "l10n_pl_housing_price_register.group_hpr_manager"
READER = "l10n_pl_housing_price_register.group_hpr_user"


class HprCommon:
    """Mixin for test classes: developer, project, two stages, three units."""

    @classmethod
    def _hpr_user(cls, login, group, company=None):
        company = company or cls.env.company
        return cls.env["res.users"].create(
            {
                "name": login,
                "login": login,
                "password": login + "-Password1",
                "email": f"{login}@example.com",
                "group_ids": [
                    Command.set(
                        [cls.env.ref("base.group_user").id]
                        + ([cls.env.ref(group).id] if group else [])
                    )
                ],
                "company_ids": [Command.set([company.id])],
                "company_id": company.id,
            }
        )

    @classmethod
    def _hpr_setup(cls):
        env = cls.env
        cls.company = env.company
        env["ir.config_parameter"].sudo().set_param(
            "l10n_pl_housing_price_register.base_url", "https://example.com"
        )
        cls.manager = cls._hpr_user("hpr_manager", MANAGER)
        cls.developer = env["l10n_pl.hpr.developer"].create(
            dict(DEVELOPER, company_id=cls.company.id, responsible_id=cls.manager.id)
        )
        cls.project = env["l10n_pl.hpr.project"].create(
            {
                "name": "Zielone Wzgórze",
                "developer_id": cls.developer.id,
                "slug": "zielone-wzgorze",
                "site_state": "mazowieckie",
                "site_county": "Przykładowy",
                "site_municipality": "Przykładowo",
                "site_city": "Przykładowo",
                "site_street": "ul. Testowa",
                "site_zip": "00-000",
            }
        )
        cls.stage_a = env["l10n_pl.hpr.stage"].create(
            {
                "name": "Budynek A",
                "project_id": cls.project.id,
                "escrow_open": True,
                "escrow_account": "PL00 0000 0000 0000 0000 0000 0000",
                "escrow_bank": "Bank Przykładowy",
                "escrow_date": date(2026, 8, 1),
                "prospectus_url": "https://example.com/prospekt-a.pdf",
                "part_type": "udział w drodze wewnętrznej",
                "part_ref": "działka 1/2",
                "part_price": 5000.0,
                "part_date": date(2026, 8, 1),
            }
        )
        cls.stage_b = env["l10n_pl.hpr.stage"].create(
            {"name": "Budynek B", "project_id": cls.project.id}
        )

        def product(price):
            return env["product.product"].create(
                {"name": "Lokal", "list_price": price, "company_id": cls.company.id}
            )

        Unit = env["l10n_pl.hpr.unit"]
        cls.a1 = Unit.create(
            {
                "name": "A1",
                "stage_id": cls.stage_a.id,
                "area": 52.40,
                "product_id": product(512000.0).id,
            }
        )
        cls.a2 = Unit.create(
            {
                "name": "A2",
                "stage_id": cls.stage_a.id,
                "area": 64.10,
                "product_id": product(598000.0).id,
            }
        )
        cls.b1 = Unit.create({"name": "B1", "stage_id": cls.stage_b.id, "area": 48.00})

    @classmethod
    def _as_manager(cls, records):
        return records.with_user(cls.manager)

    @classmethod
    def _ready(cls):
        """Everything approved, A2 in register for 598 000 from 12.10.2026."""
        cls._as_manager(cls.developer).action_approve()
        cls._as_manager(cls.project).action_approve()
        cls.stage_a.sending_enabled = True
        cls._as_manager(cls.stage_a).action_approve()
        cls.a2.write(
            {"in_register": True, "price": 598000.0, "date_from": date(2026, 10, 12)}
        )
        cls._as_manager(cls.a2).action_approve()
