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
  audit_log_id?: string | null;
  conversation_id?: string | null;
};

type Message = {
  id: string;
  role: "user" | "assistant";
  text: string;
  response?: ChatResponse;
  timestamp: number;
  feedback?: "up" | "down" | null;
  feedbackCorrection?: string | null;
};

type Conversation = {
  id: string;
  title: string;
  messages: Message[];
  createdAt: number;
  updatedAt: number;
};

type ServerChatMessage = {
  id: string;
  role: "user" | "assistant";
  text: string;
  response?: ChatResponse | null;
  created_at: string;
};

type ServerConversation = {
  id: string;
  title: string;
  messages: ServerChatMessage[];
  created_at: string;
  updated_at: string;
};

type AuthSession = {
  token: string;
  email: string;
  name: string;
  roles: string[];
};

const AUTH_KEY = "kib-auth";

const STORAGE_KEY = "kib-conversations";
const MAX_CONVERSATIONS = 12;
const MAX_MESSAGES_PER_CONVO = 24;

function compactConversations(convos: Conversation[]): Conversation[] {
  return convos.slice(0, MAX_CONVERSATIONS).map((convo) => ({
    ...convo,
    messages: convo.messages.slice(-MAX_MESSAGES_PER_CONVO),
  }));
}

function loadConversations(): Conversation[] {
  if (typeof window === "undefined") return [];
  try {
    return compactConversations(JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]"));
  } catch {
    return [];
  }
}

function saveConversations(convos: Conversation[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(compactConversations(convos)));
}

function scheduleSaveConversations(convos: Conversation[]) {
  if (typeof window === "undefined") return;
  const save = () => saveConversations(convos);
  const idleCallback = (window as typeof window & {
    requestIdleCallback?: (callback: () => void, options?: { timeout: number }) => number;
  }).requestIdleCallback;

  if (idleCallback) {
    idleCallback(save, { timeout: 1000 });
  } else {
    window.setTimeout(save, 0);
  }
}

