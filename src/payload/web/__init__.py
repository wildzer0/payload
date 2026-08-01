"""
Local web interface for payload ('pld serve') — exposes the same
functionality as the CLI over HTTP/SSE, for those who'd rather not use
the terminal. Only imported by 'pld serve' (lazy import in cli.py): it
must NEVER be imported at module level by payload.cli, otherwise
Starlette/uvicorn would become mandatory dependencies for the whole
CLI instead of just the optional 'serve' extra."""
