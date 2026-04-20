import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import "./App.css";

type BackendStatus = "booting" | "ready" | "failed";

function App() {
  const [backend, setBackend] = useState<string>("");
  const [status, setStatus] = useState<BackendStatus>("booting");

  useEffect(() => {
    let cancelled = false;

    async function wait() {
      const url = await invoke<string>("backend_url");
      if (cancelled) return;
      setBackend(url);

      // Poll /api/healthz until the Flask sidecar answers.
      const deadline = Date.now() + 30_000;
      while (Date.now() < deadline && !cancelled) {
        try {
          const r = await fetch(`${url}/api/healthz`);
          if (r.ok) {
            if (!cancelled) setStatus("ready");
            return;
          }
        } catch {
          // backend not up yet
        }
        await new Promise((res) => setTimeout(res, 500));
      }
      if (!cancelled) setStatus("failed");
    }

    wait();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="shell">
      <header className="shell__titlebar" />
      <div className="shell__body">
        <h1>DAPManager</h1>
        <p className="shell__status" data-status={status}>
          {status === "booting" && "Starting Python backend…"}
          {status === "ready" && `Backend ready at ${backend}`}
          {status === "failed" && "Backend failed to start — check terminal."}
        </p>
      </div>
    </main>
  );
}

export default App;
