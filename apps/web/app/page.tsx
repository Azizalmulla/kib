"use client";

import { FormEvent, useMemo, useState } from "react";

const REFUSAL_TEXT = "I can't answer from KIB's approved documents for this question.";

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
  question: string;
  response: ChatResponse;
};

function detectLanguage(text: string): "en" | "ar" {
  const arabicPattern = /[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]/;
  return arabicPattern.test(text) ? "ar" : "en";
}

export default function Page() {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [forcedLanguage, setForcedLanguage] = useState<"en" | "ar" | "auto">("auto");
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);

  const activeMessage = useMemo(() => {
    if (selectedIndex === null) {
      return messages[messages.length - 1] || null;
    }
    return messages[selectedIndex] || null;
  }, [messages, selectedIndex]);

  const apiBase = process.env.NEXT_PUBLIC_KIB_API_BASE_URL || "http://localhost:8000";
  const mockUser = process.env.NEXT_PUBLIC_KIB_MOCK_USER;
  const mockRoles = process.env.NEXT_PUBLIC_KIB_MOCK_ROLES;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    const trimmed = question.trim();
    if (!trimmed) {
      return;
    }

    const inferred = detectLanguage(trimmed);
    const language = forcedLanguage === "auto" ? inferred : forcedLanguage;

    setLoading(true);

    try {
      const headers: Record<string, string> = {
        "Content-Type": "application/json"
      };
      if (mockUser) {
        headers["X-Mock-User"] = mockUser;
      }
      if (mockRoles) {
        headers["X-Mock-Roles"] = mockRoles;
      }

      const response = await fetch(`${apiBase}/chat`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          question: trimmed,
          language,
          top_k: 5
        })
      });

      if (!response.ok) {
        throw new Error(`API error: ${response.status}`);
      }

      const data = (await response.json()) as ChatResponse;
      const newMessage: Message = {
        id: crypto.randomUUID(),
        question: trimmed,
        response: data
      };

      setMessages((prev) => [...prev, newMessage]);
      setSelectedIndex(null);
      setQuestion("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unexpected error");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="page">
      <header className="hero">
        <div>
          <p className="eyebrow">Kuwait International Bank</p>
          <h1>Knowledge Copilot</h1>
          <p className="lead">
            Grounded answers from approved KIB policies, procedures, and compliance documents.
          </p>
        </div>
        <div className="controls">
          <label className="select">
            <span>Language</span>
            <select
              value={forcedLanguage}
              onChange={(event) => setForcedLanguage(event.target.value as "en" | "ar" | "auto")}
            >
              <option value="auto">Auto</option>
              <option value="en">English</option>
              <option value="ar">Arabic</option>
            </select>
          </label>
        </div>
      </header>

      <section className="workspace">
        <div className="chat-panel">
          <div className="messages">
            {messages.length === 0 ? (
              <div className="empty">
                <p>Ask about KIB policies, procedures, product terms, or compliance rules.</p>
                <p>Answers are grounded in approved documents only.</p>
              </div>
            ) : (
              messages.map((message, index) => {
                const lang = message.response.language;
                const isRefusal = message.response.answer.trim() === REFUSAL_TEXT;
                const confidence = message.response.confidence;
                return (
                  <div
                    key={message.id}
                    className={`message ${selectedIndex === index ? "active" : ""}`}
                    onClick={() => setSelectedIndex(index)}
                  >
                    <div className="bubble question">
                      <p>{message.question}</p>
                    </div>
                    <div className={`bubble answer ${lang === "ar" ? "rtl" : ""}`}>
                      <div className="answer-header">
                        <span className={`confidence ${confidence}`}>{confidence}</span>
                        {isRefusal && <span className="refusal-tag">Refusal</span>}
                      </div>
                      <p>{message.response.answer}</p>
                      {message.response.missing_info && confidence !== "high" && (
                        <div className="missing">
                          <p>{message.response.missing_info}</p>
                          <ul>
                            {message.response.safe_next_steps.map((step) => (
                              <li key={step}>{step}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })
            )}
          </div>

          <form className="composer" onSubmit={handleSubmit}>
            <textarea
              placeholder="Ask a question about KIB policies or procedures"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              rows={3}
            />
            <div className="composer-actions">
              {error && <span className="error">{error}</span>}
              <button type="submit" disabled={loading}>
                {loading ? "Asking..." : "Ask Copilot"}
              </button>
            </div>
          </form>
        </div>

        <aside className="sources-panel">
          <div className="sources-header">
            <h2>Sources</h2>
            <p>{activeMessage ? "Citations from approved documents." : "No sources yet."}</p>
          </div>
          {activeMessage ? (
            <div className="sources-list">
              {activeMessage.response.citations.map((citation, idx) => (
                <div key={`${citation.doc_id}-${idx}`} className="source-card">
                  <div>
                    <p className="source-title">{citation.doc_title}</p>
                    <p className="source-meta">
                      Version {citation.document_version}
                      {citation.page_number ? ` • Page ${citation.page_number}` : ""}
                    </p>
                  </div>
                  <blockquote className="source-quote">"{citation.quote}"</blockquote>
                  <a className="source-link" href={citation.source_uri} target="_blank" rel="noreferrer">
                    Open document
                  </a>
                </div>
              ))}
            </div>
          ) : (
            <div className="empty sources-empty">
              <p>Sources will appear after the first answer.</p>
            </div>
          )}
        </aside>
      </section>
    </main>
  );
}
