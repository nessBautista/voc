# VOC

A Python workspace for collecting and preparing customer reviews. Voice of Customer.

## Functional Organization
Code is grouped into:
- `src/voc_ml` (ML workflows): All functionality related to ML traning.
- `src/voc` (dataset framework): voc will serve future renderizations for dashboards/MIS
- `src/voc_dev` (workspace tools): development tools we require to manage this development environment.


## Local web demo

For the Windows dashboard and chat demo on `voc-dev`, follow [the setup guide](docs/setup-guide.md). Maintainers prepare data access separately with [the admin guide](docs/local-demo-admin.md). The existing [Operator workspace setup](docs/operator-setup-guide.md) remains available.

On macOS/Linux, if you already use `just`, run from the repository root:

```bash
just demo-configure
# Edit config/demo.local.env: S3 reader credentials and your OpenRouter key.
just demo-setup
just demo-up
```

Use `just demo-status`, `just demo-logs`, `just demo-sync`, and `just demo-down`
(`demo-stop` is an alias). Stopping preserves data and conversations. These
recipes call the existing demo launcher; Windows installation still uses
`scripts/demo.ps1` without requiring `just`.
