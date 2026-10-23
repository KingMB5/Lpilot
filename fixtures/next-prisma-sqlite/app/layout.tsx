import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "عطرستان لارا",
  description: "فروشگاه عطر — پروژه‌ی نمونه‌ی تست استقرار",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="fa" dir="rtl">
      <body>{children}</body>
    </html>
  );
}
