import ZAI from "z-ai-web-dev-sdk";
const OPENAI_API_KEY = process.env.OPENAI_API_KEY;
export async function describePerfume(text: string): Promise<string> {
  const zai = await ZAI.create({ apiKey: OPENAI_API_KEY });
  return text;
}
