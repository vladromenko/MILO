# Security Policy

## Reporting

Please report security issues privately to the repository owner instead of
opening a public issue. Include the affected commit, reproduction steps, and the
expected impact. Do not include captured faces, voice recordings, SSH keys,
network passwords, access codes, or memory databases.

## Deployment Boundary

MILO is a research prototype, not a safety-certified controller. The phone panel
must remain on the private robot network. Edge HTTP and DDS endpoints are bound
to loopback and transported through pinned-key SSH; do not expose them directly
to an untrusted network.

Private files under `config/` and `data/` are intentionally excluded from Git.
Rotate the shared token, Wi-Fi password, access code, and SSH key after suspected
disclosure.
