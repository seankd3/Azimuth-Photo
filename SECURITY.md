# Security policy

## Reporting a vulnerability

Please report security issues privately through [GitHub security advisories](https://github.com/seankd3/azimuth-photo/security/advisories/new). Do not open a public issue for a vulnerability.

Include a clear description, reproduction steps, affected version, and any proof of impact. We will confirm receipt, investigate, and coordinate disclosure with you.

## Scope

2.0 is pre-release: report against `main`. The app is one local desktop
process with no listening port and no accounts, so the realistic surface is
file handling (RAW and sidecar parsing) and the frozen bundle itself.
