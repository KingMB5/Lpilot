const express = require("express");
const path = require("path");
const dbPath = path.join(__dirname, "db", "perfume.db"); // mounted persistent disk on Liara
const app = express();
const PORT = process.env.PORT || 3000;
app.get("/", (_req, res) => res.json({ ok: true, dbPath }));
app.listen(PORT, () => console.log(`listening on ${PORT}`));
