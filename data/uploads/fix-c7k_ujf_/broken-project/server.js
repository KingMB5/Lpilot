const express = require("express");
const app = express();
if (!process.env.MISSING_TOKEN) {
  console.error("MISSING_TOKEN is required");
}
app.get("/", (_req, res) => res.send("never happens in mock"));
app.listen(process.env.PORT || 3000);
