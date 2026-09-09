"""Drake returns: re-read the 109 malformed 2021/2022 1120S rows, and re-key them.

WHY A MIGRATION AND NOT A DATA FIX
----------------------------------
The 2021 and 2022 Drake exports emit ONE FEWER FIELD for every 1120S return, so ``csv.DictReader`` —
which assigns values to header names by position and tolerates a short row silently — mapped every
value from the omission point onward one column early. The value belonging in ``Type`` landed in
``Paid``, and ``return_type`` was imported as NULL for all 109 of them. ``app.importers.
drake_client_csv`` now restores the missing structural slot before any mapping happens, so a fresh
import reads those rows correctly.

That alone is not enough, because ``return_type`` is an INPUT to the row's identity:

    return_identity_key = SHA-256( tax_year | taxpayer_hash | spouse_hash | return_type | filing_status )

and that key is the ``ON CONFLICT`` target of the returns upsert. Leaving the stored rows as they are
means the corrected importer computes a key those rows do not have, matches nothing, and INSERTS 109
duplicate returns. Correcting ``return_type`` without the key means the next import matches the old
key and writes NULL back over the correction. Neither is acceptable, which is why the importer fix and
this migration belong in one release: after both, the corrected importer resolves each existing row
through its new key and updates it in place.

WHAT IS RE-READ, AND FROM WHERE
-------------------------------
Nothing is guessed and no client value is embedded in this file. ``raw_data`` holds the row exactly as
the export produced it, and THIS MIGRATION never rewrites it, so it reconstructs the original ordered
values from it — using ``CLIENT_EXPORT_HEADER``, because JSONB sorts its keys and loses the order —
runs the SAME normalization the importer now runs, and re-derives the affected columns with the SAME
parse helpers. Upgrade and downgrade both derive their values that way, which is what makes the pair
lossless without storing a single taxpayer figure in version control.

**That losslessness is conditional on ``raw_data`` still being the pre-fix payload.** A later Drake
source re-import legitimately REFRESHES ``raw_data``: the corrected importer writes the normalized
mapping, so a re-imported row's payload no longer describes the displaced original. This is not a
fault — it is the importer doing its job — but it means the downgrade below can no longer reconstruct
the historical values from the live row alone.

Both directions already refuse in that situation rather than inventing anything, and that behaviour is
deliberate and must not be relaxed:

  * ``downgrade`` reads the refreshed payload, finds ``return_type`` is ``1120S`` rather than NULL, and
    raises "does not re-read as a NULL return_type";
  * a re-run ``upgrade`` finds ``Paid`` no longer holds a form token and raises "row does not match the
    proven short-row shape".

**After a source re-import, the verified pre-migration backup is the authoritative rollback
mechanism**, not this downgrade. See ``docs/DRAKE_1120S_SHORT_ROW.md``. Never synthesize historical
values to make the downgrade succeed.

Columns re-derived (every column the importer reads from a header position at or after the
displacement, measured across the 109 rows):

    return_type      wrong on 109      federal_product    wrong on 77
    agi              wrong on 102      federal_ack_date   wrong on 77
    preparer_fee     wrong on  97      federal_ack_code   wrong on 79
    complete_date    wrong on 109      state_product      wrong on 79
    prepare_date     unchanged         state_ack_date     wrong on 79
    review_date      unchanged         state_ack_code     wrong on 79
    approved_date    unchanged

Columns BEFORE the displacement — ``filing_status``, the taxpayer and spouse name fields,
``taxpayer_dob``, ``preparer_code``, and both identifier hashes — are not touched and not written.
``Receipt`` and ``Fee`` straddle the one position the omission cannot be pinned to, and neither is
parsed into a column, so that ambiguity reaches nothing.

SCOPE, FROZEN AND FAIL CLOSED
-----------------------------
The cohort is the 109 exact primary keys in :data:`_COHORT`, each paired with the identity key that
row must still carry. There is no ``WHERE return_type IS NULL`` sweep: a row that is not in the frozen
set is never touched, and a row in the set that has drifted aborts the whole migration. Nothing is
inserted, nothing is deleted, and a collision on any new key stops everything before the first write.

TIMESTAMPS
----------
``drake_client_returns`` has no ``updated_at``, no ``imported_at`` and no audit columns, and no trigger
touches it — verified against the schema. ``source_updated_at`` is the EXPORT FILE's mtime, which this
migration does not re-read and does not write. No timestamp changes in either direction.
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

from app.importers.drake_client_csv import (
    CLIENT_EXPORT_HEADER,
    NORMALIZED,
    normalize_row,
    parse_date_value,
    parse_decimal_value,
    to_mapping,
)
from app.services.drake_return_identity import compute_return_identity_key

revision = "drake03"
down_revision = "dbi01"
branch_labels = None
depends_on = None

#: The one return form the production cohort recovers to. Asserted per row, never assumed.
_EXPECTED_RETURN_TYPE = "1120S"

#: Columns re-derived from the normalized row. Every one is read by the importer from a header
#: position at or after the displacement; nothing before it appears here.
_REPAIRED_COLUMNS = (
    "return_type",
    "agi",
    "preparer_fee",
    "prepare_date",
    "review_date",
    "approved_date",
    "complete_date",
    "federal_product",
    "federal_ack_date",
    "federal_ack_code",
    "state_product",
    "state_ack_date",
    "state_ack_code",
)

#: (primary key, the identity key the row must still carry). Frozen against production at
#: 041221f4f471cee8efcd340884f26aafeb94c05c; 52 rows from 2021 and 57 from 2022. Identity keys are
#: SHA-256 over already-hashed inputs — no taxpayer identifier is present or derivable.
_COHORT: tuple[tuple[int, str], ...] = (
    (643, "e37e06c46ce1957b05c6985184903a92003ce975daa75971c3143a747c18313c"),
    (657, "a77b9c06e2090a6eadf29f5e70b4554a030dc05154dd33d35ce8980a62d5f329"),
    (660, "7e297ebf2c623628cc6da8b7860b5eae342054f4c0456cd7688bfbd6403ba99c"),
    (666, "79d1f960bf63357cbb2df3f7a72e89a210296afd746f4eeeec9e2b1eafd9a744"),
    (670, "f4ba724a0176a4258759ac93dc00d586fe3938742695cb6a5d1b35a086d43e54"),
    (702, "5cdcefec723032f4c52eeffa577027580c04898116aa7dd59fa96969cd27a683"),
    (713, "b87232ac2a3e00d6bbf2470ccf4e1ee0882eeb079786ab8cc0f52fab5210e146"),
    (714, "f9eb7aac0d7da448a1087ebce28dc1e204bc610742da3e04cb749956991a503f"),
    (723, "29ae67512e396b84c27f69f7afd5818f21959c855b7281494608d598261d8d66"),
    (726, "701f8cb7794942eef87e11f9aa66b2a83919a0fda96e0b708e4329a0aeb3bb8d"),
    (729, "fc339dd66efed30c8be97a6abeed8b8f48098c6239c351e73a55fb02af8d8f45"),
    (750, "f9d8cc4d3aaefdf89ce9d82fe9248a0b76910f67e09f9324d88423e631a75ea7"),
    (766, "0d420fa8eea5765d6d0c45c65d1502b02e41448f1489c26cde3253fe4cb45442"),
    (771, "c87ef1930674b22a8d239d73443780a6274f1d64a1074a0e39ea6d98ec33fe0d"),
    (772, "91791a328a5b167598e8c82d8a80f891141b2afd9bfd6046725cc3c7eb7cb392"),
    (783, "5da723a80513938ac96a5963f27b22b7c8924b2157607c145e2b33a54e21554e"),
    (785, "614ed959078cd22a0b1148d3e0ae4cc41e465839e259fd0e488d904cf4a9a6f1"),
    (788, "a9ca883bca395da6c638216ba31d8331dd2e94ecab98b38f68f325a1b465fce7"),
    (799, "0c4dfe4b52e0acb6705935001686bec371fc7b2f120727bab9e5954445d2a6aa"),
    (803, "62eb746bf7b8d72a67bf3e0278479a5ffa4fb9516e517084547dba21dc0845b4"),
    (831, "b7af7163b911d7221a88ade657f44953fb97013249d240367bd91a4beff4bd35"),
    (840, "1ebf0586a046138c042d0b079f9d8d6d385776e19e9a3c7070c7d7807ad1bed2"),
    (865, "bbf541d3301f3e7e3adf6222a47fb12716fbab768e051d444cdc058a895e645e"),
    (879, "d04cd63dc809cc222a3fd6a5ba0417e4d1cd087c05d7ef6027cd506ed65734bd"),
    (917, "c145e5ec48fe935a16e8cea31221a873dcafceeaf555d5c60e68269cd72f5a66"),
    (924, "6ce825588981c8a1ca15654bc4ec17825c3250538af28a020a860c36eca50f68"),
    (952, "5b554192d62cb950701e5c0810e2742bc327e1f2abe1441d68fb435e200c628f"),
    (960, "92eea6dd2ee3f1638a83bc50354291b0ad0e9a2179ddb65893edd96d0e8c28e6"),
    (970, "0e05eb5e1f92fcc456a0f2c0d3b41d0df4667bd510410c04aecd04933f5b519b"),
    (1005, "e47724dfcf6c11a6e67430cdebb6fd6d472001f51fa66e114bcacb77b88172c1"),
    (1007, "66fd80db52ff25ca57d0480b423d53517b92de62ab90b504623fb836adfbd039"),
    (1008, "40053600475ceb5dc946f5df4657f4c8352b550ec8b5e15e74e38e1dc3373b6d"),
    (1015, "c44f99777365f86556e979628af82907e52d5e80e473e832653a32bcc01c672b"),
    (1016, "3e34464bcb8ee7944b85df5a4e73c5573cd381b89257fb6080436cf9096d9687"),
    (1018, "38f7230bf780f0f764fe3daf9c53a35098d48a82003a02f5a8c6eb701832f85f"),
    (1038, "9d63cc75b84ae1a5d9cd59a01006582f6670179c7046cc824167b3582a3d0cf5"),
    (1050, "e4c80c12fdf470d86abb87a793fccaaf9bb0ef1346a759bde00f96369839758b"),
    (1055, "9b86f5a3ce209c669d15b67205eaa784d9bb0146ca6b6062be4e42be5998e8f7"),
    (1076, "45e82a282e81cdc9d87f30a178fb712dad6469962464df0fbdf668366fe185de"),
    (1088, "5c5553dfa856139b8a901e6ee1a518f23caaa4ee85c762c66c37c656a2a3d90a"),
    (1091, "17b7805f0749bdc7c9d43f7ee2349b219589e765b31253c6250b8bc0e742b164"),
    (1109, "7b6e0d33f0fa005eca646bb0769ef1f3d76befe173e02d1af4a7b19430ad4c08"),
    (1110, "6076d2a5010bdb8bd5f4258e024a9fc69b2e27b896564a849e4d36ef9c0958d5"),
    (1121, "20bcfe0a79f10c73de40833695b957485fc14258ab02784cc819eb85697828b4"),
    (1145, "642a13017ba6f9eddff70815a9207dda9058c0d1a6b7f3deb9ffaf72c5cb4424"),
    (1148, "c398225d7a1122a9c3ecd0253ead066d10710c554537dfcbb1ac1875fd8f14c5"),
    (1193, "ada4caa239b1a4df378d51370a6f38acc13c8d7433d75a68a9bc3bc410edf453"),
    (1210, "d944c565933432f6af445a4d165d63041b393d04685218b58cfaa5437c54bcac"),
    (1260, "fc819c320a9872af186065e658d65d5b5b96275621bd6b6b21fb4f7330ae01fc"),
    (1268, "b8ff6e83f7d15ca7f0b471a3ad456425086a8330c34a9a6212d676e1aff20dbe"),
    (1295, "6098b6704f7ba725d4ced4105536b273f09a29e986cfc8b4e406c519fc5cb69a"),
    (1332, "0c1a4a6f94ca26d8580397b26b5ff2567788a14a730aacf5f3d1d18548c78947"),
    (1386, "55877aab2880526c6cb3946e91c421bd7fa82fa3702f5fb8ea3c8fd09b9f6bd6"),
    (1404, "4fc425798511150ee152d57bce15e30c0e8a493d58ba7f52a250be71e7e26e2e"),
    (1409, "d5e81dd1f02047cc32934be0276bf28627d0155fae045d0582cb313e589e5681"),
    (1451, "d52cc2fefee83b3fb6d9ec7f46f65b46c5233d134f192366c5db3ee570410ff9"),
    (1460, "245901cf61050b83db92b69dd7d406b703440fdb91cdbbb2f59b4af10531a2ac"),
    (1466, "c55cee2bfbf3084d869857a03be86f11dccec50f2775e1feb9a0e6b50fca4e7b"),
    (1484, "89b82c1ae8c7be234742cd97625ec98818ab5e3f0d0ec19d91cc53435a0cf7e1"),
    (1503, "51b476c36d9a9d6c1af2c04ac9bbc14589a32d592bc5bd41fb14e43836eb6175"),
    (1506, "23ea57d11340f23c74f67f4d3e08ea676cee3852b87f1811eeddd01b8d99003c"),
    (1511, "579621056d11eda360cf42e7861a0e2d017a85a109c1887c00fc6cc49a9721a2"),
    (1520, "2c829a7d4d3d66f29f547b780cf4de21686a4ec4f0c768b9a8b18bd11e3f0c42"),
    (1522, "918862e492bea45882a5851292e5481eb05ffb72b389a45d823792706ef19378"),
    (1525, "7ba4f84fa0a43aa1014e82eae03efcbd20eb2dc022730eca653e8563696a92f7"),
    (1536, "7a598cee7a4c7b83fc9e052de97db33f008c8c127657447b43ad95c09a55b2d1"),
    (1540, "2d4e80183a0b87e23777b55f44d17c7f6f0131f3a26abed14f923495a7c9f064"),
    (1566, "fbd8760eb42a27780b0d772d7b954fb59b8a715331af987f8a5c88fd38771f9a"),
    (1572, "908298fc728bc43116c3f4942cff993d47dad540be4a8c06ecef6cda793954a4"),
    (1596, "2a5ffb2e19e20dfc4f54b8175cfd016991f00b13f607f1fe0e3624469d547a80"),
    (1613, "2b4ed09d2cbbf748bd8b8c497e2895f3b4397b6e5a0fb0715b171a96082e52b9"),
    (1614, "35317e66b6c31cb33775a7b3667a7f13f25aa16c00f80a9285df8473a433f8b9"),
    (1645, "c7948fac05f54aefb37f86a22d0c8e80313208e8735a765ed22175a5887c8613"),
    (1651, "001fe1672a2fe7165cf068d0b5a2e2d281503fa76d9a5b221da9c1f0b650b94a"),
    (1655, "3125e7df3b9ab4506146215322cfd58e4f84f954ed436173de403c90c11a6595"),
    (1665, "3bc38fa9507216f48359804fafbb4ef3d1d8803689b00d37f07b359bce07391a"),
    (1666, "ad30ccaf8e00b4f528ea2300241f2b226676b5dacf7d3b7a7074690ad47b2714"),
    (1682, "8bd2a975c7dc1fa86766cb3fd12e0c64613493e7f5b8ffc768b144239683ce23"),
    (1688, "05cf0f2ecc970a744d9b691f77e0784142a5d21e83da6e8be601e38f2efcf29e"),
    (1701, "04c2d3814a24f2a68e18a083ca576227ae9fffcc21ce47658445e2b700649af3"),
    (1704, "91d3a6fa0b0155744f1d363ce11f29215c5a9508c3ea17502b4efffc47bb4237"),
    (1714, "fc60de1ca62f13990181cea3387607c8de86d8103f9ea34e18e11b6a2016fd8f"),
    (1752, "8890418e3bb1d59a4e3313041a27c724e9d85137db3ccc8799014330645e9d59"),
    (1759, "1e79818e13d5a64ea1fc52e6446895d6083685511e1c409987413235403290cf"),
    (1760, "6d865bb466ddf4af26a57ec53e0cac692079009967b6e492449e04f18a3e8b83"),
    (1761, "607ff05dfcc7e51bbf6726dbf9297db7e80451470c33a41fe0c31ae2851b7678"),
    (1768, "dbe4bb8432256681e1b4b8959ce0a7bf2485bf5889859da7514c00239a4088e8"),
    (1769, "1b7796906486fa29af60d06cc2a20818a5fe8acf84a6b0a0860160954f11f4fc"),
    (1770, "f7c51386cb0fb690c2f94ad5d5f28cdb0935f02e46e67004785f7fa8174a83c4"),
    (1798, "364dad9fb803b267bf74697679a9a575efa97724f0cdd8c7c38e6b2b29bd54ef"),
    (1811, "168f3d4eb84093b6df9ebb2d7367df6b86aa2f7203a48e473a7f22dac2f45d33"),
    (1814, "1e78f4ab4d9762644de47c66892091e74aa99e52de4898868a53768bb945a039"),
    (1832, "715879a3afea748d6a91d4996d148f00cab13323d1a509f40885be47dc1abf4c"),
    (1835, "3161540a8770cd9758fe7e39766335f9e8a0fccf4d640db8ac26e2170b61830c"),
    (1848, "d4d9df92782b228000a3975a70533cd45c4efe6cda745ccce1fda373022545de"),
    (1850, "6aa9452eabfdbf6b166979940273d3d86b0c9cf8e347a44a2ab3f03c9bfe3de3"),
    (1864, "f60d4e100d663549e8dfb2ce6acd3bb23c2ba4bde968b9d4beff9259c8a8b42e"),
    (1865, "9bdf3490b9e8195b0383b9005dbb937a90f949364531e85123b8cda8feb262e7"),
    (1942, "f38a7c8647ecdd7902bd80eb9f0f2befb094e09e656e587ef09abc1aef4a7c65"),
    (1960, "f54bc521d45717c2a641a609b2850063c8efd574553b45b2b2842e391e5d25f1"),
    (2013, "b7fa30b5a70299b80c7a8e912986fe39d510700d2caa694e69545a281da25027"),
    (2016, "23d6307cb527c21f68d9dbec82796d5545583d78f7e68eae9c555079ef9d2a03"),
    (2021, "76c4711888fd8c75f67e0d7c1f9b3f5bf6ccc686c6d9bc3f9a5d3f3e083fa99b"),
    (2042, "536e5ec6981852256b12023cc2ec378d2d347312f815a409f75a723c86bb30bf"),
    (2052, "841c9b663e4b80d8117300fd659032cce4bd56606a403b1051105da911ae263a"),
    (2075, "943654081166e58bf087a27186ea0efc4e08c2723e5645e97259c12e5c5425a2"),
    (2078, "d83a559f6ed71cfec68892ac2022c5a338572328da4d5a8f87a75ad0133673c8"),
    (2081, "0b667f5619c48ffd43d0b6661ac80c3945107b361bfe86252966071702453b70"),
    (2083, "221f7031f9c9d75748f26769d7d1b632fb49e68ca8c855ac91cadf52c542dbcc"),
)

_SELECT = sa.text("""
    SELECT id, tax_year, taxpayer_identifier_hash, spouse_identifier_hash, filing_status,
           return_type, return_identity_key, identity_status, raw_data
      FROM drake_client_returns
     WHERE id = ANY(:ids)
     ORDER BY id
