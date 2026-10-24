/**
 * Optional Express helper server.
 *
 * This does NOT mint provider tokens. It exposes a configured hosted embed URL.
 * Replace with documented server-side token creation for production, if supported
 * by your provider account. Do not add long-lived provider secrets to frontend code.
 */
const express = require("express");
const cors = require("cors");

const app = express();
const port = Number(process.env.PORT || 8787);
const frontendOrigin = process.env.FRONTEND_ORIGIN;
const agentUrl = process.env.RESQAI_AGENT_URL || "";

app.disable("x-powered-by");
app.use(express.json({ limit: "32kb" }));

if (frontendOrigin) {
  app.use(cors({ origin: frontendOrigin, methods: ["GET"] }));
}

app.get("/api/health", (_req, res) => {
  res.json({ ok: true, service: "resqai-agent-config" });
});

app.get("/api/resqai/config", (_req, res) => {
  if (!agentUrl) {
    return res.status(503).json({
      configured: false,
      message: "Voice agent URL is not configured.",
    });
  }

  // An embed URL with a token is still a bearer credential. Avoid caching it.
  res.set("Cache-Control", "no-store");
  return res.json({ configured: true, agentUrl });
});

app.listen(port, () => {
  console.log(`ResQAI config helper listening on port ${port}`);
});
