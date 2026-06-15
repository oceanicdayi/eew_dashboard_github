# EEW Dashboard GitHub Deploy

This repository contains a deployable Gradio dashboard for replaying Taiwan EEW fixture data and publishing the app to Hugging Face Spaces through GitHub Actions.

## Files

- `app.py` — Gradio dashboard UI.
- `test_loop.py` — replay fixture validation used before deployment.
- `deploy.py` — uploads the app and fixtures to the configured Hugging Face Space.
- `fixtures/` — sample EEW JSON and CSV replay data.
- `.github/workflows/deploy-hf.yml` — GitHub Actions deployment workflow.

## Deployment

Set the repository secret `HF_TOKEN` in GitHub, then push to `main` or run the workflow manually from the Actions tab.

Default Space target:

```text
oceanicdayi/Eew_dashboard
```

To change the Space target, update `EEW_SPACE_ID` in `.github/workflows/deploy-hf.yml` or set the environment variable locally when running `deploy.py`.
