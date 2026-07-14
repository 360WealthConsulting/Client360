# Release 0.9.9 — Deployment Gates

These gates must be satisfied before Release 0.9.9 (Platform Consolidation) is
deployed to production. They are approved outcomes of the Phase 1 (Microsoft 365
Token Security) review and remain binding through RC12.

## Configuration & key management gates

1. **`MICROSOFT_TOKEN_KEY` configured in every environment.** The Fernet key must
   be present in each environment (development, staging, production). Without it,
   token encryption/decryption fails closed and Microsoft 365 sync cannot run.
2. **Key backed up separately from the database.** `MICROSOFT_TOKEN_KEY` must be
   stored and backed up independently of the database. Losing the key makes all
   stored token caches undecryptable and forces every account to reconnect.

## Data migration gate

3. **Existing Microsoft 365 connections must reconnect once.** Pre-0.9.9 plaintext
   tokens are no longer read. After deploy, each connected account must complete
   the OAuth reconnect flow once to populate the encrypted MSAL cache. The status
   page surfaces which accounts still require reconnection.

## Live Microsoft tenant verification gate

4. **A live Microsoft tenant test must verify the full token lifecycle end to end:**
   - OAuth authorization
   - Encrypted MSAL cache persistence
   - Access-token expiration
   - `acquire_token_silent` refresh
   - Mail sync
   - Calendar sync
   - Document sync
   - Sync-health status

   This exercises the real Graph refresh path that unit tests cover only with
   mocked MSAL.

## Deferred items (must remain documented)

5. **Key rotation** for `MICROSOFT_TOKEN_KEY` is deferred to a later release. No
   rotation/re-encryption mechanism ships in 0.9.9.
6. **Removal of the legacy plaintext token columns** (`access_token`,
   `refresh_token` on `microsoft_accounts`) is deferred one release; they are
   retained nullable to allow rollback to v0.9.8.

These deferred items must stay documented until they are scheduled and completed.
