import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import "./App.css";
import { api, getApiKey, setApiKey, clearApiKey, UnauthorizedError } from "./api";
import type { SendMessageResp, SqlEvidence } from "./api";

type Bubble = {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  blocked?: boolean;
  evidence?: SqlEvidence[];
  steps?: string[];
};

const STARTER_QUESTIONS = [
  "What's the population of California in 2020?",
  "How many Hispanic people live in Texas?",
  "Compare 2019 vs 2020 for the top 5 states",
  "Vacant housing units in New York?",
];

function uid(): string {
  return Math.random().toString(36).slice(2, 10);
}

export default function App() {
  const [authed, setAuthed] = useState<boolean>(() => getApiKey() !== "");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [liveSteps, setLiveSteps] = useState<string[]>([]);
  const [pendingElapsed, setPendingElapsed] = useState(0);
  const liveStepsRef = useRef<string[]>([]);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => { liveStepsRef.current = liveSteps; }, [liveSteps]);

  // Drop back to the key gate when the backend rejects our key.
  function handleUnauthorized() {
    clearApiKey();
    setAuthed(false);
    setSessionId(null);
    setBubbles([]);
    setError("Your access key was rejected. Please enter a valid key.");
  }

  useEffect(() => {
    if (!authed) return;
    void (async () => {
      try {
        const s = await api.createSession();
        setSessionId(s.session_id);
        setBubbles([{ id: uid(), role: "assistant", text: s.greeting }]);
      } catch (e) {
        if (e instanceof UnauthorizedError) { handleUnauthorized(); return; }
        setError(`Could not start session: ${(e as Error).message}`);
      }
    })();
  }, [authed]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [bubbles, liveSteps]);

  async function send(messageOverride?: string) {
    const msg = (messageOverride ?? input).trim();
    if (!msg || pending || !sessionId) return;
    setBubbles((b) => [...b, { id: uid(), role: "user", text: msg }]);
    setInput("");
    setPending(true);
    setError(null);
    setLiveSteps([]);
    setPendingElapsed(0);

    const STAGES = [
      { at: 0,  label: "Searching catalog" },
      { at: 5,  label: "Picking metrics" },
      { at: 11, label: "Compiling SQL" },
      { at: 17, label: "Querying Snowflake" },
      { at: 25, label: "Summarizing results" },
    ];
    const t0 = Date.now();
    const fired = new Set<number>();
    const tick = window.setInterval(() => {
      const elapsed = Math.floor((Date.now() - t0) / 1000);
      setPendingElapsed(elapsed);
      STAGES.forEach((s, i) => {
        if (elapsed >= s.at && !fired.has(i)) {
          fired.add(i);
          setLiveSteps((prev) => [...prev, s.label]);
        }
      });
    }, 500);

    try {
      const resp: SendMessageResp = await api.sendMessage(sessionId, msg);
      window.clearInterval(tick);
      setBubbles((b) => [
        ...b,
        {
          id: uid(),
          role: "assistant",
          text: resp.reply,
          blocked: resp.blocked,
          evidence: resp.evidence,
          steps: [...liveStepsRef.current],
        },
      ]);
    } catch (e) {
      window.clearInterval(tick);
      if (e instanceof UnauthorizedError) { handleUnauthorized(); return; }
      // Append the error as an inline assistant bubble so the failed turn
      // stays visible in the conversation thread even if the user sends
      // another message. Banner-style alerts wipe on the next request.
      const detail = (e as Error).message || "Request failed";
      setBubbles((b) => [
        ...b,
        {
          id: uid(),
          role: "assistant",
          text: `⚠️ **Something went wrong.** ${detail}\n\n*You can retry the same question or rephrase it.*`,
          blocked: true,
          steps: [...liveStepsRef.current],
        },
      ]);
    } finally {
      setPending(false);
      setLiveSteps([]);
    }
  }

  // The most recent user message is shown as a contextual page title.
  const latestUserMsg = [...bubbles].reverse().find((b) => b.role === "user")?.text;

  if (!authed) {
    return <KeyGate error={error} onSubmit={(k) => { setApiKey(k); setError(null); setAuthed(true); }} />;
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">◆</span>
          <span className="brand-name">Census Analyst</span>
        </div>
        <span className="muted">{sessionId ? `session ${sessionId.slice(0, 6)}` : "connecting…"}</span>
      </header>

      {latestUserMsg && (
        <div className="context-header">
          <div className="context-question">{latestUserMsg}</div>
        </div>
      )}

      <div className="chat" ref={scrollRef}>
        {bubbles.map((b) => <BubbleView key={b.id} bubble={b} />)}
        {pending && <ThinkingBlock steps={liveSteps} elapsed={pendingElapsed} />}
        {error && <div className="alert error">⚠️ {error}</div>}
      </div>

      <footer className="composer-wrap">
        {!pending && bubbles.length <= 1 && (
          <div className="suggestions">
            {STARTER_QUESTIONS.map((q) => (
              <button key={q} className="chip" onClick={() => void send(q)} disabled={!sessionId}>
                {q}
              </button>
            ))}
          </div>
        )}
        <div className="composer">
          <textarea
            value={input}
            placeholder="Ask about the US population…"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            rows={1}
          />
          <button
            className="send"
            onClick={() => void send()}
            disabled={pending || !input.trim() || !sessionId}
            aria-label="Send"
          >
            ↑
          </button>
        </div>
      </footer>
    </div>
  );
}

