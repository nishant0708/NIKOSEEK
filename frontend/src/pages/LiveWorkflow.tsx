import { useEffect, useMemo, useState, useRef } from 'react';
import { Background, Controls, MarkerType, ReactFlow, type Edge, type Node } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  LoaderCircle, Network, Radio, RefreshCw, Workflow as WorkflowIcon,
  ChevronDown, ChevronRight, AlertTriangle, Eye, X,
  Brain, MousePointerClick, Info, CircleAlert, Flag, Crosshair
} from 'lucide-react';
import { Button } from '../components/ui/button';
import { ApplicationFormPanel } from '../components/workflow/ApplicationFormPanel';
import { StatusActions } from '../components/workflow/StatusActions';
import { api, type AgentEvent, type AgentStageKey, type Application, type ManualStatus } from '../lib/api';

const STAGE_NODES = [
  { id: 'open', label: 'Opening job page', detail: 'Open the posting in your LinkedIn window.' },
  { id: 'login', label: 'Checking LinkedIn login', detail: 'Reuse the connected session; if it expired, finish login there.' },
  { id: 'find', label: 'Finding apply button', detail: 'AI picks Apply → company site → application form.' },
  { id: 'fields', label: 'Filling the form', detail: 'AI fills what your profile answers (Easy Apply: step by step) + resume.' },
  { id: 'review', label: 'Your answers & submit', detail: 'Fill the missing answers below, then press Submit.' },
] as const;
const STAGE_ORDER: AgentStageKey[] = STAGE_NODES.map(node => node.id);
const EVENTS_PER_STAGE_NODE = 3;
const ROW_HEIGHT = 190;

const KIND_STYLE: Record<AgentEvent['kind'], { icon: typeof Brain; color: string; bg: string; label: string }> = {
  thinking: { icon: Brain, color: '#7A3FB8', bg: '#F4ECFF', label: 'AI thinking' },
  action: { icon: MousePointerClick, color: '#2E6BB8', bg: '#E8F1FF', label: 'Action' },
  info: { icon: Info, color: '#6B4329', bg: '#FFF4E7', label: 'Info' },
  warning: { icon: AlertTriangle, color: '#A0590F', bg: '#FFF0DE', label: 'Needs you' },
  error: { icon: CircleAlert, color: '#AD3212', bg: '#FFE1D6', label: 'Error' },
  stage: { icon: Flag, color: '#F56A22', bg: '#FFF9F0', label: 'Stage' },
};

function nodeStyle(state: 'pending' | 'active' | 'done' | 'alert') {
  const palette = {
    pending: { border: '#E8C9A4', background: '#FFF9F0' },
    active: { border: '#F56A22', background: '#FFF0DE' },
    done: { border: '#F4B27E', background: '#FFF4E7' },
    alert: { border: '#E9A38E', background: '#FFF0EB' },
  };
  
  const base = {
    width: 280,
    minHeight: 92,
    borderRadius: 18,
    border: `1px solid ${palette[state].border}`,
    background: palette[state].background,
    color: '#542914',
    padding: '14px 16px',
    whiteSpace: 'pre-line' as const,
    lineHeight: 1.35,
    fontWeight: 800,
    fontSize: 13,
  };
  
  if (state === 'active') {
    return {
      ...base,
      animation: 'pulseGlow 2s infinite',
    };
  }
  
  return base;
}

function stageIndexFromText(stage?: string | null) {
  const value = (stage ?? '').toLowerCase();
  if (value.includes('opening')) return 0;
  if (value.includes('login')) return 1;
  if (value.includes('finding') || value.includes('navigating') || value.includes('found apply')) return 2;
  if (value.includes('fetching') || value.includes('filling')) return 3;
  if (value.includes('review')) return 4;
  return -1;
}

function formatTokens(value?: number) {
  const n = value ?? 0;
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);
}

