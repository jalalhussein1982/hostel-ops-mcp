# hostel-ops-mcp

MCP server over the hostel check-in system: remote Streamable HTTP, OAuth 2.1 scopes mapped to desk roles, safe write tools, confirmed erasure, PII redaction by scope.

Built on [`jalalhussein1982/hostel-checkin-system`](https://github.com/jalalhussein1982/hostel-checkin-system). MCP protocol revision 2026-07-28, MCP Python SDK 2.x.

**Status:** design stage. The design document is at [`docs/superpowers/specs/2026-10-11-hostel-ops-mcp-design.md`](docs/superpowers/specs/2026-10-11-hostel-ops-mcp-design.md). Implementation follows once the design is approved.

## Why this exists

The earlier servers are local, stdio and read-only. This one puts a production guest-registration system with personal data behind MCP over Streamable HTTP, as an OAuth 2.1 resource server with per-tool scopes, write tools guarded by the app's own state machine, PII minimised by scope, and destructive actions confirmed through Multi Round-Trip Requests.

## Related servers in this portfolio

- [`ihkam-eval-mcp`](https://github.com/jalalhussein1982/ihkam-eval-mcp) — read-only evaluation harness over a legal RAG (stdio)
- `phase1/siem-mcp` in [`conflict-trends-dashboard`](https://github.com/jalalhussein1982/conflict-trends-dashboard) — conflict analytics over PostGIS (stdio)
- `energy_platform/mcp` in [`energy-platform`](https://github.com/jalalhussein1982/energy-platform) — the contributor path of an ingestion platform (stdio)
- `hostel-ops-mcp`, `geoecon-pipeline-mcp`, `coin-wargame-mcp` — this series: remote transport and OAuth, long-running jobs, and MCP as an agent harness
