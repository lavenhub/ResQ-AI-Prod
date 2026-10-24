# Claude Code integration task

You are working inside an existing ResQAI project. Integrate the voice agent using the supplied starter files, but first inspect the entire repository and understand its actual architecture.

## Hard requirements

1. Do not replace or rewrite the existing project wholesale. Preserve the existing SOS flow, visual design, routing, auth, state management, and build system.
2. Find the current SOS screen/component and integrate `frontend/ResQAIWidget.tsx` into it. Adapt imports and environment variable syntax to the existing frontend (Vite, Next.js, CRA, etc.). If the project is not React, port the behavior to the actual framework rather than forcing React into it.
3. Use the current Tough Tongue AI hosted scenario embed URL configured locally. Do not commit access tokens or `.env` files. Never expose permanent API keys in browser code.
4. If a backend already exists, add a small route using its existing conventions instead of introducing a second server. If there is no backend, the optional Express helper is for local configuration only; do not claim it mints tokens.
5. The existing agent is hosted by Tough Tongue AI. Use its documented embed method unless official account documentation supports another method. Do not invent endpoints, SDK methods, WebRTC signaling APIs, or token formats.
6. Preserve English, Hindi, Marathi, and code-mixed behavior in the existing scenario prompt. Do not overwrite the live scenario unless the user explicitly asks.
7. Make the UI accessible and mobile responsive. Request microphone access only through the provider's supported embed interaction; handle blocked permissions and expired links clearly.
8. Do not claim GPS, SMS, contact notifications, ambulance/police/fire dispatch, QR card generation, or IoT features work unless existing integrations actually implement and verify them.
9. Keep the emergency disclaimer visible: this prototype does not contact emergency services or notify contacts unless a real integration confirms success.
10. Do not log transcripts, access tokens, medical details, phone numbers, or precise location unnecessarily.

## Work plan

- Inspect package manifests, app entry points, SOS components, backend routes, env conventions, and current errors.
- Summarize the architecture and integration plan before making broad changes.
- Copy/adapt the supplied widget and styles into the correct locations.
- Add env examples and update `.gitignore` if needed to exclude local env files.
- Integrate the widget into the existing SOS screen at the appropriate location.
- If useful, implement a small config route in the existing backend; set no-store cache headers on token-bearing configuration.
- Run the existing lint, typecheck, tests, and build commands. Fix errors caused by your changes.
- Add/update documentation with exact setup and test steps.
- At the end, report files changed, commands run, tests/build results, required env vars, and any remaining blockers.

## Acceptance checks

- App builds successfully.
- SOS UI remains intact.
- Agent panel opens and closes, including Escape key if supported.
- Missing configuration shows a clear setup message instead of a broken blank iframe.
- With a valid embed URL, the hosted agent loads and can be tested with microphone permission.
- Mobile layout fits the viewport.
- No permanent secrets are committed.
- No unimplemented emergency actions are represented as successful.