function KeyGate({ error, onSubmit }: { error: string | null; onSubmit: (key: string) => void }) {
  const [value, setValue] = useState("");
  const submit = () => { if (value.trim()) onSubmit(value); };
  return (
    <div className="gate">
      <div className="gate-card">
        <div className="brand">
          <span className="brand-mark">◆</span>
          <span className="brand-name">Census Analyst</span>
        </div>
        <p className="gate-prompt">Enter your access key to continue.</p>
        <input
          className="gate-input"
          type="password"
          value={value}
          placeholder="Access key"
          autoFocus
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
        />
        <button className="gate-btn" onClick={submit} disabled={!value.trim()}>
          Unlock
        </button>
        {error && <div className="alert error">⚠️ {error}</div>}
      </div>
    </div>
  );
}

function ThinkingBlock({ steps, elapsed }: { steps: string[]; elapsed: number }) {
  if (steps.length === 0) {
    return (
      <div className="message assistant">
        <div className="thinking">
          <span className="dot-pulse" />
          <span>Thinking</span>
          <span className="elapsed">{elapsed}s</span>
        </div>
      </div>
    );
  }
  return (
    <div className="message assistant">
      <div className="thinking">
        <span className="dot-pulse" />
        <span>Thinking</span>
        <span className="elapsed">{elapsed}s</span>
      </div>
      <ul className="steplist">
        {steps.slice(0, -1).map((s, i) => (
          <li key={i} className="step done"><span className="check">✓</span> {s}</li>
        ))}
        <li className="step active">
          <span className="spinner" />
          {steps[steps.length - 1]}
        </li>
      </ul>
    </div>
  );
}

function BubbleView({ bubble }: { bubble: Bubble }) {
  const [stepsOpen, setStepsOpen] = useState(false);
  const [sqlOpen, setSqlOpen] = useState(false);

  if (bubble.role === "user") {
    return (
      <div className="message user">
        <div className="user-msg">{bubble.text}</div>
      </div>
    );
  }

  return (
    <div className={`message assistant ${bubble.blocked ? "blocked" : ""}`}>
      {bubble.steps && bubble.steps.length > 0 && (
        <div className="thinking-collapsed">
          <button className="thinking-toggle" onClick={() => setStepsOpen((v) => !v)}>
            <span className="check">✓</span>
            Thought for {bubble.steps.length} {bubble.steps.length === 1 ? "step" : "steps"}
            <span className="caret">{stepsOpen ? "▴" : "▾"}</span>
          </button>
          {stepsOpen && (
            <ul className="steplist collapsed">
              {bubble.steps.map((s, i) => (
                <li key={i} className="step done"><span className="check">✓</span> {s}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      <div className="text">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{bubble.text}</ReactMarkdown>
      </div>
      {bubble.evidence && bubble.evidence.length > 0 && (
        <div className="evidence">
          <button className="thinking-toggle" onClick={() => setSqlOpen((v) => !v)}>
            <span className="db-icon">⌘</span>
            View SQL ({bubble.evidence.length})
            <span className="caret">{sqlOpen ? "▴" : "▾"}</span>
          </button>
          {sqlOpen && (
            <div className="evbody">
              {bubble.evidence.map((ev, i) => (
                <div key={i} className="evblock">
                  <pre className="sql">{ev.sql}</pre>
                  <div className="evmeta">
                    {ev.row_count} {ev.row_count === 1 ? "row" : "rows"}
                    {ev.truncated ? " (truncated)" : ""}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
