"""Client360 deployment readiness tooling (Windows Server).

A thin, dependency-light operator CLI (``python -m app.deploy <command>``) that REUSES the existing
config validation, Alembic migrations, readiness checks, identity/roles, and uvicorn runtime. It adds
no business module and no schema — only the deployment glue (config preflight, safe migrate, admin
grant, smoke, and Windows-service command construction) plus the PowerShell wrappers under
``deploy/windows/``.
"""
