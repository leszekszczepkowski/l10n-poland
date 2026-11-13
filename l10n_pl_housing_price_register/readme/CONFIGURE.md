1. Give users the group *Housing price register: Approval* (or *Read*). The
   administrator gets *Approval* at installation, so the menu is visible at
   once; remove it if another person approves the register.
2. Set the system parameter `l10n_pl_housing_price_register.base_url` to the
   public https address of Odoo, e.g. `https://erp.example.com`. Every daily
   file keeps this address forever, so no file is created without it. Instead
   of the parameter you can freeze `web.base.url` (`web.base.url.freeze` =
   `True`); an unfrozen `web.base.url` is not used, because Odoo rewrites it
   when an administrator logs in. Choose this address once: files already
   published keep the old one.
   dane.gov.pl calls the addresses without choosing a database, so with more
   than one database set `dbfilter` (or `db_name`) to serve the right one.
3. In *Price register > Developers* create the developer for the company
   (*Fill from company* copies what Odoo already knows), fill fields A-C and
   approve them.
4. Create the project with its location (D), its stages and units. Link a unit
   to a product to compare the register price with the sales price.
5. On each stage enter the escrow account and the prospectus URL and turn on
   *Sending enabled*.
6. Send the address of `dane.xml` and `dane.md5` to dane.gov.pl to connect the
   harvester.
