# Codonova Desktop

A native desktop window for Codonova. It starts the Docker services for you and opens the dashboard in its own app window, so there is no terminal and no browser tab to manage.

## What it does

1. Checks that Docker is running (and starts Docker Desktop on Windows if it is not).
2. Runs `docker compose up -d` for the Codonova project.
3. Waits until the backend health check passes.
4. Opens the dashboard in a native window.

Desktop extras:

- **System tray** icon. Closing the window keeps Codonova running in the tray.
- **Native notifications** when a pipeline finishes.
- **Native "Save as" dialog** for the ZIP export.
- **Services menu**: start, stop and restart services, backend logs, API docs, Neo4j Browser.
- **Quit prompt**: choose whether to stop the Docker services or keep them running.
- Links to other sites (Neo4j, LangSmith, the live preview in a new tab) open in your normal browser.

## Run from source

Requires Node.js 18+ and Docker Desktop. The `.env` file in the repository root must exist (copy `.env.example`).

```bash
cd desktop
npm install
npm start
```

## Build the Windows installer

```bash
cd desktop
npm run dist
```

The installer is written to `desktop/dist/Codonova Setup 1.0.0.exe`.

The installed app wraps the Codonova project that lives on your machine, so on first start it asks you to pick the folder that contains `docker-compose.yml` and remembers it. You can change it later from `File -> Choose Codonova folder...`. Set `CODONOVA_HOME` to skip the prompt.

`signAndEditExecutable` is turned off in `package.json` because the code-signing tools need Windows symlink privileges (Developer Mode). With Developer Mode on you can remove that option to get the Codonova icon embedded in `Codonova.exe` itself. The window, tray and installer icons work either way.

## Security

The dashboard runs with `contextIsolation` and `sandbox` on and no Node.js access. The only thing exposed to the page is `window.codonova.notify(title, body)`, and the main process accepts it only from the dashboard origin.
