# Identity key rotation and rollback

## Static key ring admission

Production remains an external RS256 verifier. Configure exactly one public key
source: `jwt_public_key_pem`, `jwt_key_ring`, or `jwks_uri`. Symmetric secrets,
private keys, local registration, guest identity and refresh-token issuance remain
forbidden in production.

For a static rotation, deploy both old and new public keys under distinct non-empty
`kid` values. Verify tokens signed by both keys, then change the identity provider to
emit the new `kid`. Remove the old key only after the maximum token lifetime plus
clock skew. Missing, unknown and duplicate `kid` values fail closed.

## JWKS admission

Use an HTTPS URI whose hostname appears exactly in `jwks_allowed_hosts`. Redirects,
userinfo, non-allowlisted hosts and production HTTP are rejected. Loopback HTTP is
available only when `jwks_allow_loopback_http=true` for isolated integration tests.
The Login Backend must fetch and validate at least one RSA/RS256 verification key
before it begins listening.

Record `snapshot_age_seconds`, `snapshot_stale`, `last_success_epoch_seconds`,
`refresh_attempts`, `refresh_failures`, `unknown_kid_rejections` and `key_count` from
the resolver metrics snapshot. Unknown `kid` rejects the current token and requests
a rate-limited single-flight background refresh; clients must retry with a new
request. Requests never synchronously fetch JWKS.

During an IdP outage, a previously loaded snapshot remains usable through the
configured TTL and stale grace. After TTL plus grace, verification returns
`jwks_stale_expired`. A JWKS-only process without an initial snapshot fails startup.

## Rollback

Prepare a separately reviewed static key-ring configuration before enabling JWKS.
Rollback replaces the deployment configuration with that ring and restarts the
Login Backend; a request never falls back from JWKS to static keys implicitly.
Continue enforcing issuer, audience, subject and `now >= exp` checks. Do not place
tokens, PEM content or JWK modulus values in logs or evidence summaries.
