# Underwriting Copilot — frontend

Single-page React client for the Underwriting Copilot API. See the
[project README](../README.md) for what the tool does and how the backend works.

## Running it

The frontend is useless on its own — it renders what the API returns. Start the
backend first, in its own terminal:

```bash
cd backend && uvicorn app.main:app --reload
```

Then:

```bash
npm install && npm run dev
```

Open http://localhost:5173. Vite proxies `/api` to `127.0.0.1:8000`
(`vite.config.ts`), so the browser stays on one origin and CORS never enters the
picture locally.

If the backend reports `index_ready: false`, the page says so and prints the
command to fix it. Build the index with
`cd backend && python -m app.ingest.build_index`.

## Scripts

| Command | Does |
|---|---|
| `npm run dev` | Vite dev server on 5173 |
| `npm run build` | `tsc -b` then a production build into `dist/` |
| `npm run preview` | Serve the built `dist/` |
| `npm run lint` | oxlint |

## Structure

```
src/
  main.tsx              mount
  App.tsx               the whole page: query, auth, loading, error, results
  components/
    Disclaimer.tsx      non-dismissible "everything here is fictional" banner
    ProfileCard.tsx     what the tool understood, including what was not stated
    ResultView.tsx      branches on query_type; direct answers vs comparison grid
    VerdictCard.tsx     one carrier, with expandable cited evidence
  api/
    client.ts           typed fetch wrapper, in-memory secret
    types.ts            hand-written mirrors of the backend Pydantic models
```

## Conventions worth knowing before editing

- **No UI library, no icon library, no chart library.** React, Tailwind v4, and
  nothing else. Tailwind is wired through `@tailwindcss/vite`, not PostCSS, and
  `index.css` is a single `@import "tailwindcss"` with no `@theme` block. Colours
  are stock Tailwind classes written inline.
- **Types are hand-written, not generated.** `api/types.ts` mirrors
  `backend/app/models/verdict.py` by hand. Codegen was rejected because the
  surface is three endpoints and a generator is a build step to maintain.
- **The shared secret is never persisted.** It lives in a module variable and
  dies with the tab. `localStorage` would put a credential somewhere any script
  on the origin can read.
- **Queries go in a POST body, never a query string.** Prospect descriptions
  carry health details, and query strings land in access logs and browser
  history.
- **Comments explain reasoning, not mechanics.** Every file opens with a note on
  why it is built the way it is. Keep that up when editing.
