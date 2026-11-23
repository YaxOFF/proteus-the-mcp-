# Publishing a release

Done by the maintainer with their own accounts; no tokens are stored in this repository.

1. Verify: offline tests, `-m proteus` live tests on a real Proteus, `claude plugin validate .`.
2. Build: `py -3 -m pip install build twine` then `py -3 -m build` (writes `dist/`).
3. PyPI: `py -3 -m twine upload dist/*` (asks for your PyPI API token).
   README contains the `mcp-name` marker the MCP registry uses to verify ownership.
4. MCP registry: install `mcp-publisher`, run `mcp-publisher login github`, then
   `mcp-publisher publish` from the repository root (reads `server.json`; keep its versions equal
   to `pyproject.toml`).
5. GitHub release: tag `vX.Y.Z`, attach the wheel and `MCPBRIDGE.DLL`.
