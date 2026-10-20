# fallback-app (plan B)

Minimal Node + SQLite app wired for a persistent disk per the official Liara docs:
liara.json {"disks":[{"name":"database","mountTo":"db"}]}.
Used when the primary Next.js+Prisma+SQLite spike fails on real Liara.