""")

_UPDATE = sa.text("""
    UPDATE drake_client_returns
       SET return_type = :return_type,
           return_identity_key = :new_key,
           agi = :agi,
           preparer_fee = :preparer_fee,
           prepare_date = :prepare_date,
           review_date = :review_date,
           approved_date = :approved_date,
           complete_date = :complete_date,
           federal_product = :federal_product,
           federal_ack_date = :federal_ack_date,
           federal_ack_code = :federal_ack_code,
           state_product = :state_product,
           state_ack_date = :state_ack_date,
           state_ack_code = :state_ack_code
     WHERE id = :id
       AND return_identity_key = :old_key
""")


def _ordered_values(raw_data, *, short: bool):
    """The export row's values, in header order, out of the preserved ``raw_data``.

    JSONB sorts its keys, so the order has to come from the header. ``short`` asks for the row as the
    export produced it — one field fewer, the trailing unnamed column absent.
    """
    if isinstance(raw_data, str):
        raw_data = json.loads(raw_data)

    header = CLIENT_EXPORT_HEADER[:-1] if short else CLIENT_EXPORT_HEADER
    return [("" if raw_data.get(name) is None else str(raw_data.get(name))) for name in header]


def _repaired_values(raw_data) -> dict:
    """Re-read one stored malformed row through the corrected normalization."""
    shape = normalize_row(CLIENT_EXPORT_HEADER, _ordered_values(raw_data, short=True))

    if shape.status != NORMALIZED:
        raise RuntimeError(f"row does not match the proven short-row shape: {shape.detail}")

    row = {key: value for key, value in to_mapping(CLIENT_EXPORT_HEADER, shape.values).items()
           if key is not None}

    return {
        "return_type": (row.get("Type") or "").strip() or None,
        "agi": parse_decimal_value(row.get("AGI")),
        "preparer_fee": parse_decimal_value(row.get("Prep_Fee")),
        "prepare_date": parse_date_value(row.get("Prepare - Date")),
        "review_date": parse_date_value(row.get("Review - Date")),
        "approved_date": parse_date_value(row.get("Approved - Date")),
        "complete_date": parse_date_value(row.get("Complete - Date")),
        "federal_product": (row.get("e-File Product #1") or "").strip() or None,
        "federal_ack_date": parse_date_value(row.get("e-File ACK Date #1")),
        "federal_ack_code": (row.get("e-File ACK Code #1") or "").strip() or None,
        "state_product": (row.get("e-File Product #2") or "").strip() or None,
        "state_ack_date": parse_date_value(row.get("e-File ACK Date #2")),
        "state_ack_code": (row.get("e-File ACK Code #2") or "").strip() or None,
    }


def _original_values(raw_data) -> dict:
    """Re-read one stored row exactly as the un-normalized importer did — for the downgrade."""
    row = {key: value
           for key, value in to_mapping(CLIENT_EXPORT_HEADER,
                                        _ordered_values(raw_data, short=True)).items()
           if key is not None}

    return {
        "return_type": (row.get("Type") or "").strip() or None,
        "agi": parse_decimal_value(row.get("AGI")),
        "preparer_fee": parse_decimal_value(row.get("Prep_Fee")),
        "prepare_date": parse_date_value(row.get("Prepare - Date")),
        "review_date": parse_date_value(row.get("Review - Date")),
        "approved_date": parse_date_value(row.get("Approved - Date")),
        "complete_date": parse_date_value(row.get("Complete - Date")),
        "federal_product": (row.get("e-File Product #1") or "").strip() or None,
        "federal_ack_date": parse_date_value(row.get("e-File ACK Date #1")),
        "federal_ack_code": (row.get("e-File ACK Code #1") or "").strip() or None,
        "state_product": (row.get("e-File Product #2") or "").strip() or None,
        "state_ack_date": parse_date_value(row.get("e-File ACK Date #2")),
        "state_ack_code": (row.get("e-File ACK Code #2") or "").strip() or None,
    }


def _load(connection, expected_keys: dict[int, str]):
    """Fetch the cohort and prove it is exactly what was frozen. Raises rather than repairing.

    An EMPTY result is not drift: a fresh development database, a CI database and a restore
    rehearsal all upgrade through this revision without ever having imported Drake, and there is
    nothing there to repair. A PARTIAL result is drift, and fails closed — that is a database that
    holds some of the cohort and has lost or altered the rest.
    """
    rows = connection.execute(_SELECT, {"ids": list(expected_keys)}).mappings().all()

    if not rows:
        return []

    if len(rows) != len(expected_keys):
        raise RuntimeError(
            f"cohort is {len(rows)} rows, expected {len(expected_keys)} — refusing to migrate")

    for row in rows:
        if row["return_identity_key"] != expected_keys[row["id"]]:
            raise RuntimeError(f"row {row['id']} does not carry its frozen identity key")

        if row["identity_status"] != "identified":
            raise RuntimeError(f"row {row['id']} is not 'identified'")

    return rows


def _apply(connection, rows, *, values_for, expected_return_type, other_null_check):
    """Re-key every row, or none of them. Every guard runs before the first write."""
    planned = []
    old_keys = {row["return_identity_key"] for row in rows}

    for row in rows:
        values = values_for(row["raw_data"])
        new_key = compute_return_identity_key(
            row["tax_year"], row["taxpayer_identifier_hash"], row["spouse_identifier_hash"],
            values["return_type"], row["filing_status"],
        )

        if values["return_type"] != expected_return_type:
            raise RuntimeError(
                f"row {row['id']} re-reads as {values['return_type']!r}, "
                f"expected {expected_return_type!r}")

        if new_key is None or new_key == row["return_identity_key"]:
            raise RuntimeError(f"row {row['id']} would keep its identity key — nothing to re-key")

        planned.append((row, values, new_key))

    new_keys = [new_key for _row, _values, new_key in planned]

    if len(set(new_keys)) != len(new_keys):
        raise RuntimeError("two rows would be given the same identity key")

    owned = connection.execute(
        sa.text("SELECT id FROM drake_client_returns"
                " WHERE return_identity_key = ANY(:keys) AND NOT (id = ANY(:ids))"),
        {"keys": new_keys, "ids": [row["id"] for row, _values, _key in planned]},
    ).scalars().all()

    if owned:
        raise RuntimeError(f"target identity keys are already owned by rows {sorted(owned)}")

    stranded = connection.execute(
        sa.text(other_null_check), {"ids": [row["id"] for row, _values, _key in planned]},
    ).scalar_one()

    if stranded:
        raise RuntimeError(f"{stranded} row(s) outside the frozen cohort are in the wrong state")

    for row, values, new_key in planned:
        result = connection.execute(_UPDATE, {
            "id": row["id"], "old_key": row["return_identity_key"], "new_key": new_key,
            **{column: values[column] for column in _REPAIRED_COLUMNS},
        })

        if result.rowcount != 1:
            raise RuntimeError(f"row {row['id']} did not update exactly once")

    # The cheapest proof that every write landed: after the loop, no row may still carry any
    # pre-migration key. A rowcount check per statement can only speak for its own row.
    remaining = connection.execute(
        sa.text("SELECT count(*) FROM drake_client_returns WHERE return_identity_key = ANY(:keys)"),
        {"keys": sorted(old_keys)},
    ).scalar_one()

    if remaining:
        raise RuntimeError(f"{remaining} row(s) still carry a pre-migration identity key")

    return len(planned)


def upgrade() -> None:
    connection = op.get_bind()
    expected = dict(_COHORT)
    rows = _load(connection, expected)

    if not rows:
        return

    for row in rows:
        if row["return_type"] is not None:
            raise RuntimeError(f"row {row['id']} already has a return_type — already migrated?")

    _apply(
        connection, rows,
        values_for=_repaired_values,
        expected_return_type=_EXPECTED_RETURN_TYPE,
        other_null_check="SELECT count(*) FROM drake_client_returns"
                         " WHERE return_type IS NULL AND NOT (id = ANY(:ids))",
    )


def downgrade() -> None:
    """Restore the pre-migration values exactly, from the same untouched ``raw_data``.

    Lossless: every column this migration wrote is re-derived by mapping the stored row WITHOUT the
    normalization, which is precisely what produced those values in the first place. ``raw_data`` was
    never written, so nothing had to be remembered.
    """
    connection = op.get_bind()
    expected = {row_id: key for row_id, key in _COHORT}
    ids = list(expected)

    rows = connection.execute(_SELECT, {"ids": ids}).mappings().all()

    if not rows:
        return

    if len(rows) != len(ids):
        raise RuntimeError(f"cohort is {len(rows)} rows, expected {len(ids)} — refusing to downgrade")

    for row in rows:
        if row["return_type"] != _EXPECTED_RETURN_TYPE:
            raise RuntimeError(f"row {row['id']} is not in the migrated state")

    taken = connection.execute(
        sa.text("SELECT id FROM drake_client_returns"
                " WHERE return_identity_key = ANY(:keys) AND NOT (id = ANY(:ids))"),
        {"keys": [expected[row_id] for row_id in ids], "ids": ids},
    ).scalars().all()

    if taken:
        raise RuntimeError(f"pre-migration identity keys are already owned by rows {sorted(taken)}")

    for row in rows:
        values = _original_values(row["raw_data"])

        if values["return_type"] is not None:
            raise RuntimeError(f"row {row['id']} does not re-read as a NULL return_type")

        result = connection.execute(_UPDATE, {
            "id": row["id"], "old_key": row["return_identity_key"],
            "new_key": expected[row["id"]],
            **{column: values[column] for column in _REPAIRED_COLUMNS},
        })

        if result.rowcount != 1:
            raise RuntimeError(f"row {row['id']} did not revert exactly once")
