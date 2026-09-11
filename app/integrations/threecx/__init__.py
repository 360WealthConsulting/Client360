"""3CX Phone System v20 custom-CRM connector.

The connector is the SERVER half of 3CX's supported "server side" CRM integration: 3CX calls
Client360 over HTTP using an XML template uploaded into its management console, and Client360
answers with the client identity behind a number and accepts a journal entry when a call ends.
Client360 never calls 3CX, holds no 3CX credential, and reads no recording or transcript.

  * :mod:`~app.integrations.threecx.config`   — every switch, all defaulting to OFF
  * :mod:`~app.integrations.threecx.auth`     — the dedicated integration secret
  * :mod:`~app.integrations.threecx.lookup`   — exact normalized-phone match to one person
  * :mod:`~app.integrations.threecx.template` — the v20 XML template this server expects

Call journaling itself is NOT here: it is provider-neutral and lives with the rest of the
communications domain, in :mod:`app.services.communications.call_journal`.
"""
