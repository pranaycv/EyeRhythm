# Security policy

## Supported versions

Security fixes are made for the latest release only. Please update to the newest version from [eyesrhythm.com](https://eyesrhythm.com) or the [releases page](https://github.com/pranaycv/EyeRhythm/releases/latest) before reporting.

| Version | Supported |
|---------|-----------|
| Latest release | Yes |
| Older releases | No |

## Reporting a vulnerability

Do not report security problems in public issues, discussions, or pull requests.

Email **[hello@eyesrhythm.com](mailto:hello@eyesrhythm.com)** with the subject line `Security report` and include:

- The EyesRhythm version and operating system
- A description of the issue and its impact
- Steps to reproduce it, or a proof of concept
- Any suggested fix, if you have one

You will receive an acknowledgment within 5 business days. We will keep you informed while we investigate. Once a fix is released, we can credit you in the changelog if you wish.

## Scope

EyesRhythm processes camera frames on the user's computer and serves its interface from a local server bound to `127.0.0.1`. Reports in these areas are especially welcome:

- Camera data, blink data, or files in `~/.eyerhythm_blinkcounter/` being exposed or sent off the device
- The local API (port 8742) being reachable from other machines or from untrusted web pages
- Problems with the installers or the Windows unblock helper

Issues in third-party libraries should also be reported to their maintainers. See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) for the list of components.
