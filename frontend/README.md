## Frontend (Next.js app)

Modern Tailwind-based UI for ICICI Breeze, talking to the legacy FastAPI backend.

### Running in dev mode

From this directory:

```bash
npm install
NEXT_PUBLIC_BACKEND_URL=http://localhost:3000 npm run dev
```

Then open `http://localhost:3000`.

### Key implementation notes

- Uses the App Router (`src/app`) with an app shell (`AppShell`) for the trading workspace.
- Talks to the backend using `src/lib/api-client.ts`, which respects `NEXT_PUBLIC_BACKEND_URL` and sends cookies (`credentials: "include"`).
- Fetches data from the existing JSON endpoints exposed by the FastAPI backend:
  - `/home/data`, `/dashboard/vix/options`
  - `/portfolio/data`
  - `/order/data`
  - `/hedge/data`, `/vertical-spread/data`, `/uncovered-shorts/data`
- Auth UI is implemented in `/login` and `/register`, but the underlying flows remain the same as the legacy backend.
