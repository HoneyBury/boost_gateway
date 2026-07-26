# Security Policy

## Supported versions

Security fixes target the current `v3.6.x` maintenance line. Older tags and archived
deployment guidance are retained for traceability but do not receive guaranteed fixes.
The exact current release and supported platform boundaries are recorded in
[`docs/current-state.md`](docs/current-state.md).

## Reporting a vulnerability

Do not open a public issue or pull request for an undisclosed vulnerability. Email
`zoujiahe389+boost-gateway-security@gmail.com` with a concise description, affected
versions, reproduction conditions, impact, and a safe contact channel. Do not attach live
credentials, production data, or destructive proof-of-concept payloads.

Private vulnerability reporting is not yet an active repository control. GitHub private
vulnerability reporting is external repository state and must be verified before use. When
it is enabled and verified, its `Report a vulnerability` form becomes the preferred channel;
this document must then be updated in the same governed change.

## Response expectations

The maintainer will aim to acknowledge a report within three business days, provide an
initial triage within seven business days, and send at least weekly status updates while a
confirmed issue remains under embargo. These are response targets, not a guaranteed fix
date. Severity, affected supported versions, operational mitigations, and release evidence
determine the remediation schedule.

## Coordinated disclosure

Reporter and maintainer should agree on a disclosure date after a supported fix or
mitigation is available. A security release follows the normal governed release evidence
path. Credit is provided when requested and legally permissible. If a report is not a
security issue, it will be redirected to the public support path without publishing
confidential reproduction details.
