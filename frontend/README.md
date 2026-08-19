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

- **No UI library, no icon library, no chart library.** React and Tailwind v4.
  The only other runtime dependency is `@fontsource-variable/inter`, which is a
  typeface rather than a component kit — self-hosted so the page makes no
  request to a third party on load. The handful of icons are inline SVG; three
  glyphs do not justify a dependency.
- **Every colour, size, and radius is a token in `index.css`.** Components never
  name a colour. The palette, the type scale, and the rate-class ladder live in
  one `@theme inline` block, and the dark theme is the same block with different
  values — which is why there is not a single `dark:` class in any component.
  This block is meant to be lifted into the sibling projects so they read as one
  body of work.
- **The rate-class ladder is one variable per tier.** An element sets `--tier`
  and the `.tier-chip` / `.tier-dot` / `.tier-rail` rules mix their background,
  text, and border out of it with `color-mix`. Adding a rate class means adding
  one hue, not three shades that later drift apart.
- **Colour scheme has three states, not two.** Light, dark, and "follow the
  system" — the last is the default and the one most readers never change. A
  small inline script in `index.html` applies a stored choice before first
  paint; doing it in React paints light and then flips, and a white flash on a
  dark machine is worse than not offering the choice.
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
