# license-sentinel

<!-- mcp-name: io.github.wgd5678/license-sentinel -->
<!-- invisible on GitHub; the official MCP Registry validator reads it to prove this package is yours -->

[![License](https://glama.ai/mcp/servers/wgd5678/license-sentinel/badges/license.svg)](https://glama.ai/mcp/servers/wgd5678/license-sentinel)
[![Score](https://glama.ai/mcp/servers/wgd5678/license-sentinel/badges/score.svg)](https://glama.ai/mcp/servers/wgd5678/license-sentinel)

**Audit Python and npm dependency licenses for compliance before you ship — an MCP server for AI coding agents.**

An AI agent can add `pdf-renderer` to your project in one second. It will not tell you that
`pdf-renderer` is AGPL-3.0 and that shipping it inside a closed-source product is a license
violation. License data and compatibility rules are things a model cannot reliably recall —
packages relicense between versions (MongoDB → SSPL, Redis → BUSL, Elasticsearch → Elastic-2.0),
and "the source is on GitHub" does not mean "free to ship".

`license-sentinel` reads what is actually on your disk and judges it against how **you**
distribute your product.

- Works for **Python and npm in one pass** — existing MCP license tools are npm-only.
- **Runs locally over stdio. No network calls, no telemetry, nothing leaves your machine.**
- Verdicts, not raw data: `CLEAN` / `REVIEW` / `BLOCK`, each with the reason in plain language.

---

## Tools

| Tool | What it does |
|---|---|
| `audit_project(path, context)` | Scan a project's dependencies and return counts plus every BLOCKING and REVIEW item with reasons. |
| `check_package(names, context)` | Check specific packages or messy license strings *before* installing. Accepts `AGPL-3.0`, `BUSL-1.1`, `GPLv3`, `Apache License 2.0`, `MIT OR Apache-2.0`. |
| `generate_notices(path, output)` | Write a `THIRD-PARTY-NOTICES.md` attribution document for client hand-off. |

There is also a `pre_release_license_review` prompt that chains the audit into a go/no-go review.

## Install

```bash
# run without installing (recommended)
uvx --from license-sentinel license-sentinel

# or install
uv pip install license-sentinel
# or
pip install license-sentinel
```

## Configure your client

Claude Desktop / Cursor / Windsurf / VS Code Copilot / Zed all read the same shape:

```json
{
  "mcpServers": {
    "license-sentinel": {
      "command": "uvx",
      "args": ["--from", "license-sentinel", "license-sentinel"]
    }
  }
}
```

If you installed with pip instead, use `"command": "license-sentinel"` with no `args`.
Restart the client and the three tools appear.

## Distribution context

The same dependency is fine in one context and fatal in another, so every tool takes a
`context` argument:

| Context | Meaning | What it blocks |
|---|---|---|
| `proprietary` (default) | Closed-source product you distribute | GPL/AGPL/SSPL, BUSL/Elastic, non-commercial |
| `saas-backend` | Never distributed, only runs on your servers | AGPL/SSPL (network trigger), BUSL/Elastic |
| `permissive` | Your own project is MIT/Apache/BSD | Anything copyleft that would contaminate your terms |
| `copyleft-ok` | Your own project is GPL family | Only source-available and non-commercial |

## What it reads

- Python: `.venv/` / `venv/` / `env/` installed packages (`dist-info/METADATA`), `requirements.txt`, `pyproject.toml` (PEP 621, poetry, dependency-groups)
- npm: `node_modules/*/package.json` (including scoped packages), `package.json` dependencies

If a dependency is declared but not installed, it is reported with an `UNKNOWN` license rather
than silently dropped — an unlicensed dependency is all-rights-reserved by default.

## Privacy

No HTTP client is imported anywhere in this package. The scan is read-only (except
`generate_notices`, which writes the file you name). Nothing is uploaded.

## Limitations

- Not legal advice. It is a fast first pass that catches the expensive mistakes; have counsel
  review anything flagged.
- Transitive dependencies are read from what is installed. If you have no `.venv` and no
  `node_modules`, declared-only dependencies come back `UNKNOWN`.
- The current environment running the server is never scanned, so the server's own packages
  never pollute your report. Set `LICENSE_SENTINEL_SCAN_CURRENT_ENV=1` to change that.

## Development

```bash
uv sync
python tests/smoke_test.py     # 9 tests, no pytest needed
python tests/e2e_check.py      # calls the tools end to end
```

## License

MIT
