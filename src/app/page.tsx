export const metadata = {
  title: 'LiaraPilot — خلبان استقرار',
  description: 'AI agent that deploys finished projects to Liara (hackathon Phase 1 preview shell)',
}

/**
 * Sandbox preview shell only.
 * The real app is the FastAPI service (uvicorn app.main:app) on port 8000;
 * the gateway forwards /?XTransformPort=8000 to it. This page simply frames it
 * so the single exposed port (3000) shows the product UI.
 */
export default function Home() {
  return (
    <iframe
      src="/?XTransformPort=8000"
      title="LiaraPilot"
      style={{
        position: 'fixed',
        inset: 0,
        width: '100%',
        height: '100%',
        border: '0',
        background: '#0d0f13',
      }}
      allow="clipboard-write"
    />
  )
}
