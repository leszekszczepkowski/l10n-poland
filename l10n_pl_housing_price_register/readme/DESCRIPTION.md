This module prepares the daily price file for the Polish housing price register
on dane.gov.pl and publishes it at fixed public addresses.

- Developer (fields A-C), project (D), stages with the escrow account and the
  information prospectus, units with prices approved by a person.
- A unit of a stage without an open housing escrow account cannot go to the
  register; sending also needs the prospectus URL and a switch.
- Every day one CSV file per project is created once and never changed. A
  growing harvester XML lists all daily files as separate resources, as the
  instructions of dane.gov.pl require.
- Only approved data is sent. A change removes the approval, and the file keeps
  using the last approved version until a person approves again.
- Approved prices and daily files are append only.
- Issues (price in the offer differs from the register, a unit on offer that is
  not sent, a sold unit still marked, the prospectus does not respond, missing
  file in the morning) become an activity for the responsible user.

Public addresses (no website module needed):

- `/rejestr-cen/<developer>/dane.xml` and `dane.md5` for the harvester,
- `/rejestr-cen/<developer>/<file>.csv` for each daily file,
- `/rejestr-cen/<developer>/najnowszy-<project>.csv` for the latest file of a
  project.
