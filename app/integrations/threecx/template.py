"""The 3CX v20 server-side custom-CRM template, rendered from this server's real contract.

WHY THIS IS CODE AND NOT JUST A FILE. The template is what 3CX executes: it names the URLs, the
request bodies and the JSON paths of the responses. Checked in as a static file it would drift
from the endpoints the moment a field was renamed, and the failure would show up as a silent
absence of screen-pops on a Monday morning rather than as a red test. So the template is rendered
here from the same constants the routes use, the operator-facing copy in ``deploy/3cx/`` is a
rendered artefact, and ``tests/test_threecx_connector.py`` fails if the two disagree.

THE XML CONTRACT WAS VERIFIED, NOT INVENTED. Every element, attribute and variable below was taken
from 3CX's published server-side CRM template documentation and from a shipping vendor template
(eWay-CRM's own 3CX template), not from guesswork:

  * ``<Crm>`` / ``<Number>`` / ``<Connection>`` / ``<Parameters>`` / ``<Authentication>`` /
    ``<Scenarios>`` with ``<Request>``, ``<Rules>``, ``<Variables>``, ``<Outputs>``.
  * The default (``Id=""``) scenario is contact lookup by number; ``Id="ReportCall"`` is call
    journaling and runs when a call ends. There is exactly ONE ReportCall scenario.
  * A JSON request body is built from ``<PostValues>`` with ``RequestEncoding="Json"``, NOT from
    the ``Message`` attribute: 3CX's specification states the two are mutually exclusive and that
    ``RequestContentType`` is left empty because ``Json`` encoding sets ``application/json``
    itself. (Older vendor templates hand-escape JSON into ``Message`` and wrap each variable in
    ``^^...^^``; this avoids that escaping entirely.)
  * ``Authentication Type="No"`` plus an explicit ``<Headers><Value Key="Authorization">`` is the
    documented way to send a bearer credential.
  * ``[[Var].ToString("...")]`` formats a DateTime variable; 3CX support confirmed this syntax.
  * ``Output Type`` values used here — ``FirstName``, ``LastName``, ``Email``, ``PhoneBusiness``,
    ``ContactUrl``, ``EntityId``, ``EntityType`` — are from the documented set.

THE ONE THING 3CX DOES NOT GIVE US. The ReportCall scenario exposes no call-id variable in v20:
the documented variables are ``[CallType]``, ``[Number]``, ``[Name]``, ``[Agent]``, ``[Duration]``,
``[DateTime]``, ``[CallStartTimeLocal]`` and ``[CallStartTimeUTC]``, and 3CX support has stated
that a call id is available only from the CDR, not from call journaling. The template therefore
cannot send one, and :func:`app.services.communications.call_journal.source_external_id` derives a
stable identity from the start instant, agent, number and direction instead. ``call_id`` is still
accepted by the endpoint, so a future 3CX release — or a CDR-driven poster — can supply the real
thing without a server change.

NOTHING HERE UPLOADS ANYTHING. This module renders text. Installing the template is a deliberate
act performed by an administrator in the 3CX management console.
"""
from __future__ import annotations

from app.integrations.threecx.config import (
    JOURNAL_PATH,
    LOOKUP_PATH,
    TEMPLATE_NAME,
    TEMPLATE_VERSION,
)

#: Where the rendered, operator-facing copy lives, relative to the repository root.
ARTEFACT_PATH = "deploy/3cx/client360-3cx-crm-template.xml"

#: Two concurrent requests, matching the shipping vendor template this contract was verified
#: against. A PBX under load must not be able to open an unbounded number of connections to the
#: application server.
MAX_CONCURRENT_REQUESTS = 2

#: Carried over verbatim from the verified vendor template. These control how 3CX renders
#: ``[Number]``; the choice is deliberately NOT load-bearing here, because the Client360 endpoint
#: re-normalizes whatever arrives through the repository's one phone convention before matching.
NUMBER_PREFIX = "Zeros"
NUMBER_MAX_LENGTH = 256


