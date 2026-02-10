"use client";

import { FormEvent, KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

const REFUSAL_EN = "I can't answer from KIB's approved documents for this question.";
const REFUSAL_AR = "لا أستطيع الإجابة من مستندات KIB المعتمدة لهذا السؤال.";

type Citation = {
  doc_title: string;
  doc_id: string;
  document_version: string;
  page_number?: number | null;
  start_offset?: number | null;
  end_offset?: number | null;
  quote: string;
  source_uri: string;
};

type ChatResponse = {
  language: "en" | "ar";
  answer: string;
  confidence: "high" | "medium" | "low";
  citations: Citation[];
  missing_info?: string | null;
  safe_next_steps: string[];
};

type Message = {
  id: string;
  role: "user" | "assistant";
  text: string;
  response?: ChatResponse;
  timestamp: number;
};

type Conversation = {
  id: string;
  title: string;
  messages: Message[];
  createdAt: number;
  updatedAt: number;
};

type AuthSession = {
  token: string;
  email: string;
  name: string;
  roles: string[];
};

const AUTH_KEY = "kib-auth";

const STORAGE_KEY = "kib-conversations";

function loadConversations(): Conversation[] {
  if (typeof window === "undefined") return [];
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
  } catch {
    return [];
  }
}

function saveConversations(convos: Conversation[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(convos));
}

function detectLanguage(text: string): "en" | "ar" {
  const arabicPattern = /[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]/;
  return arabicPattern.test(text) ? "ar" : "en";
}

