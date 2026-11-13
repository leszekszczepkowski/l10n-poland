- The column names follow the draft regulation of the Minister of Digital Affairs
  of 23 September 2026. Before production use compare the 57 headers, their
  order, types and required fields with the regulation published in the Journal
  of Laws and with the current sample file, then raise the structure version in
  `models/structure.py`.
- Year change: the dataset title shows the year of the last file and one
  dataset keeps all resources. If the dane.gov.pl instruction requires one
  dataset per calendar year, group the files by year with a stable identifier
  for each year. To be checked before 31 December 2026.
- Columns F, H and I (part of the property, rights, other payments) are entered
  once per stage and go to every unit of the stage; a unit has one appurtenant
  room (G). When parking spaces or storage rooms have different prices, move
  these columns to the unit.
- Bridge modules for real estate apps that already keep units, prices and sales
  states (override `_hpr_offer_price`, `_hpr_is_sold`, `offer_state`, `area`).
- Today's file is created once, at the first run after midnight. A price
  approved during the day goes out in the next day's file. A correction file
  for a day that was already published is not supported yet.
- A day on which the server did not run stays without a file; it cannot be
  created later.
- The fixed address `najnowszy-<project>.csv` keeps returning the last file,
  also when the project stopped sending long ago.
- Units and stages with approved prices cannot be deleted or archived (the
  price history is kept). A unit created by mistake stays on the lists.
