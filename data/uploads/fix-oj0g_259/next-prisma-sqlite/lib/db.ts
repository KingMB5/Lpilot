import { PrismaClient } from "@prisma/client";
const DATABASE_URL = process.env.DATABASE_URL;
export const prisma = new PrismaClient({ datasources: { db: { url: DATABASE_URL } } });
