# Resumable Video Upload frontend

A React and TypeScript browser client for the FastAPI resumable video upload API in this repository. It sends file chunks directly to S3/LocalStack using API-generated presigned URLs, shows byte-level progress, supports pausing and resuming, and stores upload session metadata in browser local storage.

## Run locally

Start the API, PostgreSQL, and LocalStack from the repository root:

```powershell
docker compose up -d
```

From this `frontend` directory, install the JavaScript dependencies and start Vite:

```powershell
npm install
npm run dev
```

Open the local URL printed by Vite (usually `http://localhost:5173`). The API defaults to `http://localhost:8000`. To use a different API URL, copy `.env.example` to `.env.local`, edit `VITE_API_BASE_URL`, and restart Vite.

The browser must be able to reach both the API and the S3 endpoint in the generated presigned URLs. The default Compose setup exposes them at `localhost:8000` and `localhost:4566`.

## Resume behavior

The browser stores the upload ID and selected file metadata locally. To resume after a pause or page reload, select the same file again from the same browser profile. The UI asks the API for confirmed S3 parts and uploads only the missing parts. Browser storage does not contain the video bytes; the original file must be selected again.
