# Cross-Platform Dogfood Bundle Design

**Goal:** Produce one credential-free zip that a Windows or macOS user can
extract and start with a double-click, assuming Python 3.11+ is installed.

## Payload

The archive contains the Python runtime source, prebuilt React/dashboard
assets, Streamlit configuration, demo data, local dependency manifest, and
platform launchers. It excludes tests, infrastructure, internal review
artifacts, local databases, caches, credentials, and repository metadata.

## Launch contract

- `START_SCUDO.cmd` supports Windows.
- `START_SCUDO.command` supports macOS.
- First launch creates `.venv`, upgrades pip, installs
  `backend/requirements-local.txt`, and runs `run_demo.py`.
- Later launches reuse `.venv`.
- The launchers never accept, persist, or log the Bedrock bearer key. The user
  pastes the separately delivered key into the masked Streamlit field.

## Verification

Generate a SHA-256 manifest, scan the staged payload for credential markers and
local state, extract the final zip into a clean directory, compile/import the
Python runtime, and launch the extracted app in offline mode for health checks.
The same application path has separately passed live Bedrock dogfooding.
