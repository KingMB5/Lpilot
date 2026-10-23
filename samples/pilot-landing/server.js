/* LiaraPilot — صفحهٔ محصول (zero-dependency node server).
 * این صفحه خودِ محصول است که با خودش روی لیارا مستقر شده است.
 */
const http = require("http");

const HTML = `<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>LiaraPilot — استقرار و مراقبت مداوم پروژه روی لیارا</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh;
    background:
      radial-gradient(1000px 520px at 85% -10%, rgba(16,185,129,.10), transparent 60%),
      radial-gradient(800px 420px at 8% 0%, rgba(167,139,250,.06), transparent 55%),
      #07090c;
    color: #e8edf2; font-family: "Vazirmatn", "IRANSans", "Segoe UI", Tahoma, sans-serif;
    padding: 40px 20px; display: grid; place-items: center;
  }
  main { max-width: 760px; width: 100%; }
  .pill {
    display: inline-flex; align-items: center; gap: 8px;
    background: rgba(16,185,129,.10); color: #34d399;
    border: 1px solid rgba(16,185,129,.4);
    padding: 6px 16px; border-radius: 999px; font-size: .82rem; font-weight: 600;
  }
  h1 { font-size: clamp(1.7rem, 4.5vw, 2.6rem); line-height: 1.6; margin: 20px 0 10px; }
  .grad { background: linear-gradient(120deg,#34d399,#6ee7b7 55%,#a78bfa);
    -webkit-background-clip: text; background-clip: text; color: transparent; }
  .lead { color: #94a3b3; line-height: 2.1; margin: 0 0 26px; font-size: 1.02rem; }
  .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .card {
    background: rgba(255,255,255,.025); border: 1px solid rgba(255,255,255,.08);
    border-radius: 16px; padding: 18px;
  }
  .card h3 { margin: 0 0 8px; font-size: 1rem; }
  .card p { color: #94a3b3; line-height: 2; margin: 0; font-size: .88rem; }
  .dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%;
    background: #10b981; box-shadow: 0 0 0 3px rgba(16,185,129,.15);
    margin-inline-end: 8px; animation: b 2.2s ease-in-out infinite; }
  @keyframes b { 0%,100% { opacity: 1; } 50% { opacity: .4; } }
  .flow { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 26px 0 0; color: #cbd5e1; font-size: .85rem; }
  .step { background: rgba(255,255,255,.04); border: 1px solid rgba(255,255,255,.1);
    border-radius: 999px; padding: 6px 14px; }
  .arr { color: #475569; }
  footer { margin-top: 30px; color: #64748b; font-size: .8rem; line-height: 2; }
  @media (max-width: 620px) { .grid { grid-template-columns: 1fr; } }
</style>
</head>
<body>
  <main>
    <span class="pill"><span class="dot"></span> این صفحه، خودِ محصول است — با خودش روی لیارا مستقر شده</span>
    <h1>پروژه‌ات را تحویل بده؛<br/><span class="grad">بالا آوردن و مراقبتش</span> با ما</h1>
    <p class="lead">
      LiaraPilot پروژه‌ات را بررسی می‌کند و می‌گوید روی لیارا به چه چیزی نیاز داری و چقدر هزینه دارد —
      فقط با تأیید تو استقرار می‌دهد، و بعد از آن مثل یک کارشناس ارزیاب می‌ماند:
      هر دوره سرعت، سلامت و لاگ‌های سایتت را چک می‌کند و راه‌حل می‌دهد.
    </p>
    <div class="grid">
      <div class="card">
        <h3>🔍 تحلیل واقعی پروژه</h3>
        <p>پلتفرم، پورت، متغیرهای محیطی و ریسک‌ها — از فایل‌های واقعی پروژه، پیش از هر اقدام.</p>
      </div>
      <div class="card">
        <h3>🛡️ هیچ کاری بدون اجازهٔ تو</h3>
        <p>پلن و هزینه را می‌بینی، تأیید می‌کنی، بعد اجرا می‌شود. هر اقدام حساس دروازهٔ تأیید دارد.</p>
      </div>
      <div class="card">
        <h3>✨ ایجنت ستاره</h3>
        <p>پایش روزانه/هفتگی/ماهانهٔ سایت زنده: پروب HTTP، لاگ‌های لیارا و گزارش فارسی با راه‌حل.</p>
      </div>
      <div class="card">
        <h3>🧰 خودترمیمی</h3>
        <p>اگر استقرار سالم بالا نیامد، از لاگ‌ها علت را می‌یابد، اصلاح می‌کند و دوباره صحت‌سنجی می‌کند.</p>
      </div>
    </div>
    <div class="flow">
      <span class="step">تحویل پروژه (ZIP یا گیت‌هاب)</span><span class="arr">←</span>
      <span class="step">تحلیل و هزینه</span><span class="arr">←</span>
      <span class="step">تأیید تو</span><span class="arr">←</span>
      <span class="step">استقرار روی لیارا</span><span class="arr">←</span>
      <span class="step">مراقبت دوره‌ای</span>
    </div>
    <footer>LiaraPilot · استقرار روی زیرساخت لیارا — با تأیید تو، و مراقبت مداوم بعد از آن</footer>
  </main>
</body>
</html>
`;

http.createServer((_req, res) => {
  res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
  res.end(HTML);
}).listen(process.env.PORT || 3000, () => console.log("landing listening on " + (process.env.PORT || 3000)));
