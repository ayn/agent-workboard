# Agent guide

Read CONTRIBUTING.md before changing code. Keep the dashboard agent-agnostic and
usable with Python's standard library. Use branches and pull requests.

Never check in real task files, private endpoints, usernames, credentials, raw
payloads, or screenshots of personal workspaces. Use synthetic fixtures only.
Preserve explicit projection, safe text rendering, the HTTP allowlist and bounded
source fetches. Test the real HTTP/SSE lifecycle where mocks would hide failures.

For setting up this board in another project, use docs/agent-setup.md. Do not
replace that project's existing agent instructions. The board shows producer
reports and must not claim to infer real agent/process state automatically.
