# Security policy

This policy covers the fork [pavlikru/lumalou](https://github.com/pavlikru/lumalou)
and the `lumalou-gld09` package published from it on PyPI.

## Supported versions

Only the latest `lumalou-gld09` release receives fixes.

## Reporting a vulnerability

Do not open a public issue for a security problem. Report it privately through
GitHub's private vulnerability reporting:
<https://github.com/pavlikru/lumalou/security/advisories/new>.

Include the affected version, the impact, steps to reproduce and, if you have
one, a suggested fix. Do not attach secrets, private keys, device addresses or
Bluetooth captures that identify a device.

## Scope

In scope: the `lumalou-gld09` package (import name `lumalou`) and the changes
this fork makes to the upstream code, for example device-key binding, the
strict response handling, the schedule and profile codecs and the CLI.

Reports about writes that could damage a device are in scope, including any
path that reaches the firmware-update (DFU) service `00001530-…` or sends
firmware/OTA commands. The library never does either on purpose.

Out of scope here:

- The original project [stramanu/lumalou](https://github.com/stramanu/lumalou)
  (the upstream `lumalou` PyPI and npm packages and the web app): report to
  that project. If a problem affects both, report it there and here.
- The Home Assistant integration: report to
  [pavlikru/ha-lumalou](https://github.com/pavlikru/ha-lumalou/security/advisories/new).
