# Two-repository workspace deployment

The application is maintained in two repositories:

- Backend: https://github.com/minasamir1401/masry-stream-api-
- Frontend: https://github.com/minasamir1401/masry-stream-web

The Compose files in this directory were preserved from the shared workspace root. They expect the backend and frontend to be checked out into sibling directories named `backend` and `frontend`:

```text
workspace/
  backend/
  frontend/
  .env
```

Run Compose from that parent workspace, explicitly setting its project directory:

```bash
docker compose --project-directory . --env-file .env -f backend/deployment/docker-compose.yml config
docker compose --project-directory . --env-file .env -f backend/deployment/docker-compose.yml up -d --build
```

For Dokploy's shared proxy network, use `backend/deployment/docker-compose.dokploy.yml` with the same parent project directory. `WORKSPACE_DOKPLOY_GUIDE.md` preserves the original shared-workspace instructions; its monorepo paths refer to this assembled workspace, not to either repository on its own.

`deployment/.env.example` is a workspace environment template. Copy it into the parent workspace as `.env` and set your own production values. Real environment files, credentials, databases, sessions, generated signing keys, translation test files, and runtime previews are not included in either repository.

If deploying the repositories as separate Dokploy applications, each repository's root Dockerfile is the build entry point; configure the backend URL and database in Dokploy rather than using the combined-workspace Compose templates.

Preserve the database and backend data volumes when updating. They contain saved anime URL identities, account/session data, cached translations, and the generated private signing key. See [the complete change and validation notes](../docs/AUDIT_FIXES.md) before applying the update.
