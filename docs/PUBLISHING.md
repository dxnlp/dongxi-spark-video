# Preparing a GitHub upload

Repository name: **dongxi-spark-video**.

The prepared repository contains source, tests, dependency constraints and documentation. It excludes downloaded models, upstream checkout, environments, outputs, prompts, job records, raw diagnostics, credentials and machine-specific state. There is no generated demo video in the initial package.

Before uploading:

```bash
python3 scripts/test.py
git status --short
git diff --cached --stat
git diff --cached --check
git ls-files
```

Review the MIT license for this portal/orchestration code and the separate upstream/model terms in `THIRD_PARTY_NOTICES.md`. Inspect the staged files rather than relying solely on `.gitignore`: ignored patterns do not remove already-tracked files.

When ready, create the first commit and publish using the GitHub account and visibility you choose:

```bash
git add .
git commit -m "Prepare Dongxi Spark Video portal"
# Choose ONE visibility flag, after reviewing the staged contents:
gh repo create dongxi-spark-video --source=. --private --push
# Use --public instead if you intend a public repository.
```

No GitHub repository is created by setup, test, launch or shutdown commands. Publication is a separate action. CI needs only read access to repository contents, installs no model, and performs no GPU render.