function timeOf(event: AgentEvent) {
  return event.created_at ? new Date(event.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '';
}

function tokensOf(event: AgentEvent) {
  const tokens = event.data?.tokens as { input?: number; output?: number; total?: number } | undefined;
  return tokens?.total ? `${formatTokens(tokens.total)} tok (${formatTokens(tokens.input)} in / ${formatTokens(tokens.output)} out)` : '';
}

function AgentLogPanel({ events, live, application }: { events: AgentEvent[]; live: boolean; application: Application }) {
  const runTokens = events.reduce((sum, event) => sum + (((event.data?.tokens as { total?: number } | undefined)?.total) ?? 0), 0);
  const list = useRef<HTMLDivElement>(null);
  // Scroll only the log box (scrollIntoView would also scroll the whole page).
  useEffect(() => { if (list.current) list.current.scrollTop = list.current.scrollHeight; }, [events.length]);
  return (
    <div className="rounded-[2rem] border border-[#EBCDAA] bg-[#FFF9F0] p-5">
      <div className="flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-lg font-black text-ink"><Brain size={18} className="text-orange" /> Agent log</h3>
        <div className="flex items-center gap-3 text-xs font-bold">
          <span className="rounded-full bg-[#F4ECFF] px-2.5 py-1 text-[#7A3FB8]" title="This attempt / all attempts of this job">AI: {formatTokens(runTokens)} tokens this run · {formatTokens(application.llm_total_tokens)} total ({application.llm_calls ?? 0} calls)</span>
          {live && <span className="flex items-center gap-2 text-[#8B4E23]"><LoaderCircle size={13} className="animate-spin" /> running</span>}
        </div>
      </div>
      <p className="mt-1 text-xs text-[#805940]">What the agent saw, what it decided and why — saved for every attempt.</p>
      <div ref={list} className="mt-4 max-h-[420px] space-y-2 overflow-y-auto pr-1">
        {!events.length && <p className="rounded-xl border border-dashed border-[#EBCDAA] bg-white p-4 text-sm font-bold text-[#805940]">No log yet for this job.</p>}
        {events.map((event, index) => {
          const kind = KIND_STYLE[event.kind] ?? KIND_STYLE.info;
          const Icon = kind.icon;
          const url = typeof event.data?.url === 'string' ? event.data.url : null;
          if (event.kind === 'stage') {
            return <div key={event.id ?? index} className="flex items-center gap-2 pt-2 text-[11px] font-black uppercase tracking-[.14em] text-orange"><Flag size={12} />{event.message}<span className="ml-auto font-bold normal-case tracking-normal text-[#A16A45]">{timeOf(event)}</span></div>;
          }
          return (
            <div key={event.id ?? index} className="flex gap-3 rounded-xl border px-3 py-2" style={{ background: kind.bg, borderColor: `${kind.color}33` }}>
              <Icon size={16} className="mt-0.5 shrink-0" style={{ color: kind.color }} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-[.12em]" style={{ color: kind.color }}>{kind.label}
                  {tokensOf(event) && <span className="rounded-full bg-white/70 px-1.5 py-0.5 normal-case tracking-normal text-[#7A3FB8]">{tokensOf(event)}</span>}
                  <span className="ml-auto font-bold normal-case tracking-normal text-[#A16A45]">{timeOf(event)}</span></div>
                <p className="mt-0.5 break-words text-sm text-[#542914]">{event.message}</p>
                {url && event.kind !== 'action' && <p className="mt-0.5 truncate font-mono text-[11px] text-[#805940]">{url}</p>}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function stageLabel(application?: Application | null) {
  if (!application) return 'No live activity yet.';
  return application.stage || 'Queued';
}

function QueueCategory({ 
  title, 
  applications, 
  selectedId, 
  onSelect, 
  onRetry,
  onStatus,
  defaultCollapsed = false
}: { 
  title: string;
  applications: Application[];
  selectedId: number | null;
  onSelect: (app: Application) => void;
  onRetry: (app: Application) => Promise<void> | void;
  onStatus: (app: Application, status: ManualStatus) => Promise<void>;
  defaultCollapsed?: boolean;
}) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  if (applications.length === 0) return null;

  const isCompact = applications.length >= 5;

  return (
    <div className="space-y-2 mt-4">
      <div 
        className="flex items-center gap-2 cursor-pointer select-none"
        onClick={() => setCollapsed(!collapsed)}
      >
        {collapsed ? <ChevronRight size={16} className="text-[#A16A45]" /> : <ChevronDown size={16} className="text-[#A16A45]" />}
        <h3 className="text-xs font-black uppercase tracking-[.14em] text-[#A16A45] flex items-center gap-2">
          {title}
          <span className="bg-[#EBCDAA] text-white px-2 py-0.5 rounded-full text-[10px]">{applications.length}</span>
        </h3>
      </div>
      
      {!collapsed && (
        <div className="space-y-3 pl-2 border-l-2 border-[#EBCDAA]">
          {applications.map(application => {
            const active = selectedId === application.id;
            const badgeClass =
              application.status === 'SUBMITTED' || application.status === 'COMPLETED'
                ? 'bg-[#347233] text-white'
                : application.status === 'REJECTED'
                ? 'bg-[#6B4329] text-white'
                : application.status === 'HUMAN_REVIEW_REQUIRED'
                ? 'bg-[#DDF4D5] text-[#347233]'
                : application.status === 'FAILED'
                  ? 'bg-[#FFE1D6] text-[#AD3212]'
                  : application.status === 'IN_PROGRESS'
                    ? 'bg-[#FFE4C8] text-[#8B4E23]'
                    : 'bg-[#FFF0DE] text-[#8B4E23]';
            
            if (isCompact) {
              return (
                <div 
                  key={application.id}
                  onClick={() => onSelect(application)}
                  className={`flex items-center justify-between p-2 rounded-lg cursor-pointer transition ${active ? 'bg-[#FFF4E7] border border-orange shadow-sm' : 'bg-white border border-[#EBCDAA] hover:bg-[#FFF9F0]'}`}
                >
                  <div className="min-w-0 flex-1 mr-2">
                    <p className="truncate text-xs font-bold text-ink">{application.job.title ?? 'Untitled job'}</p>
                    <p className="truncate text-[10px] text-[#805940]">{application.job.company}</p>
                  </div>
                  <span className={`shrink-0 rounded-full px-1.5 py-0.5 text-[9px] font-black uppercase tracking-[.12em] ${badgeClass}`}>
                    {application.status.replace(/_/g, ' ')}
                  </span>
                </div>
              );
            }

            return (
              <article 
                key={application.id}
                onClick={() => onSelect(application)}
                className={`rounded-2xl border bg-white p-4 cursor-pointer transition ${active ? 'border-orange shadow-[0_0_0_6px_rgba(245,106,34,0.12)]' : 'border-[#EBCDAA] hover:bg-[#FFF9F0]'}`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="truncate font-black text-ink">{application.job.title ?? 'Untitled job'}</h3>
                    <p className="mt-1 text-sm text-[#805940]">{application.job.company} · {application.job.location}</p>
                  </div>
                  <span className={`rounded-full px-2 py-1 text-[10px] font-black uppercase tracking-[.12em] ${badgeClass}`}>{application.status.replace(/_/g, ' ')}</span>
                </div>
                <p className="mt-3 text-sm font-bold text-[#875D42]">{stageLabel(application)}</p>
                <div className="mt-3 flex flex-wrap gap-2 text-xs font-bold text-[#805940]">
                  <span className="rounded-full bg-[#FFF4E7] px-3 py-1">Resume: {application.resume_role ?? 'unknown'}</span>
                  <span className="rounded-full bg-[#FFF4E7] px-3 py-1">Queue #{application.queue_position}</span>
                  {(application.llm_total_tokens ?? 0) > 0 && <span className="rounded-full bg-[#F4ECFF] px-3 py-1 text-[#7A3FB8]">{formatTokens(application.llm_total_tokens)} tokens · {application.llm_calls} AI calls</span>}
                </div>
                <div className="mt-3"><StatusActions application={application} onRetry={onRetry} onStatus={onStatus} /></div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function LiveWorkflow({ token }: { token: string }) {
  const [applications, setApplications] = useState<Application[]>([]);
  const [connected, setConnected] = useState(false);
  const [busy, setBusy] = useState(false);
  const [selectedApplicationId, setSelectedApplicationId] = useState<number | null>(null);
  const [toast, setToast] = useState<{ message: string; id: number } | null>(null);
  // Latest attempt's agent log per application, kept live from the websocket.
  const [eventsByApp, setEventsByApp] = useState<Record<number, AgentEvent[]>>({});

  const prevAppsRef = useRef<Map<number, string>>(new Map());
  const toastCountRef = useRef(0);

  const showToast = (message: string) => {
    const id = ++toastCountRef.current;
    setToast({ message, id });
    window.setTimeout(() => setToast(current => current?.id === id ? null : current), 8000);
  };

  const upsertApplication = (app: Application) => {
    setApplications(current => {
      const next = current.filter(item => item.id !== app.id);
      next.push(app);
      return next.sort((left, right) => left.queue_position - right.queue_position);
    });
    const prevStatus = prevAppsRef.current.get(app.id);
    if (prevStatus && prevStatus !== app.status) {
      if (app.status === 'HUMAN_REVIEW_REQUIRED' && prevStatus === 'IN_PROGRESS') showToast(`🔔 ${app.job.title ?? 'A job'} needs you: ${app.stage}`);
      if (app.status === 'SUBMITTED') showToast(`✅ ${app.job.title ?? 'A job'} — application submitted!`);
      if (app.status === 'FAILED') showToast(`⚠️ ${app.job.title ?? 'A job'} failed: ${(app.error ?? '').slice(0, 80)}`);
    }
    prevAppsRef.current.set(app.id, app.status);
  };

  const appendEvent = (event: AgentEvent) => {
    setEventsByApp(current => {
      const list = current[event.application_id] ?? [];
      const currentAttempt = list[list.length - 1]?.attempt ?? 0;
      if (event.attempt < currentAttempt) return current;
      const base = event.attempt > currentAttempt ? [] : list; // a retry starts a fresh log
      if (event.id != null && base.some(item => item.id === event.id)) return current;
      return { ...current, [event.application_id]: [...base, event] };
    });
  };

  useEffect(() => {
    api.applications(token).then(apps => {
      setApplications(apps);
      apps.forEach(app => prevAppsRef.current.set(app.id, app.status));
    }).catch(() => undefined);
  }, [token]);

  useEffect(() => {
    let cancelled = false;
    let socket: WebSocket | null = null;
    let ping: number | undefined;

    const connect = () => {
      if (cancelled) return;
      socket = new WebSocket(api.applicationSocketUrl(token));
      socket.onopen = () => {
        setConnected(true);
        ping = window.setInterval(() => socket?.readyState === WebSocket.OPEN && socket.send('ping'), 25000);
      };
      socket.onclose = () => {
        setConnected(false);
        window.clearInterval(ping);
        if (!cancelled) window.setTimeout(connect, 1500);
      };
      socket.onerror = () => setConnected(false);
      socket.onmessage = event => {
        try {
          const payload = JSON.parse(event.data) as { type?: string; applications?: Application[]; application?: Application; event?: AgentEvent };
          if (payload.type === 'snapshot' && payload.applications) {
            setApplications(payload.applications);
            payload.applications.forEach(app => prevAppsRef.current.set(app.id, app.status));
          } else if (payload.type === 'application.updated' && payload.application) {
            upsertApplication(payload.application);
          } else if (payload.type === 'application.event' && payload.event) {
            appendEvent(payload.event);
          }
        } catch {
          return;
        }
      };
    };

    connect();
    return () => {
      cancelled = true;
      window.clearInterval(ping);
      socket?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const activeApplication = applications.find(application => application.status === 'IN_PROGRESS')
    ?? applications.find(application => application.status === 'PENDING')
    ?? applications.find(application => application.status === 'HUMAN_REVIEW_REQUIRED')
    ?? null;

  // null = "follow live": always show whatever job the worker is on right now.
  const selectedApp = (selectedApplicationId ? applications.find(a => a.id === selectedApplicationId) : null) ?? activeApplication;
  const following = selectedApplicationId === null;

  // Load the saved log whenever the shown job changes (and refresh it when its status changes).
  useEffect(() => {
    if (!selectedApp) return;
    let cancelled = false;
    api.applicationEvents(token, selectedApp.id)
      .then(result => {
        if (cancelled) return;
        setEventsByApp(current => {
          const live = current[selectedApp.id] ?? [];
          const liveAttempt = live[live.length - 1]?.attempt ?? 0;
          if (liveAttempt > result.attempt) return current;
          const seen = new Set(result.events.map(item => item.id));
          const extra = live.filter(item => item.attempt === result.attempt && item.id != null && !seen.has(item.id));
          return { ...current, [selectedApp.id]: [...result.events, ...extra] };
        });
      })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [token, selectedApp?.id, selectedApp?.status]);

  const failedApplications = applications.filter(application => application.status === 'FAILED');
  const queuedApplications = applications.filter(application => application.status === 'PENDING');
  const inProgressApplications = applications.filter(application => application.status === 'IN_PROGRESS');
  const reviewApplications = applications.filter(application => application.status === 'HUMAN_REVIEW_REQUIRED');
  const submittedApplications = applications.filter(application => application.status === 'SUBMITTED');
  const completedApplications = applications.filter(application => application.status === 'COMPLETED');
  const rejectedApplications = applications.filter(application => application.status === 'REJECTED');

  const events = selectedApp ? eventsByApp[selectedApp.id] ?? [] : [];
  const isFailed = selectedApp?.status === 'FAILED';
  const isReview = selectedApp?.status === 'HUMAN_REVIEW_REQUIRED';
  const isRunning = selectedApp?.status === 'IN_PROGRESS';
  const isSubmitted = selectedApp?.status === 'SUBMITTED' || selectedApp?.status === 'COMPLETED';

  // Where are we? Prefer the agent log (exact), fall back to the stage text.
  const currentIndex = useMemo(() => {
    if (!selectedApp || selectedApp.status === 'PENDING') return -1;
    if (isSubmitted) return STAGE_NODES.length; // everything done
    if (isReview) return STAGE_ORDER.indexOf('review');
    const errorEvent = [...events].reverse().find(item => item.kind === 'error');
    const failedAt = errorEvent?.data?.failed_at as AgentStageKey | undefined;
    const last = [...events].reverse().find(item => STAGE_ORDER.includes(item.stage_key));
    const key = (isFailed && failedAt) || last?.stage_key;
    const fromEvents = key ? STAGE_ORDER.indexOf(key) : -1;
    return fromEvents >= 0 ? fromEvents : stageIndexFromText(selectedApp.stage);
  }, [selectedApp, events, isFailed, isReview, isSubmitted]);

  const nodes: Node[] = useMemo(() => {
    const stageNodes: Node[] = STAGE_NODES.map((node, index) => {
      let state: 'pending' | 'active' | 'done' | 'alert' = 'pending';
      let label: string = node.label;
      if (isFailed && currentIndex === index) state = 'alert';
      else if (isReview && index === currentIndex) state = 'alert';
      else if (currentIndex > index) { state = 'done'; label = `✅ ${label}`; }
      else if (currentIndex === index && isRunning) { state = 'active'; label = `⏳ ${label}`; }
      const style = nodeStyle(state);
      return {
        id: node.id,
        position: { x: 40, y: 28 + index * ROW_HEIGHT },
        data: { label: `${label}\n${node.detail}` },
        style: state === 'active' ? { ...style, animation: 'pulseGlow 2s infinite' } : style,
      };
    });
    // AI log bubbles next to each stage: the latest few decisions/actions made in that stage.
    const logNodes: Node[] = STAGE_NODES.flatMap((node, index) => {
      const stageEvents = events.filter(item => item.stage_key === node.id && item.kind !== 'stage').slice(-EVENTS_PER_STAGE_NODE);
      return stageEvents.map((item, j) => {
        const kind = KIND_STYLE[item.kind] ?? KIND_STYLE.info;
        return {
          id: `log-${node.id}-${item.id ?? j}`,
          position: { x: 380, y: 20 + index * ROW_HEIGHT + j * 62 },
          type: 'output',
          data: { label: `${kind.label}: ${item.message.length > 110 ? `${item.message.slice(0, 110)}…` : item.message}` },
          style: {
            width: 380, borderRadius: 12, border: `1px solid ${kind.color}55`, background: kind.bg, color: '#542914',
            padding: '6px 10px', fontSize: 11, fontWeight: 600, lineHeight: 1.3, textAlign: 'left' as const,
          },
        };
      });
    });
    return [...stageNodes, ...logNodes];
  }, [events, currentIndex, isFailed, isReview, isRunning]);

  const flowEdges: Edge[] = useMemo(() => {
    const stageEdges: Edge[] = STAGE_NODES.slice(0, -1).map((node, index) => ({
      id: `${node.id}-${STAGE_NODES[index + 1].id}`,
      source: node.id,
      target: STAGE_NODES[index + 1].id,
      animated: isRunning && currentIndex === index,
      style: { stroke: currentIndex > index ? '#F56A22' : '#E8C9A4', strokeWidth: 2 },
      markerEnd: { type: MarkerType.ArrowClosed, color: currentIndex > index ? '#F56A22' : '#E8C9A4' },
    }));
    return stageEdges;
  }, [currentIndex, isRunning]);

  async function changeStatus(application: Application, status: ManualStatus) {
    try { upsertApplication(await api.setApplicationStatus(token, application.id, status)); }
    catch (e) { showToast(`⚠️ ${e instanceof Error ? e.message : 'Could not change the status.'}`); }
  }

  async function retryApplication(application: Application) {
    if (!application.job_id) return;
    setBusy(true);
    try {
      await api.applyJobs(token, { job_ids: [application.job_id], resume_key: application.resume_key });
    } catch (e) {
      showToast(`⚠️ ${e instanceof Error ? e.message : 'Could not retry this job.'}`);
    } finally {
      setBusy(false);
    }
  }


  return (
    <>
      <style>{`
        @keyframes pulseGlow {
          0% { box-shadow: 0 0 0 0 rgba(245, 106, 34, 0.4); }
          70% { box-shadow: 0 0 0 15px rgba(245, 106, 34, 0); }
          100% { box-shadow: 0 0 0 0 rgba(245, 106, 34, 0); }
        }
        @keyframes pulseEdge {
          0% { opacity: 1; }
          50% { opacity: 0.3; }
          100% { opacity: 1; }
        }
        @keyframes slideIn {
          from { transform: translateY(-100%); opacity: 0; }
          to { transform: translateY(0); opacity: 1; }
        }
      `}</style>
      
      {toast && (
        <div className="fixed top-4 left-1/2 -translate-x-1/2 z-50 bg-[#FFF0DE] border border-orange shadow-lg rounded-full px-6 py-3 flex items-center gap-3 text-sm font-bold text-[#8B4E23]" style={{ animation: 'slideIn 0.3s ease-out' }}>
          {toast.message}
          <button onClick={() => setToast(null)} className="ml-2 text-[#8B4E23] hover:text-orange">
            <X size={14} />
          </button>
        </div>
      )}

      <main className="min-h-screen bg-cream px-5 py-9">
        <section className="mx-auto max-w-7xl">
          <p className="flex items-center gap-2 text-xs font-black uppercase tracking-[.16em] text-orange"><Network size={15} /> Live workflow</p>
          <h1 className="mt-2 text-4xl font-black">Queue, status, and retries</h1>
          <p className="mt-3 max-w-2xl text-[#805940]">Watch each job live: the agent opens the posting in your LinkedIn window, finds the company’s application form, fills it from your profile and hands it to you for review. Every AI decision is logged below.</p>

          <div className="mt-7 grid gap-6 lg:grid-cols-[minmax(0,1.2fr)_380px]">
            <div className="flex flex-col gap-6">
              {selectedApp?.status === 'HUMAN_REVIEW_REQUIRED' && (
                <div className="bg-[#FFF0EB] border border-[#E9A38E] rounded-xl p-4 flex items-center justify-between shadow-sm">
                  <div className="flex items-center gap-3">
                    <AlertTriangle className="text-[#AD3212]" size={20} />
                    <div>
                      <p className="font-bold text-[#AD3212]">{selectedApp.stage}</p>
                      <p className="text-sm text-[#AD3212]/80">{selectedApp.job.title} is open in the LinkedIn browser window. Answer the missing fields in “Application form” below and submit from here — or do it directly in the browser.</p>
                    </div>
                  </div>
                  {selectedApp.application_url && (
                    <a 
                      href={selectedApp.application_url} 
                      target="_blank" 
                      rel="noreferrer"
                      className="inline-flex items-center gap-2 bg-[#AD3212] text-white px-4 py-2 rounded-lg text-sm font-bold hover:bg-[#8B280F] transition"
                    >
                      <Eye size={16} />
                      Open form URL
                    </a>
                  )}
                </div>
              )}

              <div className="overflow-hidden rounded-[2rem] border border-[#EBCDAA] bg-[#FFF9F0]">
                <div className="flex items-center justify-between border-b border-[#EBCDAA] px-5 py-4">
                  <div className="flex items-center gap-2">
                    <WorkflowIcon className="text-orange" size={18} />
                    <p className="font-black text-ink">Live application flow</p>
                  </div>
                  <div className="flex items-center gap-2">
                    {selectedApp && <span className="hidden max-w-[260px] truncate text-xs font-bold text-[#805940] sm:inline">{selectedApp.job.title} · {selectedApp.job.company}</span>}
                    {selectedApp && <StatusActions application={selectedApp} onRetry={retryApplication} onStatus={changeStatus} size="xs" />}
                    {!following && (
                      <button type="button" onClick={() => setSelectedApplicationId(null)} className="inline-flex items-center gap-1 rounded-full bg-[#FFE4C8] px-3 py-1 text-xs font-bold text-[#82512D] hover:bg-[#FFD7AE]">
                        <Crosshair size={13} /> Follow live
                      </button>
                    )}
                    <span className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-bold ${connected ? 'bg-[#DDF4D5] text-[#347233]' : 'bg-[#FFE1D6] text-[#AD3212]'}`}>
                      <Radio size={13} /> {connected ? 'Live' : 'Reconnecting'}
                    </span>
                  </div>
                </div>
                <div className="h-[720px] overflow-hidden bg-[radial-gradient(circle_at_1px_1px,rgba(245,106,34,0.10)_1px,transparent_0)] bg-[length:24px_24px]">
                  <ReactFlow
                    key={`${selectedApp?.id ?? 'none'}-${nodes.length}`}
                    nodes={nodes}
                    edges={flowEdges}
                    fitView
                    fitViewOptions={{ padding: 0.18, minZoom: 0.55, maxZoom: 1.15 }}
                    nodesDraggable={false}
                    nodesConnectable={false}
                    elementsSelectable={false}
                    panOnDrag={false}
                    defaultEdgeOptions={{ type: 'smoothstep' }}
                    proOptions={{ hideAttribution: true }}
                  >
                    <Background color="#E6B98B" gap={18} size={1} />
                    <Controls showInteractive={false} />
                  </ReactFlow>
                </div>
              </div>

              {selectedApp && <AgentLogPanel events={events} live={isRunning} application={selectedApp} />}

              {selectedApp && ['HUMAN_REVIEW_REQUIRED', 'SUBMITTED', 'COMPLETED', 'REJECTED', 'FAILED'].includes(selectedApp.status) && (
                <ApplicationFormPanel token={token} application={selectedApp} onUpdated={upsertApplication} />
              )}
            </div>

            <aside className="rounded-[2rem] border border-[#EBCDAA] bg-[#FFF9F0] p-5 max-h-[calc(100vh-200px)] overflow-y-auto">
              <div className="flex items-center gap-2">
                <LoaderCircle className="text-orange" size={18} />
                <h2 className="text-xl font-black">Queue</h2>
              </div>
              <p className="mt-2 text-sm text-[#805940]">Queued jobs, active jobs, failed jobs, and retry actions.</p>
              
              <div className="mt-4">
                <QueueCategory 
                  title="In progress" 
                  applications={inProgressApplications} 
                  selectedId={selectedApp?.id ?? null}
                  onSelect={(app) => setSelectedApplicationId(app.id)}
                  onRetry={retryApplication}
                  onStatus={changeStatus}
                />
                
                <QueueCategory 
                  title="Human review required" 
                  applications={reviewApplications} 
                  selectedId={selectedApp?.id ?? null}
                  onSelect={(app) => setSelectedApplicationId(app.id)}
                  onRetry={retryApplication}
                  onStatus={changeStatus}
                />
                
                <QueueCategory 
                  title="Queued" 
                  applications={queuedApplications} 
                  selectedId={selectedApp?.id ?? null}
                  onSelect={(app) => setSelectedApplicationId(app.id)}
                  onRetry={retryApplication}
                  onStatus={changeStatus}
                  defaultCollapsed={queuedApplications.length >= 10}
                />
                
                <QueueCategory
                  title="Submitted"
                  applications={submittedApplications}
                  selectedId={selectedApp?.id ?? null}
                  onSelect={(app) => setSelectedApplicationId(app.id)}
                  onRetry={retryApplication}
                  onStatus={changeStatus}
                  defaultCollapsed={submittedApplications.length >= 5}
                />

                <QueueCategory
                  title="Completed"
                  applications={completedApplications}
                  selectedId={selectedApp?.id ?? null}
                  onSelect={(app) => setSelectedApplicationId(app.id)}
                  onRetry={retryApplication}
                  onStatus={changeStatus}
                  defaultCollapsed={completedApplications.length >= 5}
                />

                <QueueCategory
                  title="Rejected"
                  applications={rejectedApplications}
                  selectedId={selectedApp?.id ?? null}
                  onSelect={(app) => setSelectedApplicationId(app.id)}
                  onRetry={retryApplication}
                  onStatus={changeStatus}
                  defaultCollapsed
                />

                <QueueCategory 
                  title="Failed" 
                  applications={failedApplications} 
                  selectedId={selectedApp?.id ?? null}
                  onSelect={(app) => setSelectedApplicationId(app.id)}
                  onRetry={retryApplication}
                  onStatus={changeStatus}
                />

                {!applications.length && (
                  <div className="mt-4 rounded-2xl border border-dashed border-[#EBCDAA] bg-white p-5 text-sm font-bold text-[#805940]">
                    No applications have been queued yet. Use Apply selected from Jobs to start the worker.
                  </div>
                )}
              </div>
            </aside>
          </div>
        </section>
      </main>
    </>
  );
}