function formatTime(ts: number): string {
  return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function StreamingText({ text, speed = 20, onDone }: { text: string; speed?: number; onDone?: () => void }) {
  const [displayed, setDisplayed] = useState("");
  const [done, setDone] = useState(false);

  useEffect(() => {
    setDisplayed("");
    setDone(false);
    const words = text.split(" ");
    let i = 0;
    const interval = setInterval(() => {
      i++;
      setDisplayed(words.slice(0, i).join(" "));
      if (i >= words.length) {
        clearInterval(interval);
        setDone(true);
        onDone?.();
      }
    }, speed);
    return () => clearInterval(interval);
  }, [text]);

  return <>{displayed}{!done && <span className="cursor">|</span>}</>;
}

const SUGGESTIONS = [
  "What are the terms for KIB online banking?",
  "What is KIB's capital adequacy ratio?",
  "ما هي تعليمات بنك الكويت المركزي بشأن كفاية رأس المال؟",
  "What are CBK's anti-money laundering requirements?",
];

export default function Page() {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [forcedLanguage, setForcedLanguage] = useState<"en" | "ar" | "auto">("auto");
  const [selectedMsgId, setSelectedMsgId] = useState<string | null>(null);
  const [sourcesPanelOpen, setSourcesPanelOpen] = useState(true);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConvoId, setActiveConvoId] = useState<string | null>(null);
  const [streamingMsgId, setStreamingMsgId] = useState<string | null>(null);

  // Auth state
  const [auth, setAuth] = useState<AuthSession | null>(null);
  const [loginEmail, setLoginEmail] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [loginError, setLoginError] = useState<string | null>(null);
  const [loginLoading, setLoginLoading] = useState(false);

  // Load auth + conversations from localStorage on mount
  useEffect(() => {
    try {
      const stored = localStorage.getItem(AUTH_KEY);
      if (stored) setAuth(JSON.parse(stored));
    } catch {}
    setConversations(loadConversations());
  }, []);

  // Save current messages to the active conversation whenever messages change
  const activeConvoIdRef = useRef(activeConvoId);
  activeConvoIdRef.current = activeConvoId;

  useEffect(() => {
    const id = activeConvoIdRef.current;
    if (!id || messages.length === 0) return;
    setConversations((prev) => {
      const exists = prev.some((c) => c.id === id);
      if (!exists) return prev;
      const updated = prev.map((c) =>
        c.id === id
          ? { ...c, messages, updatedAt: Date.now() }
          : c
      );
      saveConversations(updated);
      return updated;
    });
  }, [messages]);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const activeCitations = useMemo(() => {
    if (selectedMsgId) {
      const msg = messages.find((m) => m.id === selectedMsgId);
      return msg?.response?.citations || [];
    }
    const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
    return lastAssistant?.response?.citations || [];
  }, [messages, selectedMsgId]);

  const apiBase = process.env.NEXT_PUBLIC_KIB_API_BASE_URL || "http://localhost:8000";

  async function handleLogin(e: FormEvent) {
    e.preventDefault();
    setLoginError(null);
    setLoginLoading(true);
    try {
      const res = await fetch(`${apiBase}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: loginEmail, password: loginPassword }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail || "Invalid credentials");
      }
      const data = await res.json();
      const session: AuthSession = {
        token: data.token,
        email: data.email,
        name: data.name,
        roles: data.roles,
      };
      localStorage.setItem(AUTH_KEY, JSON.stringify(session));
      setAuth(session);
    } catch (err) {
      setLoginError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoginLoading(false);
    }
  }

  function handleLogout() {
    localStorage.removeItem(AUTH_KEY);
    setAuth(null);
    setMessages([]);
    setSelectedMsgId(null);
    setActiveConvoId(null);
    setStreamingMsgId(null);
    setError(null);
    setLoginEmail("");
    setLoginPassword("");
  }

  const activeRole = auth?.roles?.[0] || "front_desk";

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 160) + "px";
    }
  }, [input]);

  async function sendMessage(text: string) {
    const trimmed = text.trim();
    if (!trimmed || loading) return;

    setError(null);
    const inferred = detectLanguage(trimmed);
    const language = forcedLanguage === "auto" ? inferred : forcedLanguage;

    const userMsg: Message = {
      id: crypto.randomUUID(),
      role: "user",
      text: trimmed,
      timestamp: Date.now(),
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);

    try {
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
        "Authorization": `Bearer ${auth?.token}`,
      };

      const history = messages.map((m) => ({ role: m.role, text: m.text }));
      const response = await fetch(`${apiBase}/chat`, {
        method: "POST",
        headers,
        body: JSON.stringify({ question: trimmed, language, top_k: 5, history }),
      });

      if (!response.ok) throw new Error(`API error: ${response.status}`);

      const data = (await response.json()) as ChatResponse;
      const assistantMsg: Message = {
        id: crypto.randomUUID(),
        role: "assistant",
        text: data.answer,
        response: data,
        timestamp: Date.now(),
      };

      setStreamingMsgId(assistantMsg.id);
      setMessages((prev) => [...prev, assistantMsg]);
      setSelectedMsgId(assistantMsg.id);

      // Create conversation on first response in this chat
      if (!activeConvoIdRef.current) {
        const newId = assistantMsg.id;
        activeConvoIdRef.current = newId;
        setActiveConvoId(newId);
        setConversations((c) => {
          if (c.some((x) => x.id === newId)) return c;
          const newConvo: Conversation = {
            id: newId,
            title: trimmed.length > 50 ? trimmed.slice(0, 50) + "..." : trimmed,
            messages: [userMsg, assistantMsg],
            createdAt: Date.now(),
            updatedAt: Date.now(),
          };
          const next = [newConvo, ...c];
          saveConversations(next);
          return next;
        });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unexpected error");
    } finally {
      setLoading(false);
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    sendMessage(input);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage(input);
    }
  }

  function newConversation() {
    setMessages([]);
    setSelectedMsgId(null);
    setActiveConvoId(null);
    activeConvoIdRef.current = null;
    setStreamingMsgId(null);
    setError(null);
  }

  // Show login screen if not authenticated
  if (!auth) {
    return (
      <div className="login-screen">
        <div className="login-card">
          <img src="/kib-logo.png" alt="KIB" className="login-logo" />
          <h1>Knowledge Copilot</h1>
          <p className="login-sub">Sign in to access KIB&apos;s knowledge base</p>
          <form onSubmit={handleLogin} className="login-form">
            <input
              type="email"
              placeholder="Email"
              value={loginEmail}
              onChange={(e) => setLoginEmail(e.target.value)}
              required
              autoFocus
            />
            <input
              type="password"
              placeholder="Password"
              value={loginPassword}
              onChange={(e) => setLoginPassword(e.target.value)}
              required
            />
            {loginError && <div className="login-error">{loginError}</div>}
            <button type="submit" disabled={loginLoading} className="login-btn">
              {loginLoading ? "Signing in..." : "Sign in"}
            </button>
          </form>
          <div className="login-hint">
            <p><strong>Demo accounts:</strong></p>
            <p>Front Desk: frontdesk@kib.com / frontdesk123</p>
            <p>Compliance: compliance@kib.com / compliance123</p>
          </div>
        </div>
      </div>
    );
  }

  function loadConversation(convo: Conversation) {
    setMessages(convo.messages);
    setActiveConvoId(convo.id);
    activeConvoIdRef.current = convo.id;
    setSelectedMsgId(null);
    setStreamingMsgId(null);
    setError(null);
  }

  function deleteConversation(id: string, e: React.MouseEvent) {
    e.stopPropagation();
    setConversations((prev) => {
      const next = prev.filter((c) => c.id !== id);
      saveConversations(next);
      return next;
    });
    if (activeConvoId === id) newConversation();
  }

  const isRefusal = (text: string) =>
    text.trim() === REFUSAL_EN || text.trim() === REFUSAL_AR;

  return (
    <div className="app-shell">
      {/* Sidebar */}
      <nav className="sidebar">
        <div className="sidebar-brand">
          <div className="brand-icon">K</div>
          <div>
            <div className="brand-name">KIB Copilot</div>
            <div className="brand-sub">Knowledge Assistant</div>
          </div>
        </div>

        <div className="sidebar-section">
          <label className="sidebar-label">Signed in as</label>
          <div className="user-info">
            <div className="user-name">{auth.name}</div>
            <div className="user-role-badge">{activeRole === "front_desk" ? "Front Desk" : "Compliance"}</div>
          </div>
        </div>

        {conversations.length > 0 && (
          <div className="sidebar-section">
            <label className="sidebar-label">History</label>
            <div className="convo-list">
              {conversations.map((convo) => (
                <div
                  key={convo.id}
                  className={`convo-item ${activeConvoId === convo.id ? "active" : ""}`}
                  onClick={() => loadConversation(convo)}
                >
                  <span className="convo-title">{convo.title}</span>
                  <button
                    className="convo-delete"
                    onClick={(e) => deleteConversation(convo.id, e)}
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="sidebar-spacer" />

        <button className="sidebar-btn" onClick={newConversation}>
          <span>+</span> New conversation
        </button>

        <button className="sidebar-btn logout-btn" onClick={handleLogout}>
          Sign out
        </button>
      </nav>

      {/* Main chat area */}
      <main className="main-area">
        <header className="topbar">
          <div>
            <h1 className="topbar-title">Knowledge Copilot</h1>
            <p className="topbar-sub">
              Grounded answers from approved KIB &amp; CBK documents
            </p>
          </div>
          <div className="topbar-actions">
            <button
              className={`toggle-sources ${sourcesPanelOpen ? "active" : ""}`}
              onClick={() => setSourcesPanelOpen(!sourcesPanelOpen)}
            >
              Sources {activeCitations.length > 0 && `(${activeCitations.length})`}
            </button>
          </div>
        </header>

        <div className="chat-body">
          <div className="chat-scroll">
            {messages.length === 0 ? (
              <div className="welcome">
                <img src="/kib-logo.png" alt="KIB" className="welcome-logo" />
                <h2>Ask me anything about KIB</h2>
                <p>
                  I answer from approved policies, product T&amp;Cs, compliance
                  documents, and CBK regulations. All answers include source
                  citations.
                </p>
                <div className="suggestions">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      className="suggestion-chip"
                      onClick={() => sendMessage(s)}
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              messages.map((msg) => {
                const isUser = msg.role === "user";
                const resp = msg.response;
                const confidence = resp?.confidence;
                const refused = resp ? isRefusal(resp.answer) : false;
                const isAr = resp?.language === "ar";

                return (
                  <div
                    key={msg.id}
                    className={`chat-row ${isUser ? "user-row" : "assistant-row"}`}
                    onClick={() => !isUser && setSelectedMsgId(msg.id)}
                  >
                    {!isUser && (
                      <div className="avatar assistant-avatar">K</div>
                    )}
                    <div className={`chat-bubble ${isUser ? "user-bubble" : "assistant-bubble"} ${
                      !isUser && selectedMsgId === msg.id ? "selected" : ""
                    } ${isAr ? "rtl" : ""}`}>
                      {!isUser && confidence && (
                        <div className="bubble-meta">
                          <span className={`badge ${confidence}`}>{confidence}</span>
                          {refused && <span className="badge refusal">Refusal</span>}
                          <span className="time">{formatTime(msg.timestamp)}</span>
                        </div>
                      )}
                      <div className="bubble-text">
                        {!isUser && streamingMsgId === msg.id
                          ? <StreamingText text={msg.text} speed={25} onDone={() => setStreamingMsgId(null)} />
                          : msg.text
                        }
                      </div>
                      {!isUser && resp?.missing_info && confidence !== "high" && (
                        <div className="missing-block">
                          <p>{resp.missing_info}</p>
                          <ul>
                            {resp.safe_next_steps.map((s) => (
                              <li key={s}>{s}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {!isUser && resp?.citations && resp.citations.length > 0 && (
                        <div className="inline-sources">
                          {resp.citations.length} source{resp.citations.length > 1 ? "s" : ""} cited
                        </div>
                      )}
                    </div>
                    {isUser && (
                      <div className="avatar user-avatar">
                        {auth.name.charAt(0).toUpperCase()}
                      </div>
                    )}
                  </div>
                );
              })
            )}

            {loading && (
              <div className="chat-row assistant-row">
                <div className="avatar assistant-avatar">K</div>
                <div className="chat-bubble assistant-bubble">
                  <div className="typing">
                    <span></span><span></span><span></span>
                  </div>
                </div>
              </div>
            )}

            {error && (
              <div className="chat-row assistant-row">
                <div className="avatar assistant-avatar">K</div>
                <div className="chat-bubble error-bubble">
                  <strong>Error:</strong> {error}
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          <form className="composer" onSubmit={handleSubmit}>
            <div className="composer-inner">
              <textarea
                ref={textareaRef}
                placeholder="Ask about KIB policies, products, or CBK regulations..."
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                rows={1}
                disabled={loading}
              />
              <button
                type="submit"
                disabled={loading || !input.trim()}
                className="send-btn"
                aria-label="Send"
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="12" y1="19" x2="12" y2="5" />
                  <polyline points="5 12 12 5 19 12" />
                </svg>
              </button>
            </div>
            <p className="composer-hint">
              Press <kbd>Enter</kbd> to send, <kbd>Shift+Enter</kbd> for new line
              {activeRole === "front_desk" ? " · Front Desk mode (concise answers)" : " · Compliance mode (detailed answers)"}
            </p>
          </form>
        </div>
      </main>

      {/* Sources panel */}
      {sourcesPanelOpen && (
        <aside className="sources-panel">
          <div className="sources-header">
            <h2>Sources</h2>
            <button className="close-sources" onClick={() => setSourcesPanelOpen(false)}>×</button>
          </div>
          {activeCitations.length > 0 ? (
            <div className="sources-list">
              {activeCitations.map((cit, idx) => (
                <div key={`${cit.doc_id}-${idx}`} className="source-card">
                  <div className="source-num">{idx + 1}</div>
                  <div className="source-body">
                    <p className="source-title">{cit.doc_title}</p>
                    <p className="source-meta">
                      v{cit.document_version}
                      {cit.page_number ? ` · Page ${cit.page_number}` : ""}
                    </p>
                    <blockquote className="source-quote">&ldquo;{cit.quote}&rdquo;</blockquote>
                    <a
                      className="source-link"
                      href={cit.source_uri}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Open document ↗
                    </a>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="sources-empty">
              <p>Citations will appear here when you ask a question.</p>
            </div>
          )}
        </aside>
      )}
    </div>
  );
}
