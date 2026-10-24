# ResQAI Voice Agent Integration Starter

This package is a compatibility-first integration layer for an existing ResQAI application. It is intentionally framework-light: the UI component is React + TypeScript, and the optional backend is Express. Claude Code should inspect the existing repository and adapt these files to the app's actual framework, routing, styling, and API conventions.

## What this starter does

- Adds an accessible SOS voice-agent panel.
- Embeds the existing Tough Tongue AI scenario using its supported embed URL.
- Keeps the agent URL in environment configuration instead of hard-coding it into source files.
- Includes an optional Express configuration/health endpoint.
- Documents the security and production requirements.

## Important provider limitation

The current agent was created as a Tough Tongue AI hosted scenario (`ResQAI Emergency Voice Assistant`, scenario ID `6ad83140767d01fd14f81fa4`). This starter uses the provider's hosted embed, not a custom raw-audio WebRTC implementation. Do not invent undocumented provider APIs or claim this is a self-hosted streaming stack. For production, use a documented provider API to mint short-lived session tokens on the backend if available to your account; never expose a permanent API key in browser code.

An embed URL containing a scenario access token is a bearer credential. Treat it as secret, keep its lifetime short, and replace expired URLs. Do not commit `.env` files.

## Files

- `frontend/ResQAIWidget.tsx` — React component
- `frontend/resqai-widget.css` — baseline styles
- `backend/server.example.js` — optional Express config/health API
- `.env.example` — environment variable template
- `CLAUDE_INTEGRATION_PROMPT.md` — prompt to run in Claude Code at the root of your existing project

## Quick integration

1. Copy the files into your repository or unzip this folder next to the project.
2. Configure the hosted embed URL in the frontend environment as `VITE_RESQAI_AGENT_URL` (Vite) or adapt the environment access for your framework.
3. Import `ResQAIWidget` into the existing SOS screen.
4. Import the CSS or translate it to your existing styling system.
5. Test microphone permission, browser autoplay behavior, mobile sizing, and session start/end.
6. Ask Claude Code to read `CLAUDE_INTEGRATION_PROMPT.md` and integrate it with the existing project.

## Safety behavior

This is an emergency-assistance prototype. Unless you separately implement and verify integrations, it does not notify contacts, dispatch an ambulance, call police/fire, or verify GPS location. The interface must clearly state this. Never tell users that an external action succeeded unless the relevant service returns a confirmed success result.

## Environment

Copy `.env.example` to the appropriate environment file. Do not check in secrets or token-bearing URLs. A Vite `VITE_*` variable is visible to browser users at runtime/build time, so it is not a place for permanent provider API keys. It is only suitable for a temporary embed URL during development.

## Optional backend

The Express sample exposes `/api/health` and `/api/resqai/config`. The config endpoint returns only the configured embed URL, so it is convenience/configuration—not a token-minting security boundary. Do not put a long-lived provider API key in this endpoint. In production, replace this with documented server-side token minting or your chosen provider's official session API.

## Compatibility notes

This package does not assume your project uses Vite, Express, Tailwind, a specific router, or a specific backend. Claude Code should adapt paths, component conventions, auth, CORS, CSP, and styling rather than replacing your existing app wholesale.
