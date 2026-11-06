import React, { useEffect, useRef, useState } from "react";
import { FEATURES } from "../features.js";
import { ADMIN_HOME, isAdministrationOnly } from "../navModel.js";
import { IconBook, IconChart, IconCheckCircle, IconPlus, IconQueue, IconUser, IconCatalog } from "./Icons.jsx";

export default function CommandPalette({ isOpen, onClose, navigate, views = [], user = {} }) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef(null);
  const previousFocusRef = useRef(null);

  useEffect(() => {
    if (isOpen) {
      previousFocusRef.current = document.activeElement;
      setQuery("");
      setActiveIndex(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    } else if (previousFocusRef.current) {
      previousFocusRef.current.focus?.();
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const isInternal = user.role !== "customer";

  const adminOnly = isAdministrationOnly(user);

  const allActions = adminOnly ? [
    { id: "nav-admin", label: "Administration", category: "Navigation", icon: <IconBook />, run: () => navigate(ADMIN_HOME) },
    { id: "nav-profile", label: "User profile & settings", category: "Navigation", icon: <IconUser />, run: () => navigate("/profile") },
  ] : [
    ...views.map((v) => ({
      id: `view-${v.key}`,
      label: `Queue: ${v.label}`,
      category: "Queues",
      icon: <IconQueue />,
      run: () => navigate(`/tickets?view=${v.key}`),
    })),
    {
      id: "action-new",
      label: user.is_agent ? "New ticket" : "Raise a request",
      category: "Actions",
      icon: <IconPlus />,
      run: () => navigate("/new"),
    },
    ...(isInternal
      ? [
          ...(FEATURES.catalog ? [{
            id: "nav-catalog",
            label: "Service catalog",
            category: "Navigation",
            icon: <IconCatalog />,
            run: () => navigate("/catalog"),
          }] : []),
          ...(FEATURES.approvals ? [{
            id: "nav-approvals",
            label: "Pending approvals",
            category: "Navigation",
            icon: <IconCheckCircle />,
            run: () => navigate("/approvals"),
          }] : []),
        ]
      : []),
    ...(user.is_agent
      ? [
          {
            id: "nav-dashboard",
            label: "Dashboard",
            category: "Navigation",
            icon: <IconChart />,
            run: () => navigate("/dashboard"),
          },
        ]
      : []),
    {
      id: "nav-kb",
      label: "Knowledge base",
      category: "Navigation",
      icon: <IconBook />,
      run: () => navigate("/kb"),
    },
    {
      id: "nav-profile",
      label: "User profile & settings",
      category: "Navigation",
      icon: <IconUser />,
      run: () => navigate("/profile"),
    },
  ];

  const q = query.trim().toLowerCase();
  let filtered = allActions.filter((a) => a.label.toLowerCase().includes(q) || a.category.toLowerCase().includes(q));

  // If query looks like a ticket number (e.g. "123", "INC-000123", "SR-000045")
  const ticketNumMatch = query.trim().match(/^(?:inc-|sr-)?(\d+)$/i);
  if (ticketNumMatch && !adminOnly) {
    const rawNum = ticketNumMatch[1];
    filtered = [
      {
        id: `ticket-jump-${rawNum}`,
        label: `Jump to Ticket #${query.trim().toUpperCase()}`,
        category: "Tickets",
        icon: <IconQueue />,
        run: () => navigate(`/tickets/${rawNum}`),
      },
      ...filtered,
    ];
  }

  const handleKeyDown = (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((prev) => (prev + 1) % Math.max(1, filtered.length));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((prev) => (prev - 1 + filtered.length) % Math.max(1, filtered.length));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (filtered[activeIndex]) {
        filtered[activeIndex].run();
        onClose();
      }
    }
  };

  return (
    <div
      className="cmd-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
    >
      <div className="cmd-palette" onKeyDown={handleKeyDown}>
        <div className="cmd-header">
          <input
            ref={inputRef}
            className="cmd-input"
            type="text"
            placeholder="Type a command, queue, or ticket number..."
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setActiveIndex(0);
            }}
            aria-autocomplete="list"
            aria-controls="cmd-list"
          />
          <span className="cmd-badge">ESC</span>
        </div>
        <div id="cmd-list" className="cmd-list" role="listbox">
          {filtered.length === 0 ? (
            <div style={{ padding: "16px", textAlign: "center", color: "var(--ink-3)", fontSize: 13 }}>
              No commands or tickets match "{query}"
            </div>
          ) : (
            filtered.map((item, idx) => (
              <button
                key={item.id}
                type="button"
                className={`cmd-item ${idx === activeIndex ? "active" : ""}`}
                onClick={() => {
                  item.run();
                  onClose();
                }}
                onMouseEnter={() => setActiveIndex(idx)}
                role="option"
                aria-selected={idx === activeIndex}
              >
                <div className="cmd-item-label">
                  <span style={{ display: "flex", opacity: 0.7 }}>{item.icon}</span>
                  <span>{item.label}</span>
                </div>
                <span className="cmd-badge">{item.category}</span>
              </button>
            ))
          )}
        </div>
        <div className="cmd-footer">
          <span><b>↑↓</b> Navigate</span>
          <span><b>↵</b> Select</span>
          <span><b>ESC</b> Close</span>
        </div>
      </div>
    </div>
  );
}
