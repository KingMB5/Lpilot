const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD || "benis1404";
export function checkPassword(input: string): boolean {
  return input === ADMIN_PASSWORD;
}
