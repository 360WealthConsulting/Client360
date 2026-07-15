"""Developer Demo Mode (feature/developer-demo-mode).

This package is DEMO-ONLY tooling. It is never imported by the production
application entrypoint (`app.main`). It runs against a dedicated `*_demo`
database, uses only fictional data, and reuses the real authentication and
authorization machinery — it does not weaken any security control and adds no
production login bypass.
"""
