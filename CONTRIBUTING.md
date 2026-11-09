# Contributing to EyesRhythm

Thank you for your interest in improving EyesRhythm. This guide explains how to report problems, suggest ideas, and submit changes.

## Before you start

- Read the [Code of Conduct](CODE_OF_CONDUCT.md). It applies to issues, pull requests, and every other project space.
- For security problems, follow [SECURITY.md](SECURITY.md). Do not open a public issue.
- EyesRhythm is a wellness tool, not a medical device. Changes must not add diagnostic or treatment claims.

## Reporting a bug

Open an issue with the **Bug report** template and include:

- Your EyesRhythm version (Settings → About) and operating system
- Your camera setup (built-in, USB, or Hybrid)
- Steps to reproduce the problem, what you expected, and what happened
- Relevant log excerpts, if any:
  - macOS: `~/Library/Logs/EyesRhythm.log` and `~/Library/Logs/EyesRhythm_crash.log`
  - Windows: `%LOCALAPPDATA%\EyesRhythm\crash.log` and `%LOCALAPPDATA%\EyesRhythm\runtime.log`

Please do not attach photos or video of your face.

## Suggesting a feature

Open an issue with the **Feature request** template. Describe the problem you want to solve before describing a solution. This helps the discussion stay focused on what users need.

## Development setup

You need Python 3.10–3.12 (3.11 recommended), Node.js 18 or later, and a webcam.

```bash
# macOS / Linux
./for_mac/setup.sh
./for_mac/run.sh
```

For Windows, see [`for_windows/README.md`](for_windows/README.md). For hot reload while working on the UI:

```bash
python -m app.main --browser        # terminal 1
cd frontend && npm run dev          # terminal 2
```

Start with [`docs/CODE_GUIDE.md`](docs/CODE_GUIDE.md) for an overview of the architecture.

## Submitting a pull request

1. Fork the repository and create a branch from `main`.
2. Keep each pull request focused on one change.
3. Match the style of the surrounding code. Python follows PEP 8, and the UI uses TypeScript with React function components.
4. Add or update tests in `tests/` when you change behavior.
5. Run the checks locally:

   ```bash
   python -m unittest discover -s tests
   cd frontend && npm run build
   ```

6. Update the documentation when behavior, settings, or shortcuts change. Add a line to [`CHANGELOG.md`](CHANGELOG.md) for user-visible changes.
7. Write commit messages as a short sentence in the imperative mood, for example: `Fix the Trace line after the camera wakes from idle.`
8. Open the pull request and fill in the template.

### Privacy requirements

EyesRhythm promises that nothing leaves the user's computer. Pull requests must not:

- Send camera frames, blink data, or usage data over the network
- Add analytics, telemetry, or third-party tracking
- Store images or video of the user

### Versioning

Releases follow [Semantic Versioning](https://semver.org/). When preparing a release, update `VERSION` in `app/version.py` and `MyAppVersion` in `for_windows/for_developers/EyeRhythm.iss`. `tests/test_version.py` checks that they match.

## License of contributions

EyesRhythm is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE). By submitting a contribution, you agree that it may be distributed under the same license.
