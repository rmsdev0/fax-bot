# Security Policy

fax-bot handles other people's fax correspondence, which makes privacy bugs
security bugs. If you find a way to read, alter, or publish correspondence you
shouldn't be able to — or any other vulnerability — please report it privately.

## Reporting

Use GitHub's private vulnerability reporting on this repository
("Security" tab → "Report a vulnerability"). Please do not open a public
issue for anything exploitable.

You can expect an acknowledgment within a week. This is a one-person hobby
service; fixes ship as fast as one person reasonably can.

## Scope notes

- The webhook endpoint relies on Telnyx's Ed25519 signatures
  (`WEBHOOK_VERIFY=true` in any reachable deployment).
- `/admin/*` requires the `X-Admin-Token` header and is disabled entirely when
  no token is configured.
- Anything that lets one fax number affect another number's thread, opt-out
  state, or gallery items is a vulnerability — please report it.
