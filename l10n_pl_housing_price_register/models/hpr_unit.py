# Copyright 2026 Leszek Szczepkowski
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl).
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from . import structure
from .hpr_approval_mixin import (
    APPROVING,
    approving,
    changed_fields,
    check_button_call,
    check_manager,
    check_no_formula,
    today_pl,
)
from .hpr_project import PRICE_TOLERANCE

STATE_FIELDS = ("state", "approved_by_id", "approved_on")
PRICE_FIELDS = (
    "price",
    "date_from",
    "name",
    "in_register",
    "stage_id",
    "area",
    "total_price",
    "annex_type",
    "annex_ref",
    "annex_price",
    "annex_date",
)


class HprUnit(models.Model):
    """A residential unit or house offered for sale.

    Other modules can link it to their own model of units (e.g. a real estate
    module) by overriding `_hpr_offer_price`, `_hpr_is_sold` and the `offer_state`
    and `area` fields.
    """

    _name = "l10n_pl.hpr.unit"
    _description = "Housing price register - unit"
    _inherit = ["mail.thread"]
    _order = "project_id, name"
    _check_company_auto = True

    name = fields.Char("Number (E2)", required=True, tracking=True)
    stage_id = fields.Many2one(
        "l10n_pl.hpr.stage",
        string="Stage",
        required=True,
        ondelete="cascade",
        index=True,
    )
    project_id = fields.Many2one(related="stage_id.project_id", store=True, index=True)
    company_id = fields.Many2one(related="stage_id.company_id", store=True, index=True)
    product_id = fields.Many2one(
        "product.product",
        string="Product",
        check_company=True,
        help="Optional. The sales price of the product (with taxes) is compared "
        "with the price in the register.",
    )
    offer_state = fields.Selection(
        [("available", "Available"), ("reserved", "Reserved"), ("sold", "Sold")],
        default="available",
        required=True,
        tracking=True,
        help="Sold units (developer agreement signed) leave the file at once.",
    )
    area = fields.Float("Usable floor area (m²)", digits=(16, 2), tracking=True)
    in_register = fields.Boolean(tracking=True, copy=False)
    price = fields.Float("Gross price (E5)", digits=(16, 2), tracking=True, copy=False)
    price_m2 = fields.Float(
        "Price per m² (E3)", digits=(16, 2), compute="_compute_price_m2"
    )
    date_from = fields.Date("Price valid from (E4/E6)", tracking=True, copy=False)
    total_price = fields.Float(
        "Total price (E7)",
        digits=(16, 2),
        tracking=True,
        copy=False,
        help="Only when the offer shows a total price with the parts, rights and "
        "other payments of art. 19a.",
    )
    annex_type = fields.Char("Appurtenant rooms - type (G1)", tracking=True)
    annex_ref = fields.Char("Appurtenant rooms - reference (G2)", tracking=True)
    annex_price = fields.Float(
        "Appurtenant rooms - price (G3)", digits=(16, 2), tracking=True
    )
    annex_date = fields.Date("Appurtenant rooms - price from (G4)", tracking=True)
    state = fields.Selection(
        [("draft", "To approve"), ("approved", "Price approved")],
        string="Price status",
        default="draft",
        required=True,
        readonly=True,
        tracking=True,
        copy=False,
    )
    approved_by_id = fields.Many2one(
        "res.users", string="Approved by", readonly=True, copy=False
    )
    approved_on = fields.Datetime(readonly=True, copy=False)
    offer_price = fields.Float(
        "Price in the offer",
        digits=(16, 2),
        compute="_compute_offer_price",
        help="Sales price of the product with taxes - the price the buyer sees.",
    )
    issue = fields.Text(readonly=True, copy=False)
    price_ids = fields.One2many("l10n_pl.hpr.price", "unit_id", "Approved prices")

    _name_uniq = models.Constraint(
        "unique(project_id, name)", "This unit number already exists in the project."
    )

    @api.depends("price", "area")
    def _compute_price_m2(self):
        for unit in self:
            unit.price_m2 = round(unit.price / unit.area, 2) if unit.area else 0.0

    def _hpr_offer_price(self):
        """Gross price in the offer, 0.0 when unknown.

        Override to use another source of offer prices.
        """
        self.ensure_one()
        # sudo: the reader may have no access to products; only the price is read
        product = self.product_id.sudo()
        if not product:
            return 0.0
        price = product.lst_price
        if "taxes_id" in product._fields and product.taxes_id:
            taxes = product.taxes_id.filtered(lambda t: t.company_id == self.company_id)
            price = taxes.compute_all(price, product=product)["total_included"]
        return price

    def _compute_offer_price(self):
        for unit in self:
            unit.offer_price = unit._hpr_offer_price()

    def _hpr_is_sold(self):
        self.ensure_one()
        return self.offer_state == "sold"

    @api.constrains(
        "name", "annex_type", "annex_ref", "price", "area", "total_price", "annex_price"
    )
    def _check_text(self):
        for unit in self:
            check_no_formula(unit, ("name", "annex_type", "annex_ref"))
            for fname in ("price", "area", "total_price", "annex_price"):
                if unit[fname] < 0:
                    raise ValidationError(
                        self.env._(
                            "%(unit)s: %(field)s cannot be negative.",
                            unit=unit.name,
                            field=unit._fields[fname].string,
                        )
                    )

    @api.constrains("in_register", "stage_id")
    def _check_escrow(self):
        for unit in self:
            if unit.in_register and not unit.stage_id.escrow_open:
                raise ValidationError(
                    self.env._(
                        "%(unit)s: stage “%(stage)s” has no open escrow account, the "
                        "unit cannot go to the price register.",
                        unit=unit.name,
                        stage=unit.stage_id.name,
                    )
                )

    @api.model_create_multi
    def create(self, vals_list):
        if not approving(self.env):
            vals_list = [
                {k: v for k, v in vals.items() if k not in STATE_FIELDS}
                for vals in vals_list
            ]
        return super().create(vals_list)

    def write(self, vals):
        if "stage_id" in vals and self.filtered(
            lambda u: u.price_ids and u.stage_id.id != vals["stage_id"]
        ):
            raise UserError(
                self.env._(
                    "A unit with approved prices cannot move to another stage: its "
                    "prices were approved for this stage, project and escrow "
                    "account. Create a new unit instead."
                )
            )
        if not approving(self.env):
            vals = {k: v for k, v in vals.items() if k not in STATE_FIELDS}
            if changed_fields(self, vals, PRICE_FIELDS):
                vals["state"] = "draft"
        return super(HprUnit, self.with_context(hpr_approving=False)).write(vals)

    def _hpr_last_valid_price(self, day):
        """The most recently approved price among those valid on the day.

        The latest approval wins, not the latest “from” date: a correction
        approved later with an earlier date replaces a price approved earlier
        for a later date, as on the unit card.
        """
        self.ensure_one()
        valid = self.price_ids.filtered(lambda p: p.date_from and p.date_from <= day)
        return valid.sorted(lambda p: (p.approved_on, p.id))[-1:]

    def _hpr_price_on(self, day):
        """Last approved price valid on the day (date from <= day), if the unit was
        marked “in register” at that approval."""
        self.ensure_one()
        last = self._hpr_last_valid_price(day)
        return last if last and last.in_register else self.env["l10n_pl.hpr.price"]

    def _hpr_current_price(self, day):
        """Approved price valid on the day (whatever its “in register” flag); the
        last approved one when every approved price starts later."""
        self.ensure_one()
        return self._hpr_last_valid_price(day) or self.price_ids[:1]

    def _hpr_issues(self):
        """Texts about this unit: offer and register do not match."""
        self.ensure_one()
        out = []
        last = self._hpr_current_price(today_pl())
        offer = self._hpr_offer_price()
        if self.in_register:
            if self.state != "approved":
                out.append(
                    self.env._(
                        "%s: change waiting for approval (the file uses the last "
                        "approved price, if any).",
                        self.name,
                    )
                )
            if last and offer and abs(offer - last.price) > PRICE_TOLERANCE:
                out.append(
                    self.env._(
                        "%(unit)s: price in the offer %(offer)s PLN ≠ price in the "
                        "register %(register)s PLN.",
                        unit=self.name,
                        offer=structure.amount(offer),
                        register=structure.amount(last.price),
                    )
                )
            if self._hpr_is_sold():
                out.append(
                    self.env._(
                        "%s: sold - it left the file; untick “in register”.",
                        self.name,
                    )
                )
            if last and abs((self.area or 0.0) - last.area) > 0.004:
                out.append(
                    self.env._(
                        "%(unit)s: floor area %(area)s m² ≠ approved %(approved)s m², "
                        "approve the price again.",
                        unit=self.name,
                        area=self.area,
                        approved=last.area,
                    )
                )
        elif self.stage_id.sending_active and self.offer_state in (
            "available",
            "reserved",
        ):
            out.append(
                self.env._(
                    "%(unit)s: on offer (%(state)s) but not in the register.",
                    unit=self.name,
                    state=dict(
                        self._fields["offer_state"]._description_selection(self.env)
                    ).get(self.offer_state),
                )
            )
        return out

    def action_approve(self):
        check_manager(self.env)
        check_button_call(self.env)
        Price = self.env["l10n_pl.hpr.price"].sudo()  # only the module writes history
        for unit in self:
            if unit.price <= 0 or not unit.date_from or not unit.area:
                raise UserError(
                    self.env._(
                        "%s: approval needs a price, a “from” date and the floor area.",
                        unit.name,
                    )
                )
            unit.with_context(hpr_approving=APPROVING).write(
                {
                    "state": "approved",
                    "approved_by_id": self.env.uid,
                    "approved_on": fields.Datetime.now(),
                }
            )
            Price.create(
                {
                    "unit_id": unit.id,
                    "name": unit.name,
                    "price": unit.price,
                    "price_m2": unit.price_m2,
                    "area": unit.area,
                    "date_from": unit.date_from,
                    "in_register": unit.in_register,
                    "total_price": unit.total_price,
                    "annex_type": unit.annex_type,
                    "annex_ref": unit.annex_ref,
                    "annex_price": unit.annex_price,
                    "annex_date": unit.annex_date,
                    "approved_by_id": self.env.uid,
                }
            )
        return True

    def action_price_from_offer(self):
        """Copy the offer price as a new price to approve, valid from today."""
        check_manager(self.env)
        for unit in self:
            offer = unit._hpr_offer_price()
            if not offer:
                raise UserError(
                    self.env._("%s: there is no price in the offer.", unit.name)
                )
            unit.write({"price": offer, "date_from": today_pl()})
        return True