function normalizeConversation(convo: ServerConversation): Conversation {
  return {
    id: convo.id,
    title: convo.title,
    createdAt: Date.parse(convo.created_at),
    updatedAt: Date.parse(convo.updated_at),
    messages: convo.messages.map((message) => ({
      id: message.id,
      role: message.role,
      text: message.text,
      response: message.response || undefined,
      timestamp: Date.parse(message.created_at),
    })),
  };
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

type AuditLog = {
  id: string;
  role_names: string[];
  query: string;
  answer: string;
  retrieval_meta?: { confidence?: string; missing_info?: string } | null;
  latency_ms?: number | null;
  created_at: string;
};

type FeedbackItem = {
  id: string;
  audit_log_id: string;
  rating: string;
  correction?: string | null;
  created_at: string;
};

type UploadResult = {
  status: string;
  title: string;
  pages: number;
  chunks: number;
  message: string;
};

type DocItem = {
  id: string;
  title: string;
  doc_type: string;
  language: string;
  status: string;
  page_count?: number | null;
  source_uri?: string | null;
  created_at: string;
};

function AdminDashboard({ apiBase, token }: { apiBase: string; token: string }) {
  const [tab, setTab] = useState<"audit" | "feedback" | "documents">("documents");
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([]);
  const [feedbackList, setFeedbackList] = useState<FeedbackItem[]>([]);
  const [documents, setDocuments] = useState<DocItem[]>([]);
  const [loadingData, setLoadingData] = useState(false);

  // Upload state
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadTitle, setUploadTitle] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const headers = { Authorization: `Bearer ${token}` };

  useEffect(() => {
    loadData();
  }, [tab]);

  async function loadData() {
    setLoadingData(true);
    try {
      if (tab === "audit") {
        const res = await fetch(`${apiBase}/audit?limit=50`, { headers });
        if (res.ok) setAuditLogs(await res.json());
      } else if (tab === "feedback") {
        const res = await fetch(`${apiBase}/feedback`, { headers });
        if (res.ok) setFeedbackList(await res.json());
      } else if (tab === "documents") {
        const res = await fetch(`${apiBase}/admin/documents`, { headers });
        if (res.ok) setDocuments(await res.json());
      }
    } catch {}
    setLoadingData(false);
  }

  async function handleUpload(e: React.FormEvent) {
    e.preventDefault();
    if (!uploadFile || !uploadTitle.trim()) return;

    setUploading(true);
    setUploadError(null);
    setUploadResult(null);

    const formData = new FormData();
    formData.append("file", uploadFile);
    formData.append("title", uploadTitle.trim());
    formData.append("doc_type", "pdf");
    formData.append("allowed_roles", "admin,employee");

    try {
      const res = await fetch(`${apiBase}/admin/upload`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        throw new Error(data?.detail || `Upload failed (${res.status})`);
      }
      const data = await res.json();
      setUploadResult(data);
      setUploadFile(null);
      setUploadTitle("");
      loadData();
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="admin-panel">
      <div className="admin-tabs">
        <button className={`admin-tab ${tab === "documents" ? "active" : ""}`} onClick={() => setTab("documents")}>
          Documents ({documents.length})
        </button>
        <button className={`admin-tab ${tab === "audit" ? "active" : ""}`} onClick={() => setTab("audit")}>
          Audit Logs ({auditLogs.length})
        </button>
        <button className={`admin-tab ${tab === "feedback" ? "active" : ""}`} onClick={() => setTab("feedback")}>
          Feedback ({feedbackList.length})
        </button>
        <button className="admin-tab refresh" onClick={loadData} disabled={loadingData}>
          {loadingData ? "Loading..." : "Refresh"}
        </button>
      </div>

      {tab === "documents" && (
        <div className="admin-table-wrap">
          <div className="upload-section">
            <h3>Upload New Document</h3>
            <form className="upload-form" onSubmit={handleUpload}>
              <div className="upload-row">
                <input
                  type="text"
                  placeholder="Document title"
                  value={uploadTitle}
                  onChange={(e) => setUploadTitle(e.target.value)}
                  required
                  className="upload-input"
                />
              </div>
              <div className="upload-row">
                <label className="upload-file-label">
                  <input
                    type="file"
                    accept=".pdf"
                    onChange={(e) => setUploadFile(e.target.files?.[0] || null)}
                    className="upload-file-input"
                  />
                  <span className="upload-file-btn">
                    {uploadFile ? uploadFile.name : "Choose PDF file..."}
                  </span>
                </label>
                <button type="submit" disabled={uploading || !uploadFile || !uploadTitle.trim()} className="upload-submit">
                  {uploading ? "Uploading & Processing..." : "Upload & Ingest"}
                </button>
              </div>
            </form>
            {uploading && (
              <div className="upload-progress">
                Processing PDF: extracting text, chunking, generating embeddings... This may take a minute.
              </div>
            )}
            {uploadResult && (
              <div className="upload-success">
                {uploadResult.message}
              </div>
            )}
            {uploadError && (
              <div className="upload-error">{uploadError}</div>
            )}
          </div>

          <h3 style={{ margin: "24px 0 12px", fontSize: "14px", fontWeight: 600 }}>All Documents ({documents.length})</h3>
          <table className="admin-table">
            <thead>
              <tr>
                <th>Title</th>
                <th>Type</th>
                <th>Language</th>
                <th>Pages</th>
                <th>Status</th>
                <th>Added</th>
              </tr>
            </thead>
            <tbody>
              {documents.map((doc) => (
                <tr key={doc.id}>
                  <td className="truncate-cell">{doc.title}</td>
                  <td>{doc.doc_type}</td>
                  <td>{doc.language}</td>
                  <td>{doc.page_count || "—"}</td>
                  <td><span className={`badge ${doc.status === "approved" ? "high" : "medium"}`}>{doc.status}</span></td>
                  <td className="nowrap">{new Date(doc.created_at).toLocaleDateString()}</td>
                </tr>
              ))}
              {documents.length === 0 && !loadingData && (
                <tr><td colSpan={6} className="empty-row">No documents yet</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {tab === "audit" && (
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Role</th>
                <th>Question</th>
                <th>Confidence</th>
                <th>Latency</th>
                <th>Answer (preview)</th>
              </tr>
            </thead>
            <tbody>
              {auditLogs.map((log) => (
                <tr key={log.id}>
                  <td className="nowrap">{new Date(log.created_at).toLocaleString()}</td>
                  <td>{log.role_names.join(", ")}</td>
                  <td className="truncate-cell">{log.query}</td>
                  <td>
                    <span className={`badge ${log.retrieval_meta?.confidence || "low"}`}>
                      {log.retrieval_meta?.confidence || "—"}
                    </span>
                  </td>
                  <td className="nowrap">{log.latency_ms ? `${log.latency_ms}ms` : "—"}</td>
                  <td className="truncate-cell">{log.answer.slice(0, 100)}{log.answer.length > 100 ? "..." : ""}</td>
                </tr>
              ))}
              {auditLogs.length === 0 && !loadingData && (
                <tr><td colSpan={6} className="empty-row">No audit logs yet</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {tab === "feedback" && (
        <div className="admin-table-wrap">
          <table className="admin-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Rating</th>
                <th>Correction</th>
                <th>Audit Log ID</th>
              </tr>
            </thead>
            <tbody>
              {feedbackList.map((fb) => (
                <tr key={fb.id}>
                  <td className="nowrap">{new Date(fb.created_at).toLocaleString()}</td>
                  <td>
                    <span className={fb.rating === "up" ? "feedback-up" : "feedback-down"}>
                      {fb.rating === "up" ? "👍" : "👎"}
                    </span>
                  </td>
                  <td>{fb.correction || "—"}</td>
                  <td className="mono">{fb.audit_log_id.slice(0, 8)}...</td>
                </tr>
              ))}
              {feedbackList.length === 0 && !loadingData && (
                <tr><td colSpan={4} className="empty-row">No feedback yet</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
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
  const [feedbackPromptMsgId, setFeedbackPromptMsgId] = useState<string | null>(null);
  const [feedbackCorrection, setFeedbackCorrection] = useState("");
  const [feedbackSubmittingId, setFeedbackSubmittingId] = useState<string | null>(null);

  // Auth state
  const [auth, setAuth] = useState<AuthSession | null>(null);
  const [loginEmail, setLoginEmail] = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [loginError, setLoginError] = useState<string | null>(null);
  const [loginLoading, setLoginLoading] = useState(false);
  const apiBase = process.env.NEXT_PUBLIC_KIB_API_BASE_URL || "http://localhost:8000";

  // Load auth + conversations from localStorage on mount
  useEffect(() => {
    try {
      const stored = localStorage.getItem(AUTH_KEY);
      if (stored) setAuth(JSON.parse(stored));
    } catch {}
    setConversations(loadConversations());
  }, []);

  const loadServerConversations = useCallback(async (token: string) => {
    const res = await fetch(`${apiBase}/conversations`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!res.ok) throw new Error("Failed to load conversations");
    const data = (await res.json()) as ServerConversation[];
    const next = compactConversations(data.map(normalizeConversation));
    setConversations(next);
    scheduleSaveConversations(next);
  }, [apiBase]);

  useEffect(() => {
    if (!auth?.token) return;
    loadServerConversations(auth.token).catch(() => {});
  }, [auth?.token, loadServerConversations]);

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
      scheduleSaveConversations(updated);
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

  const isAdmin = auth?.roles?.includes("admin") || false;
  const [adminView, setAdminView] = useState<"dashboard" | "chat">("chat");
  const showAdmin = isAdmin && adminView === "dashboard";

  // Default to dashboard when admin logs in
  useEffect(() => {
    if (isAdmin) setAdminView("dashboard");
    else setAdminView("chat");
  }, [isAdmin]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "auto" });
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
        body: JSON.stringify({
          question: trimmed,
          language,
          top_k: 5,
          history,
          conversation_id: activeConvoIdRef.current,
        }),
      });

      if (!response.ok) throw new Error(`API error: ${response.status}`);

      const data = (await response.json()) as ChatResponse;
      const wasNewConversation = !activeConvoIdRef.current;
      const conversationId = data.conversation_id || activeConvoIdRef.current || crypto.randomUUID();
      const assistantMsg: Message = {
        id: crypto.randomUUID(),
        role: "assistant",
        text: data.answer,
        response: data,
        timestamp: Date.now(),
      };

      if (wasNewConversation) {
        activeConvoIdRef.current = conversationId;
        setActiveConvoId(conversationId);
      }
      setStreamingMsgId(assistantMsg.id);
      setMessages((prev) => [...prev, assistantMsg]);
      setSelectedMsgId(assistantMsg.id);

      // Create conversation on first response in this chat
      if (wasNewConversation) {
        setConversations((c) => {
          if (c.some((x) => x.id === conversationId)) return c;
          const newConvo: Conversation = {
            id: conversationId,
            title: trimmed.length > 50 ? trimmed.slice(0, 50) + "..." : trimmed,
            messages: [userMsg, assistantMsg],
            createdAt: Date.now(),
            updatedAt: Date.now(),
          };
          const next = [newConvo, ...c];
          scheduleSaveConversations(next);
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
    if (isAdmin) setAdminView("chat");
  }

  async function submitFeedback(msgId: string, rating: "up" | "down", correction?: string) {
    const msg = messages.find((m) => m.id === msgId);
    if (!msg?.response?.audit_log_id || msg.feedback || feedbackSubmittingId) return;

    const trimmedCorrection = correction?.trim() || null;
    setFeedbackSubmittingId(msgId);
    setMessages((prev) =>
      prev.map((m) => (
        m.id === msgId
          ? { ...m, feedback: rating, feedbackCorrection: trimmedCorrection }
          : m
      ))
    );
    setFeedbackPromptMsgId(null);
    setFeedbackCorrection("");
    try {
      const res = await fetch(`${apiBase}/feedback`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${auth?.token}`,
        },
        body: JSON.stringify({
          audit_log_id: msg.response.audit_log_id,
          rating,
          correction: trimmedCorrection,
        }),
      });
      if (!res.ok) throw new Error("Feedback failed");
    } catch {
      setMessages((prev) =>
        prev.map((m) => (
          m.id === msgId
            ? { ...m, feedback: null, feedbackCorrection: null }
            : m
        ))
      );
    } finally {
      setFeedbackSubmittingId(null);
    }
  }

  function openDownFeedback(msgId: string) {
    const msg = messages.find((m) => m.id === msgId);
    if (!msg?.response?.audit_log_id || msg.feedback) return;
    setFeedbackPromptMsgId(msgId);
    setFeedbackCorrection("");
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
            <p>Admin: admin@kib.com / admin123</p>
            <p>Employee: employee@kib.com / employee123</p>
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
    if (isAdmin) setAdminView("chat");
  }

  async function deleteConversation(id: string, e: React.MouseEvent) {
    e.stopPropagation();
    setConversations((prev) => {
      const next = prev.filter((c) => c.id !== id);
      scheduleSaveConversations(next);
      return next;
    });
    if (activeConvoId === id) newConversation();
    try {
      await fetch(`${apiBase}/conversations/${id}`, {
        method: "DELETE",
        headers: { Authorization: `Bearer ${auth?.token}` },
      });
    } catch {}
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
            <div className="user-role-badge">{isAdmin ? "Admin" : "Employee"}</div>
          </div>
        </div>

        {isAdmin && (
          <div className="sidebar-section">
            <div className="admin-nav">
              <button
                className={`admin-nav-btn ${adminView === "dashboard" ? "active" : ""}`}
                onClick={() => setAdminView("dashboard")}
              >
                Dashboard
              </button>
              <button
                className={`admin-nav-btn ${adminView === "chat" ? "active" : ""}`}
                onClick={() => setAdminView("chat")}
              >
                Chat
              </button>
            </div>
          </div>
        )}

        {conversations.length > 0 && (!isAdmin || adminView === "chat") && (
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

        {(!isAdmin || adminView === "chat") && (
          <button className="sidebar-btn" onClick={newConversation}>
            <span>+</span> New conversation
          </button>
        )}

        <button className="sidebar-btn logout-btn" onClick={handleLogout}>
          Sign out
        </button>
      </nav>

      {/* Main chat area */}
      <main className="main-area">
        <header className="topbar">
          <div>
            <h1 className="topbar-title">{showAdmin ? "Admin Dashboard" : "Knowledge Copilot"}</h1>
            <p className="topbar-sub">
              {showAdmin ? "Audit logs, feedback review, and system management" : "Grounded answers from approved KIB & CBK documents"}
            </p>
          </div>
          <div className="topbar-actions">
            {!showAdmin && (
              <button
                className={`toggle-sources ${sourcesPanelOpen ? "active" : ""}`}
                onClick={() => setSourcesPanelOpen(!sourcesPanelOpen)}
              >
                Sources {activeCitations.length > 0 && `(${activeCitations.length})`}
              </button>
            )}
          </div>
        </header>

        {showAdmin ? (
          <AdminDashboard apiBase={apiBase} token={auth.token} />
        ) : (
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
                const feedbackPending = feedbackSubmittingId === msg.id;

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
                      {!isUser && resp?.audit_log_id && streamingMsgId !== msg.id && (
                        <div className="feedback-row">
                          <button
                            className={`feedback-btn ${msg.feedback === "up" ? "active-up" : ""}`}
                            onClick={(e) => { e.stopPropagation(); submitFeedback(msg.id, "up"); }}
                            disabled={!!msg.feedback || !!feedbackSubmittingId}
                            title="Helpful"
                          >
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3H14z"/><path d="M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>
                          </button>
                          <button
                            className={`feedback-btn ${msg.feedback === "down" ? "active-down" : ""}`}
                            onClick={(e) => { e.stopPropagation(); openDownFeedback(msg.id); }}
                            disabled={!!msg.feedback || !!feedbackSubmittingId}
                            title="Not helpful"
                          >
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3H10z"/><path d="M17 2h3a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-3"/></svg>
                          </button>
                          {msg.feedback && (
                            <span className="feedback-thanks">
                              {feedbackPending ? "Saving..." : "Thanks for feedback"}
                            </span>
                          )}
                        </div>
                      )}
                      {!isUser && resp?.audit_log_id && feedbackPromptMsgId === msg.id && !msg.feedback && (
                        <form
                          className="feedback-comment"
                          onClick={(e) => e.stopPropagation()}
                          onSubmit={(e) => {
                            e.preventDefault();
                            submitFeedback(msg.id, "down", feedbackCorrection);
                          }}
                        >
                          <label>What was wrong?</label>
                          <textarea
                            value={feedbackCorrection}
                            onChange={(e) => setFeedbackCorrection(e.target.value)}
                            placeholder="Example: unrelated sources, wrong number, missing citation..."
                            rows={3}
                            autoFocus
                          />
                          <div className="feedback-comment-actions">
                            <button
                              type="button"
                              onClick={() => {
                                setFeedbackPromptMsgId(null);
                                setFeedbackCorrection("");
                              }}
                              disabled={!!feedbackSubmittingId}
                            >
                              Cancel
                            </button>
                            <button type="submit" disabled={!!feedbackSubmittingId}>
                              {feedbackPending ? "Saving..." : "Save feedback"}
                            </button>
                          </div>
                        </form>
                      )}
                      {!isUser && msg.feedbackCorrection && (
                        <div className="feedback-saved-comment">
                          Saved note: {msg.feedbackCorrection}
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
              {isAdmin ? " · Admin mode (detailed answers)" : " · Employee mode"}
            </p>
          </form>
        </div>
        )}
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
