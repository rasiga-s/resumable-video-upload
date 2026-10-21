# Resumable Video Upload frontend

A React and TypeScript browser client for the FastAPI resumable video upload API in this repository. It sends file chunks directly to S3/LocalStack using API-generated presigned URLs, shows byte-level progress, supports pausing and resuming, and stores upload session metadata in browser local storage.

## Run locally

LocalStack persistence requires a LocalStack Pro auth token. From the repository root, create a private `.env` file and enter your token:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and replace `your-localstack-auth-token` with your token. `.env` is git-ignored and should not be committed.

Start the API, PostgreSQL, and persistent LocalStack from the repository root:

```powershell
docker compose up -d
```

From this `frontend` directory, install the JavaScript dependencies and start Vite:

```powershell
npm install
npm run dev
```

Open the local URL printed by Vite (usually `http://localhost:5173`). The API defaults to `http://localhost:8000`. The frontend uses the local demo API key `test-api-key-123`, matching the default user seeded by the API. To use a different API URL or key, copy `.env.example` to `.env.local`, edit `VITE_API_BASE_URL` or `VITE_API_KEY`, and restart Vite. The bundled key is for local development only; do not use it in production.

The browser must be able to reach both the API and the S3 endpoint in the generated presigned URLs. The default Compose setup exposes them at `localhost:8000` and `localhost:4566`.

## Resume behavior

The browser stores the upload ID and selected file metadata locally. To resume after a pause or page reload, select the same file again from the same browser profile. The UI asks the API for confirmed S3 parts and uploads only the missing parts. Browser storage does not contain the video bytes; the original file must be selected again.

LocalStack snapshots S3 changes as requests are made and restores them from the `localstack_data` volume after a restart. This favors preserving confirmed upload parts over speed and may slow local uploads. Use Docker Desktop's normal **Stop** and **Start** actions; do not delete the containers' data volumes. If storage has already lost an in-progress multipart session, the API creates a replacement session when resume is requested, and all parts must be uploaded again.