def _xml_attr(value: str) -> str:
    """Escape a value for an XML attribute. ``&`` first, or the later escapes are double-escaped."""
    return (str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _post_values(pairs: list[tuple[str, str]], indent: str) -> str:
    """A JSON request body as ``<PostValues>``, which is how 3CX builds one.

    NOT the ``Message`` attribute. 3CX's template specification is explicit that a JSON body is
    built from ``<PostValues>``, that ``Message`` and ``<PostValues>`` are mutually exclusive, and
    that ``RequestContentType`` is left empty because ``RequestEncoding="Json"`` sets
    ``application/json`` itself. Older vendor templates hand-escape JSON into ``Message`` and wrap
    each variable in ``^^...^^`` to get the quoting right; building the object here avoids that
    whole class of escaping bug.

    A top-level JSON object is an ``<Object Key="">`` wrapper holding one ``<Value Key="...">`` per
    field. ``Passes="0"`` matches the request's own ``MessagePasses="0"`` and the ``Passes="0"``
    the verified vendor template uses throughout — this is a single-pass request.
    """
    lines = [f'{indent}<Object Key="">']
    lines += [f'{indent}  <Value Key="{key}" Type="String" Passes="0">{_xml_text(value)}</Value>'
              for key, value in pairs]
    lines.append(f"{indent}</Object>")
    return "\n".join(lines)


def _xml_text(value: str) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


#: The call's start instant as unambiguous UTC ISO 8601. ``[CallStartTimeUTC]`` is a DateTime
#: object, and this is 3CX's confirmed formatting syntax for one. It is half of the derived call
#: identity, so an ambiguous or locale-dependent rendering here would break de-duplication.
_STARTED_AT = '[[CallStartTimeUTC].ToString("yyyy-MM-ddTHH:mm:ssZ")]'

#: Skip journaling unless the operator enabled it, a client was actually identified, and the call
#: completed. The server refuses a missed or unanswered call too — this is the cheap half of a
#: deliberate belt-and-braces, so a 3CX build that evaluates the condition differently still
#: cannot get an incomplete call into the ledger.
_REPORT_CALL_SKIP_IF = (
    '[ReportCallEnabled]!=True'
    '||[EntityId]==""'
    '||[CallType]=="Missed"'
    '||[CallType]=="Notanswered"'
)


def render() -> str:
    """The complete template XML, deterministic for a given version and set of endpoint paths."""
    lookup_body = _post_values([("number", "[Number]")], " " * 10)
    journal_body = _post_values([
        ("call_type", "[CallType]"),
        ("number", "[Number]"),
        ("agent", "[Agent]"),
        ("duration", "[Duration]"),
        ("started_at_utc", _STARTED_AT),
        ("entity_id", "[EntityId]"),
    ], " " * 10)
    return f"""<?xml version="1.0" encoding="utf-8"?>
<!--
  Client360 custom CRM template for 3CX Phone System v20 (server side).

  GENERATED FILE - do not edit by hand. It is rendered by app/integrations/threecx/template.py
  from the live endpoint definitions, and tests/test_threecx_connector.py fails if this copy and
  that renderer disagree. Edit the renderer, re-render, and commit both.

  Install: 3CX Management Console > Settings > CRM > Server side > Add, then set the three
  parameters below. See docs/THREECX_INTEGRATION.md.
-->
<Crm xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
     xmlns:xsd="http://www.w3.org/2001/XMLSchema"
     Country="US" Name="{_xml_attr(TEMPLATE_NAME)}" Version="{TEMPLATE_VERSION}"
     SupportsEmojis="false">
  <Number Prefix="{NUMBER_PREFIX}" MaxLength="{NUMBER_MAX_LENGTH}" />
  <Connection MaxConcurrentRequests="{MAX_CONCURRENT_REQUESTS}" />
  <Parameters>
    <Parameter Name="BaseUrl" Type="String" Parent="General Configuration" Editor="String"
               Title="Client360 base URL (https://host, no trailing slash)" />
    <!-- The DEDICATED integration secret. Not a Client360 login: 3CX stores template parameters
         as readable configuration, so this value is visible to PBX administrators and grants
         nothing beyond the two connector endpoints. -->
    <Parameter Name="IntegrationSecret" Type="String" Parent="General Configuration" Editor="String"
               Title="Client360 integration secret" />
    <Parameter Name="ReportCallEnabled" Type="Boolean" Parent="General Configuration" Editor="String"
               Title="Enable call journaling" Default="True" />
  </Parameters>
  <!-- "No" plus an explicit Authorization header is the documented way to send a bearer
       credential; Basic would put the secret in a base64 user:password pair it is not. -->
  <Authentication Type="No" />
  <Scenarios>
    <!-- CONTACT LOOKUP BY NUMBER. The default scenario (Id="") is the one 3CX runs on an
         incoming or outgoing call to decide what to pop.

         Client360 answers with a "contacts" array holding EXACTLY ONE entry when the number
         matches one active client, and an EMPTY array for both no match and several matches.
         That is what makes the "never pop on an ambiguous number" rule structural rather than
         advisory: Rule Type="Any" simply does not fire on an empty array, so 3CX has nothing to
         open and no name to display. -->
    <Scenario Id="" Type="REST">
      <Request Url="[BaseUrl]{LOOKUP_PATH}" MessagePasses="0" Message=""
               RequestContentType="" RequestEncoding="Json"
               RequestType="Post" ResponseType="Json">
        <Headers>
          <Value Key="Authorization">Bearer [IntegrationSecret]</Value>
        </Headers>
        <PostValues>
{lookup_body}
        </PostValues>
      </Request>
      <Rules>
        <Rule Type="Any">contacts</Rule>
      </Rules>
      <Variables>
        <Variable Name="PersonId" Path="contacts.person_id"><Filter /></Variable>
        <Variable Name="ClientFirstName" Path="contacts.first_name"><Filter /></Variable>
        <Variable Name="ClientLastName" Path="contacts.last_name"><Filter /></Variable>
        <Variable Name="ClientEmail" Path="contacts.email"><Filter /></Variable>
        <Variable Name="ClientUrl" Path="contacts.contact_url"><Filter /></Variable>
      </Variables>
      <!-- AllowEmpty="false": an entry with no name is not a contact, so 3CX pops nothing. -->
      <Outputs AllowEmpty="false">
        <Output Type="FirstName" Passes="0" Value="[ClientFirstName]" />
        <Output Type="LastName" Passes="0" Value="[ClientLastName]" />
        <Output Type="Email" Passes="0" Value="[ClientEmail]" />
        <!-- No phone number is echoed back. 3CX already holds the number it asked about, so
             returning it would add nothing and put one more copy of client contact data on the
             wire. The staff profile page this call pops open: -->
        <Output Type="ContactUrl" Passes="0" Value="[ClientUrl]" />
        <Output Type="EntityType" Passes="0" Value="person" />
        <Output Type="EntityId" Passes="0" Value="[PersonId]" />
      </Outputs>
    </Scenario>

    <!-- CALL JOURNALING. Reserved Id; 3CX runs exactly one of these when a call ends.

         No call-id variable exists in v20, so none is sent. Client360 derives a stable identity
         from the start instant, agent, number and direction, and the endpoint is idempotent: a
         3CX retry returns the original record and writes nothing. -->
    <Scenario Id="ReportCall" Type="REST">
      <Request SkipIf="{_xml_attr(_REPORT_CALL_SKIP_IF)}"
               Url="[BaseUrl]{JOURNAL_PATH}" MessagePasses="0" Message=""
               RequestContentType="" RequestEncoding="Json"
               RequestType="Post" ResponseType="Json">
        <Headers>
          <Value Key="Authorization">Bearer [IntegrationSecret]</Value>
        </Headers>
        <PostValues>
{journal_body}
        </PostValues>
      </Request>
      <Rules />
      <Variables />
      <!-- Nothing is read back and no scenario is chained: journaling is the end of the flow. -->
      <Outputs AllowEmpty="true" />
    </Scenario>
  </Scenarios>
</Crm>
"""
