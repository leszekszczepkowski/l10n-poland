1. Mark the units that are on offer as *In register*, enter the price and the
   date from which it is valid, and press *Approve price*.
2. The scheduled action creates today's file once, after midnight Polish time.
   *Create today's file* does the same by hand. The *Today's file* tab shows
   what goes out and why a unit is left out.
3. When the price changes, enter the new price and its date and approve it. A
   price approved in advance is used from its date. When several approved
   prices are valid on a day, the most recently approved one goes to the file,
   the same one the unit card shows.
4. When a unit is sold (developer agreement signed), set its offer state to
   *Sold*: it leaves the next file.

Approving works through the approve buttons of the web client. Calls to
`action_approve` through JSON-RPC or XML-RPC are refused, so an integration or
an AI connector cannot approve prices on its own. This is a guard against RPC
clients, not a proof that a person clicked: anyone with a session can still
call the button route.

The source value is the gross price of the unit (E5). The price per m² (E3) is
calculated from it and rounded to the grosz, so E3 × floor area can differ from
E5 by a few grosze.

Saving the same values again (an import, an integration) keeps the approval;
only a real change sends the data back to approval. A stage with approved
prices cannot move to another project, and a unit with approved prices cannot
move to another stage. A project that sends data cannot be archived before
sending is turned off on its stages.

A daily file is never changed or deleted. If a published price was wrong,
approve the correct price; it goes out in the next daily file.
