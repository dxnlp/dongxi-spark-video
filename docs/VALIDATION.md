# Repository preparation checks

Checked on 2026-09-25. Preparation did not start model loading, inference, downloads or a new environment on Spark.

## Passed

- 41 Python standard-library CPU tests, including HTTP tests on an ephemeral local port with model/Docker calls mocked.
- Python compilation for the portal, scripts, guards and tests.
- Ruff 0.16.8 formatting and undefined/unused-name checks (`--select F`).
- JavaScript syntax check after Prettier 3.6.2 formatting of the static UI.
- Bash parsing for bootstrap, install and download scripts.
- Git staged whitespace check, local Markdown link check, and checks that runtime/credential/model/media paths are ignored while configuration and CI files remain included.
- Staged-content scan for common token/private-key patterns and original personal machine paths. This is a bounded pattern check, not a guarantee against every possible secret.

The original Spark portal process and client SSH tunnel were stopped. A final read-only check found no project processes, no running Docker containers and no GPU compute processes; host memory was 3.54 GiB used / 118.15 GiB available. The old continuation schedule remained paused. Existing weights and environments remained on disk; no driver, network or other project environment was changed.

## Not executed during packaging

The fresh-install `scripts/setup.py` orchestration, new launcher/shutdown commands against a newly deployed checkout, and a new end-to-end GPU render have not been exercised on Spark. They were packaged from the measured runtime and checked with CPU tests/static review. The existing benchmark figures describe the earlier working installation, not a new execution of this repository.

GitHub Actions is configured but has not run on GitHub. No remote repository was created or pushed during preparation.
