import { useState, useEffect, useCallback } from "react";

// ─── Config ────────────────────────────────────────────────────────────────
// In production this points to your queue server.
// When running the React app on the same host it's just "".
const API = "";

// ─── Helpers ───────────────────────────────────────────────────────────────
function fmt_time(seconds) {
  if (!seconds) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function fmt_size(bytes) {
  if (!bytes) return "—";
  if (bytes > 1_000_000) return `${(bytes / 1_000_000).toFixed(1)} MB`;
  if (bytes > 1_000) return `${(bytes / 1_000).toFixed(0)} KB`;
  return `${bytes} B`;
}

function fmt_date(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString(undefined, {
    month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function status_color(status) {
  switch (status) {
    case "queued":  return "text-amber-400 bg-amber-400/10 border-amber-400/30";
    case "sending": return "text-blue-400 bg-blue-400/10 border-blue-400/30";
    case "sent":    return "text-green-400 bg-green-400/10 border-green-400/30";
    case "error":   return "text-red-400 bg-red-400/10 border-red-400/30";
    case "deleted": return "text-slate-500 bg-slate-500/10 border-slate-500/30";
    default:        return "text-slate-400 bg-slate-400/10 border-slate-400/30";
  }
}

function printer_state_color(state) {
  switch (state?.toLowerCase()) {
    case "printing":    return "text-amber-400";
    case "ready":
    case "idle":        return "text-green-400";
    case "offline":
    case "error":       return "text-red-400";
    case "unconfigured": return "text-slate-500";
    default:            return "text-slate-400";
  }
}

// ─── Sub-components ────────────────────────────────────────────────────────

function Dot({ color }) {
  return (
    <span className={`inline-block w-2 h-2 rounded-full mr-2 ${color} opacity-90`}
          style={{ boxShadow: "0 0 6px currentColor" }} />
  );
}

function Badge({ status }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded border text-xs font-mono uppercase tracking-wider ${status_color(status)}`}>
      {status}
    </span>
  );
}

function ActionButton({ onClick, disabled, loading, variant = "default", children }) {
  const base = "inline-flex items-center gap-2 px-4 py-2 rounded font-mono text-sm font-medium transition-all duration-150 disabled:opacity-40 disabled:cursor-not-allowed";
  const variants = {
    default: "bg-slate-700 hover:bg-slate-600 text-slate-100 border border-slate-600",
    primary: "bg-amber-500 hover:bg-amber-400 text-slate-900 border border-amber-400",
    danger:  "bg-red-500/20 hover:bg-red-500/30 text-red-400 border border-red-500/40",
    ghost:   "bg-transparent hover:bg-slate-700 text-slate-400 hover:text-slate-200 border border-slate-700",
  };
  return (
    <button onClick={onClick} disabled={disabled || loading}
            className={`${base} ${variants[variant]}`}>
      {loading && (
        <svg className="animate-spin w-3.5 h-3.5" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
        </svg>
      )}
      {children}
    </button>
  );
}

function Toast({ toasts }) {
  return (
    <div className="fixed bottom-6 right-6 flex flex-col gap-2 z-50 pointer-events-none">
      {toasts.map(t => (
        <div key={t.id}
             className={`px-4 py-3 rounded border font-mono text-sm shadow-xl transition-all
               ${t.type === "error"
                 ? "bg-red-950 border-red-500/50 text-red-300"
                 : "bg-green-950 border-green-500/50 text-green-300"}`}>
          {t.msg}
        </div>
      ))}
    </div>
  );
}

function PrinterPanel({ printer, loading }) {
  const state = printer?.state ?? "—";
  const stateColor = printer_state_color(state);
  const isPrinting = state?.toLowerCase() === "printing";

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-800/60 overflow-hidden">
      {/* LCD-style header */}
      <div className="px-5 py-3 border-b border-slate-700 flex items-center justify-between"
           style={{ background: "linear-gradient(90deg, #1a1d2e 0%, #12141f 100%)" }}>
        <div className="flex items-center gap-3">
          <svg className="w-4 h-4 text-amber-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
              d="M9 3H5a2 2 0 00-2 2v4m6-6h10a2 2 0 012 2v4M9 3v18m0 0h10a2 2 0 002-2V9M9 21H5a2 2 0 01-2-2V9m0 0h18"/>
          </svg>
          <span className="font-mono text-xs text-slate-400 tracking-widest uppercase">Printer Status</span>
        </div>
        <div className="flex items-center gap-2">
          {loading && <svg className="animate-spin w-3 h-3 text-slate-500" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"/>
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z"/>
          </svg>}
          <span className={`font-mono text-sm font-bold tracking-wide ${stateColor}`}
                style={{ textShadow: "0 0 12px currentColor" }}>
            {state.toUpperCase()}
          </span>
        </div>
      </div>

      <div className="px-5 py-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat label="File" value={printer?.file || "—"} mono truncate />
        <Stat label="Progress" value={printer?.progress != null ? `${printer.progress}%` : "—"} mono />
        <Stat label="State" value={state} mono color={stateColor} />
        <Stat label="Active" value={isPrinting ? "Yes" : "No"} mono color={isPrinting ? "text-amber-400" : "text-slate-500"} />
      </div>

      {/* Progress bar */}
      {isPrinting && printer?.progress != null && (
        <div className="px-5 pb-4">
          <div className="h-1.5 rounded-full bg-slate-700 overflow-hidden">
            <div className="h-full rounded-full bg-amber-400 transition-all duration-700"
                 style={{
                   width: `${printer.progress}%`,
                   boxShadow: "0 0 8px #F59E0B",
                 }} />
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, mono, color, truncate }) {
  return (
    <div>
      <div className="text-xs text-slate-500 uppercase tracking-wider mb-1">{label}</div>
      <div className={`text-sm ${mono ? "font-mono" : ""} ${color || "text-slate-200"} ${truncate ? "truncate" : ""}`}
           title={truncate ? value : undefined}>
        {value}
      </div>
    </div>
  );
}

function JobCard({ job, onDelete, isNext }) {
  const mappings = job.material_mappings || [];

  return (
    <div className={`rounded-lg border transition-all duration-200
      ${isNext
        ? "border-amber-500/50 bg-amber-500/5 shadow-[0_0_20px_rgba(245,158,11,0.08)]"
        : "border-slate-700 bg-slate-800/40 hover:border-slate-600"}`}>

      {/* Card header */}
      <div className="px-4 py-3 flex items-start justify-between gap-4 border-b border-slate-700/60">
        <div className="flex items-center gap-3 min-w-0">
          {isNext && (
            <span className="shrink-0 text-xs font-mono text-amber-400 border border-amber-400/40 px-1.5 py-0.5 rounded bg-amber-400/10">
              NEXT
            </span>
          )}
          <span className="font-mono text-sm text-slate-200 truncate" title={job.filename}>
            {job.filename}
          </span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <Badge status={job.status} />
          <button onClick={() => onDelete(job.id)}
                  className="text-slate-600 hover:text-red-400 transition-colors p-1 rounded hover:bg-red-400/10">
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12"/>
            </svg>
          </button>
        </div>
      </div>

      {/* Card body */}
      <div className="px-4 py-3 flex flex-wrap gap-x-6 gap-y-2">
        <Stat label="Size" value={fmt_size(job.file_size)} mono />
        <Stat label="Time" value={fmt_time(job.printing_time)} mono />
        <Stat label="Layers" value={job.total_layers || "—"} mono />
        <Stat label="Tools" value={job.tool_count ?? "—"} mono />
        <Stat label="Queued" value={fmt_date(job.created_at)} />
        {job.sent_at && <Stat label="Sent" value={fmt_date(job.sent_at)} />}
      </div>

      {/* Material mappings */}
      {mappings.length > 0 && (
        <div className="px-4 pb-3 flex flex-wrap gap-2">
          {mappings.map((m, i) => (
            <div key={i}
                 className="flex items-center gap-1.5 px-2 py-1 rounded bg-slate-700/60 border border-slate-600/50 text-xs font-mono">
              <span className="w-3 h-3 rounded-full border border-slate-500 shrink-0"
                    style={{ backgroundColor: m.slotMaterialColor || m.toolMaterialColor || "#888" }} />
              <span className="text-slate-400">T{m.toolId}</span>
              <span className="text-slate-600">→</span>
              <span className="text-slate-300">S{m.slotId}</span>
              <span className="text-slate-500 ml-0.5">{m.materialName}</span>
            </div>
          ))}
        </div>
      )}

      {/* Error message */}
      {job.error && (
        <div className="mx-4 mb-3 px-3 py-2 rounded bg-red-950/60 border border-red-500/30 text-xs font-mono text-red-400">
          {job.error}
        </div>
      )}
    </div>
  );
}

function EmptyQueue() {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <svg className="w-12 h-12 text-slate-700 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1}
          d="M9 3H5a2 2 0 00-2 2v4m6-6h10a2 2 0 012 2v4M9 3v18m0 0h10a2 2 0 002-2V9M9 21H5a2 2 0 01-2-2V9m0 0h18"/>
      </svg>
      <p className="font-mono text-slate-500 text-sm">Queue is empty</p>
      <p className="font-mono text-slate-600 text-xs mt-1">Upload a job from OrcaSlicer to get started</p>
    </div>
  );
}

// ─── Main App ──────────────────────────────────────────────────────────────

export default function App() {
  const [status, setStatus]       = useState(null);
  const [jobs, setJobs]           = useState([]);
  const [toasts, setToasts]       = useState([]);
  const [loadingStatus, setLoadingStatus] = useState(true);
  const [loadingAction, setLoadingAction] = useState(null); // "next"|"clear"|"purge"

  const toast = useCallback((msg, type = "success") => {
    const id = Date.now();
    setToasts(t => [...t, { id, msg, type }]);
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 3500);
  }, []);

  const fetchStatus = useCallback(async () => {
    try {
      const r = await fetch(`${API}/queue/status`);
      const d = await r.json();
      setStatus(d);
    } catch {
      setStatus(s => s ? { ...s, printer: { state: "offline" } } : null);
    } finally {
      setLoadingStatus(false);
    }
  }, []);

  const fetchJobs = useCallback(async () => {
    try {
      const r = await fetch(`${API}/queue`);
      const d = await r.json();
      setJobs(d.jobs || []);
    } catch {
      // keep previous
    }
  }, []);

  const refresh = useCallback(async () => {
    await Promise.all([fetchStatus(), fetchJobs()]);
  }, [fetchStatus, fetchJobs]);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  async function sendNext() {
    setLoadingAction("next");
    try {
      const r = await fetch(`${API}/queue/next`, { method: "POST" });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "Failed");
      toast(`Sent: ${d.job?.filename}`);
      await refresh();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setLoadingAction(null);
    }
  }

  async function clearQueue() {
    setLoadingAction("clear");
    try {
      const r = await fetch(`${API}/queue/clear`, { method: "POST" });
      const d = await r.json();
      toast(`Cleared ${d.cleared} job${d.cleared !== 1 ? "s" : ""}`);
      await refresh();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setLoadingAction(null);
    }
  }

  async function purgeQueue() {
    setLoadingAction("purge");
    try {
      const r = await fetch(`${API}/queue/cleanup`, { method: "POST" });
      const d = await r.json();
      toast(`Purged ${d.purged_rows} row${d.purged_rows !== 1 ? "s" : ""}, removed ${d.removed_files.length} file${d.removed_files.length !== 1 ? "s" : ""}`);
      await refresh();
    } catch (e) {
      toast(e.message, "error");
    } finally {
      setLoadingAction(null);
    }
  }

  async function deleteJob(id) {
    try {
      await fetch(`${API}/queue/${id}`, { method: "DELETE" });
      await refresh();
    } catch (e) {
      toast(e.message, "error");
    }
  }

  const queuedJobs  = jobs.filter(j => j.status === "queued");
  const activeJobs  = jobs.filter(j => j.status === "sending");
  const sentJobs    = jobs.filter(j => j.status === "sent");
  const errorJobs   = jobs.filter(j => j.status === "error");
  const nextJobId   = status?.next_job?.id;
  const hasQueued   = queuedJobs.length > 0 || activeJobs.length > 0;

  const allDisplayJobs = [
    ...activeJobs,
    ...errorJobs,
    ...queuedJobs,
    ...sentJobs,
  ];

  return (
    <div className="min-h-screen"
         style={{ background: "#0F1117", color: "#E8EAF0", fontFamily: "'Inter', sans-serif" }}>

      {/* Google Fonts */}
      <link rel="stylesheet"
            href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500;700&display=swap" />

      {/* Header */}
      <header className="border-b border-slate-800 px-6 py-4 flex items-center justify-between"
              style={{ background: "rgba(15,17,23,0.95)", backdropFilter: "blur(8px)",
                       position: "sticky", top: 0, zIndex: 40 }}>
        <div className="flex items-center gap-3">
          <div className="w-7 h-7 rounded bg-amber-500/20 border border-amber-500/40 flex items-center justify-center">
            <svg className="w-4 h-4 text-amber-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                d="M17 17H7a2 2 0 01-2-2V5a2 2 0 012-2h10a2 2 0 012 2v10a2 2 0 01-2 2z"/>
            </svg>
          </div>
          <div>
            <span className="font-mono font-bold text-slate-100 tracking-tight">print queue</span>
            <span className="font-mono text-xs text-slate-600 ml-3">AD5X</span>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <span className="font-mono text-xs text-slate-600 mr-2 hidden sm:block">
            {jobs.length} job{jobs.length !== 1 ? "s" : ""}
          </span>
          <ActionButton onClick={sendNext}
                        disabled={!hasQueued}
                        loading={loadingAction === "next"}
                        variant="primary">
            Send Next
          </ActionButton>
          <ActionButton onClick={clearQueue}
                        disabled={!hasQueued}
                        loading={loadingAction === "clear"}
                        variant="default">
            Clear Queue
          </ActionButton>
          <ActionButton onClick={purgeQueue}
                        loading={loadingAction === "purge"}
                        variant="danger">
            Purge
          </ActionButton>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-6 py-6 space-y-6">

        {/* Printer panel */}
        <PrinterPanel printer={status?.printer} loading={loadingStatus} />

        {/* Queue stats strip */}
        <div className="grid grid-cols-4 gap-3">
          {[
            { label: "Queued",  value: queuedJobs.length,  color: "text-amber-400" },
            { label: "Sending", value: activeJobs.length,  color: "text-blue-400"  },
            { label: "Sent",    value: sentJobs.length,    color: "text-green-400" },
            { label: "Errors",  value: errorJobs.length,   color: "text-red-400"   },
          ].map(s => (
            <div key={s.label}
                 className="rounded-lg border border-slate-700 bg-slate-800/40 px-4 py-3 text-center">
              <div className={`font-mono text-2xl font-bold ${s.color}`}
                   style={{ textShadow: "0 0 20px currentColor" }}>
                {s.value}
              </div>
              <div className="font-mono text-xs text-slate-500 mt-0.5 uppercase tracking-wider">
                {s.label}
              </div>
            </div>
          ))}
        </div>

        {/* Job list */}
        <div className="space-y-3">
          {allDisplayJobs.length === 0
            ? <EmptyQueue />
            : allDisplayJobs.map(job => (
                <JobCard key={job.id}
                         job={job}
                         onDelete={deleteJob}
                         isNext={job.id === nextJobId} />
              ))
          }
        </div>

      </main>

      <Toast toasts={toasts} />
    </div>
  );
}