/* LiaraPilot landing — zero-dependency node server (fast, stable build). */
const http = require("http");

const HTML = `<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>LiaraPilot — خلبان استقرار و نگهبان پروژه</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; display: grid; place-items: center;
    background: radial-gradient(900px 500px at 80% -10%, rgba(16,185,129,.12), transparent 60%), #0a0e13;
    color: #eef2f8; font-family: "Vazirmatn", "IRANSans", "Segoe UI", Tahoma, sans-serif;
    padding: 24px; text-align: center;
  }
  .card {
    max-width: 640px; width: 100%;
    background: rgba(18,22,29,.9); border: 1px solid rgba(148,163,184,.14);
    border-radius: 20px; padding: 40px 32px; box-shadow: 0 30px 80px rgba(0,0,0,.5);
  }
  .badge {
    display: inline-flex; align-items: center; gap: 8px;
    background: rgba(16,185,129,.12); color: #34d399;
    border: 1px solid rgba(16,185,129,.42);
    padding: 6px 14px; border-radius: 999px; font-size: .8rem;
  }
  h1 { font-size: 1.9rem; margin: 18px 0 10px; }
  .grad { background: linear-gradient(135deg,#34d399,#14b8a6); -webkit-background-clip: text; background-clip: text; color: transparent; }
  p { color: #a3aebb; line-height: 2; margin: 8px 0 22px; }
  ul { list-style: none; padding: 0; margin: 0 0 26px; color: #a3aebb; font-size: .95rem; line-height: 2.1; }
  li::before { content: "✓"; color: #34d399; margin-inline-end: 8px; font-weight: 700; }
  a.btn {
    display: inline-block; background: linear-gradient(135deg,#10b981,#14b8a6);
    color: #04110b; font-weight: 800; text-decoration: none;
    padding: 12px 28px; border-radius: 12px; font-size: 1rem;
  }
  .small { font-size: .78rem; color: #8b97a9; margin-top: 18px; }
</style>
</head>
<body>
  <main class="card">
    <span class="badge">● چالش لیارا — خلبان استقرار با هوش مصنوعی</span>
    <h1>پروژه‌ات را بسپار به <span class="grad">خلبان</span></h1>
    <p>این سایت با <b>LiaraPilot</b> روی لیارا مستقر شده است: ایجنتی که پروژه را بررسی می‌کند، پلن و هزینه را می‌گوید، فقط با تأیید تو استقرار می‌دهد — و بعد، مثل یک نگهبان، دوره‌ای سلامت و سرعت و لاگ‌های سایتت را چک می‌کند.</p>
    <ul>
      <li>بررسی هوشمند پروژه (پلتفرم، ریسک‌ها، متغیرها)</li>
      <li>پلن + هزینهٔ شفاف و تأیید انسانی در هر گام</li>
      <li>خودترمیمی از لاگ‌ها و صحت‌سنجی URL زنده</li>
      <li>نگهبان پروژه: پایش روزانه/هفتگی/ماهانه + گزارش فارسی</li>
    </ul>
    <a class="btn" href="https://github.com/KingMB5/Lpilot" target="_blank" rel="noopener">کد در گیت‌هاب</a>
    <p class="small">LiaraPilot • ساخته‌شده برای چالش لیارا</p>
  </main>
</body>
</html>
`;

http.createServer((_req, res) => {
  res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
  res.end(HTML);
}).listen(process.env.PORT || 3000, () => console.log("landing listening on " + (process.env.PORT || 3000)));
