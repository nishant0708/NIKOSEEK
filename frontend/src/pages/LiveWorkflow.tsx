import { useEffect, useMemo, useState, useRef, type ReactNode } from 'react';
import { Background, Controls, MarkerType, ReactFlow, type Edge, type Node } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  LoaderCircle, Radio, Workflow as WorkflowIcon,
  ChevronDown, ChevronRight, AlertTriangle, Eye, X,
  Brain, MousePointerClick, Info, CircleAlert, Flag, Crosshair, CircleHelp,
  Bell, BriefcaseBusiness, ExternalLink, Maximize2, Minimize2, MonitorPlay, ListChecks, Inbox, FileText,
} from 'lucide-react';
import { ApplicationFormPanel } from '../components/workflow/ApplicationFormPanel';
import { StatusActions } from '../components/workflow/StatusActions';
import { LiveBrowserPanel } from '../components/workflow/LiveBrowserPanel';
import { AgentQuestionCard } from '../components/workflow/AgentQuestionCard';
import { InstructAgent } from '../components/workflow/InstructAgent';
import { ResumePanel } from '../components/workflow/ResumePanel';
import { api, type AgentEvent, type AgentStageKey, type Application, type BrowserFrame, type ManualStatus } from '../lib/api';

const STAGE_NODES = [
  { id: 'open', label: 'Opening job page', detail: 'Open the posting in your {provider} window.' },
  { id: 'login', label: 'Checking {provider} login', detail: 'Reuse the connected session; if it expired, finish login there.' },
  { id: 'resume', label: 'Resume agent', detail: 'Reads the JD on the posting, scores your resumes against it; tailors one from your profile when it helps.' },
  { id: 'find', label: 'Finding apply button', detail: 'Easy Apply / Indeed Apply, or Apply → company site (Workday & co: sign up / sign in for you). Stuck? It asks you.' },
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
  question: { icon: CircleHelp, color: '#C2410C', bg: '#FFEAD5', label: 'Asked you' },
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
  const at = (key: AgentStageKey) => STAGE_ORDER.indexOf(key);
  if (value.startsWith('resume agent')) return at('resume');
  if (value.includes('opening')) return at('open');
  if (value.includes('login')) return at('login');
  if (value.includes('finding') || value.includes('navigating') || value.includes('found apply')) return at('find');
  if (value.includes('fetching') || value.includes('filling')) return at('fields');
  if (value.includes('review')) return at('review');
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


function AgentLogPanel({ events, live, application, height }: { events: AgentEvent[]; live: boolean; application: Application; height: number }) {
  const runTokens = events.reduce((sum, event) => sum + (((event.data?.tokens as { total?: number } | undefined)?.total) ?? 0), 0);
  const list = useRef<HTMLDivElement>(null);
  // Scroll only the log box (scrollIntoView would also scroll the whole page).
  useEffect(() => { if (list.current) list.current.scrollTop = list.current.scrollHeight; }, [events.length]);
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 text-xs font-bold">
        <span className="rounded-full bg-[#F4ECFF] px-2.5 py-1 text-[#7A3FB8]" title="This attempt / all attempts of this job">AI: {formatTokens(runTokens)} tokens this run · {formatTokens(application.llm_total_tokens)} total ({application.llm_calls ?? 0} calls)</span>
        {live && <span className="flex items-center gap-2 text-[#8B4E23]"><LoaderCircle size={13} className="animate-spin" /> running</span>}
      </div>
      <div ref={list} style={{ maxHeight: height }} className="mt-3 space-y-2 overflow-y-auto pr-1">
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

function newestFirst(apps: Application[]) {
  const t = (a: Application) => Date.parse(a.created_at ?? '') || 0;
  return [...apps].sort((a, b) => t(b) - t(a) || b.id - a.id);
}

function timeAgo(value?: string | null) {
  const ms = Date.now() - (Date.parse(value ?? '') || Date.now());
  const m = Math.round(ms / 60000);
  if (m < 1) return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  return h < 24 ? `${h}h ago` : `${Math.round(h / 24)}d ago`;
}

const STATUS_DOT: Record<string, string> = {
  HUMAN_REVIEW_REQUIRED: '#E2711D', IN_PROGRESS: '#2E6BB8', PENDING: '#C9A27E', FAILED: '#AD3212',
  SUBMITTED: '#347233', COMPLETED: '#347233', REJECTED: '#6B4329',
};

/** One status group in the queue sidebar — collapsed until you open it; newest job first. */
function QueueGroup({ title, applications, selectedId, onSelect, alert }: {
  title: string; applications: Application[]; selectedId: number | null; onSelect: (app: Application) => void; alert?: boolean;
}) {
  const [open, setOpen] = useState(false);
  if (!applications.length) return null;
  return (
    <div className="rounded-2xl border border-[#EBCDAA] bg-white">
      <button type="button" onClick={() => setOpen(v => !v)} className="flex w-full items-center gap-2 px-3 py-2.5 text-left">
        {open ? <ChevronDown size={15} className="text-[#A16A45]" /> : <ChevronRight size={15} className="text-[#A16A45]" />}
        <span className="flex-1 text-xs font-black uppercase tracking-[.12em] text-[#82512D]">{title}</span>
        {alert && <span className="h-2 w-2 rounded-full bg-[#E2711D]" />}
        <span className="rounded-full bg-[#FFE4C8] px-2 py-0.5 text-[11px] font-black text-[#82512D]">{applications.length}</span>
      </button>
      {open && (
        <div className="space-y-1 border-t border-[#F3DFC8] p-1.5">
          {applications.map(application => {
            const active = selectedId === application.id;
            return (
              <button type="button" key={application.id} onClick={() => onSelect(application)}
                className={`flex w-full items-start gap-2 rounded-xl px-2.5 py-2 text-left transition ${active ? 'bg-[#FFF0DE] ring-1 ring-orange' : 'hover:bg-[#FFF9F0]'}`}>
                <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full" style={{ background: STATUS_DOT[application.status] ?? '#C9A27E' }} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-[13px] font-bold text-ink">{application.job.title ?? 'Untitled job'}</span>
                  <span className="block truncate text-[11px] text-[#805940]">{application.job.company} · {timeAgo(application.created_at)}</span>
                </span>
                {application.job.fetched_by_cron && <span className="shrink-0 rounded-full bg-[#F0E7FF] px-1.5 py-0.5 text-[9px] font-black uppercase tracking-wide text-[#7541AE]">Cron</span>}
                {application.pending_question && <Bell size={13} className="mt-1 shrink-0 text-[#E2711D]" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

type TileMode = 'normal' | 'expanded' | 'collapsed';
const TILE_KEY = 'nikoseek.liveTiles';
// Default layout: Job details and Agent log full width; Live flow and Live browser side by side under them.
const DEFAULT_TILES: Record<string, TileMode> = { details: 'expanded', log: 'expanded', flow: 'normal', browser: 'normal', resume: 'normal' };

function readTiles(): Record<string, TileMode> {
  try { return JSON.parse(window.localStorage.getItem(TILE_KEY) ?? '{}') as Record<string, TileMode>; } catch { return {}; }
}

/** A small box on the job view; expand it to full width / taller, or collapse it to its title. */
function Tile({ title, icon, mode, onMode, right, children }: {
  title: string; icon: ReactNode; mode: TileMode; onMode: (mode: TileMode) => void; right?: ReactNode; children: ReactNode;
}) {
  return (
    <div className={`min-w-0 overflow-hidden rounded-[1.5rem] border border-[#EBCDAA] bg-[#FFF9F0] ${mode === 'expanded' ? 'xl:col-span-2' : ''}`}>
      <div className="flex items-center gap-2 border-b border-[#F3DFC8] px-4 py-2.5">
        <button type="button" onClick={() => onMode(mode === 'collapsed' ? 'normal' : 'collapsed')} className="flex min-w-0 flex-1 items-center gap-2 text-left">
          {mode === 'collapsed' ? <ChevronRight size={15} className="text-[#A16A45]" /> : <ChevronDown size={15} className="text-[#A16A45]" />}
          <span className="text-orange">{icon}</span>
          <span className="truncate font-black text-ink">{title}</span>
        </button>
        {right}
        {mode !== 'collapsed' && (
          <button type="button" title={mode === 'expanded' ? 'Make smaller' : 'Expand'} onClick={() => onMode(mode === 'expanded' ? 'normal' : 'expanded')}
            className="rounded-full p-1.5 text-[#82512D] hover:bg-[#FFE4C8]">{mode === 'expanded' ? <Minimize2 size={14} /> : <Maximize2 size={14} />}</button>
        )}
      </div>
      {mode !== 'collapsed' && <div className="p-4">{children}</div>}
    </div>
  );
}

function Detail({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[10px] font-black uppercase tracking-[.12em] text-[#A16A45]">{label}</dt>
      <dd className="mt-0.5 break-words text-sm font-bold text-[#542914]">{children}</dd>
    </div>
  );
}

function fmtDate(value?: string | null) {
  return value ? new Date(value).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '—';
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
  const prevQuestionRef = useRef<Map<number, string | null>>(new Map());
  // Live browser view: which application's tab is streamed, and the latest frame.
  const socketRef = useRef<WebSocket | null>(null);
  const watchRef = useRef<number | null>(null);
  const [browserFrame, setBrowserFrame] = useState<BrowserFrame | null>(null);
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
    if (app.pending_question && prevQuestionRef.current.get(app.id) !== app.pending_question.id) {
      showToast(`🙋 ${app.job.title ?? 'A job'}: the agent needs your answer.`);
    }
    prevQuestionRef.current.set(app.id, app.pending_question?.id ?? null);
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
      socketRef.current = socket;
      socket.onopen = () => {
        setConnected(true);
        if (watchRef.current) socket?.send(JSON.stringify({ type: 'browser.watch', application_id: watchRef.current }));
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
          const payload = JSON.parse(event.data) as { type?: string; applications?: Application[]; application?: Application; event?: AgentEvent; application_id?: number } & Partial<BrowserFrame> & Partial<Pick<Application, 'llm_input_tokens' | 'llm_output_tokens' | 'llm_total_tokens' | 'llm_calls'>>;
          if (payload.type === 'snapshot' && payload.applications) {
            setApplications(payload.applications);
            payload.applications.forEach(app => prevAppsRef.current.set(app.id, app.status));
          } else if (payload.type === 'application.updated' && payload.application) {
            upsertApplication(payload.application);
          } else if (payload.type === 'application.event' && payload.event) {
            appendEvent(payload.event);
          } else if (payload.type === 'application.tokens' && payload.application_id) {
            const { application_id: id, llm_input_tokens, llm_output_tokens, llm_total_tokens, llm_calls } = payload;
            setApplications(current => current.map(app => app.id === id ? { ...app, llm_input_tokens, llm_output_tokens, llm_total_tokens, llm_calls } : app));
          } else if (payload.type === 'browser.frame' && payload.application_id) {
            setBrowserFrame(payload as BrowserFrame);
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

  // A question can belong to a later queued application while an older application is
  // still running. Follow the application that needs the user before queue order.
  const activeApplication = applications.find(application => application.pending_question)
    ?? applications.find(application => application.status === 'IN_PROGRESS')
    ?? applications.find(application => application.status === 'HUMAN_REVIEW_REQUIRED')
    ?? applications.find(application => application.status === 'PENDING')
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

  const provider = (selectedApp?.job.source ?? '').toLowerCase() === 'indeed' ? 'Indeed' : 'LinkedIn';

  // Stream the selected job's browser tab while it has one (running or waiting for you).
  const watchId = selectedApp && ['IN_PROGRESS', 'HUMAN_REVIEW_REQUIRED'].includes(selectedApp.status) ? selectedApp.id : null;
  useEffect(() => {
    watchRef.current = watchId;
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(watchId ? { type: 'browser.watch', application_id: watchId } : { type: 'browser.unwatch' }));
    }
    if (!watchId) setBrowserFrame(null);
  }, [watchId, connected]);

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

  // The resume node only lights up when the resume agent actually ran for this job (it is optional).
  const resumeRan = events.some(item => item.stage_key === 'resume') || Boolean(selectedApp?.resume_agent?.status || selectedApp?.resume_agent?.pinned);
  const nodes: Node[] = useMemo(() => {
    const stageNodes: Node[] = STAGE_NODES.map((node, index) => {
      let state: 'pending' | 'active' | 'done' | 'alert' = 'pending';
      let label: string = node.label;
      if (node.id === 'resume' && !resumeRan) label = `${label} (off for this job)`;
      else if (isFailed && currentIndex === index) state = 'alert';
      else if (isReview && index === currentIndex) state = 'alert';
      else if (currentIndex > index) { state = 'done'; label = `✅ ${label}`; }
      else if (currentIndex === index && isRunning) { state = 'active'; label = `⏳ ${label}`; }
      const style = nodeStyle(state);
      return {
        id: node.id,
        position: { x: 40, y: 28 + index * ROW_HEIGHT },
        data: { label: `${label.replace('{provider}', provider)}\n${node.detail.replace('{provider}', provider)}` },
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
  }, [events, currentIndex, isFailed, isReview, isRunning, provider, resumeRan]);

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
      // Passing an automatically chosen resume back as `resume_key` pins it and
      // prevents the resume agent from evaluating it again. Only preserve a
      // resume that the applicant explicitly pinned themselves.
      await api.applyJobs(token, { job_ids: [application.job_id], resume_key: application.resume_agent?.pinned ? application.resume_key : undefined });
    } catch (e) {
      showToast(`⚠️ ${e instanceof Error ? e.message : 'Could not retry this job.'}`);
    } finally {
      setBusy(false);
    }
  }


  const [tiles, setTiles] = useState<Record<string, TileMode>>(readTiles);
  const tileMode = (id: string) => tiles[id] ?? DEFAULT_TILES[id] ?? 'normal';
  const setTile = (id: string) => (mode: TileMode) => setTiles(current => {
    const next = { ...current, [id]: mode };
    try { window.localStorage.setItem(TILE_KEY, JSON.stringify(next)); } catch { /* private mode */ }
    return next;
  });
  const needsYou = newestFirst(applications.filter(a => a.status === 'HUMAN_REVIEW_REQUIRED' || (a.pending_question && a.status !== 'HUMAN_REVIEW_REQUIRED')));
  const groups: { title: string; apps: Application[]; alert?: boolean }[] = [
    { title: 'Human review', apps: needsYou, alert: needsYou.some(a => a.pending_question) },
    { title: 'In progress', apps: newestFirst(inProgressApplications.filter(a => !a.pending_question)) },
    { title: 'Queued', apps: newestFirst(queuedApplications) },
    { title: 'Failed', apps: newestFirst(failedApplications) },
    { title: 'Submitted', apps: newestFirst(submittedApplications) },
    { title: 'Completed', apps: newestFirst(completedApplications) },
    { title: 'Rejected', apps: newestFirst(rejectedApplications) },
  ];
  const job = selectedApp?.job;
  const formUrl = selectedApp?.application_url;

  return (
    <>
      <style>{`
        @keyframes pulseGlow {
          0% { box-shadow: 0 0 0 0 rgba(245, 106, 34, 0.4); }
          70% { box-shadow: 0 0 0 15px rgba(245, 106, 34, 0); }
          100% { box-shadow: 0 0 0 0 rgba(245, 106, 34, 0); }
        }
        @keyframes slideIn {
          from { transform: translateY(-100%); opacity: 0; }
          to { transform: translateY(0); opacity: 1; }
        }
      `}</style>

      {toast && (
        <div className="fixed top-4 left-1/2 -translate-x-1/2 z-50 bg-[#FFF0DE] border border-orange shadow-lg rounded-full px-6 py-3 flex items-center gap-3 text-sm font-bold text-[#8B4E23]" style={{ animation: 'slideIn 0.3s ease-out' }}>
          {toast.message}
          <button onClick={() => setToast(null)} className="ml-2 text-[#8B4E23] hover:text-orange"><X size={14} /></button>
        </div>
      )}

      <main className="min-h-screen bg-cream px-5 py-7">
        <div className="mx-auto flex max-w-[1500px] flex-col gap-6 lg:flex-row">
          <aside className="lg:sticky lg:top-24 lg:h-[calc(100vh-7.5rem)] lg:w-80 lg:shrink-0 lg:overflow-y-auto">
            <div className="rounded-[1.75rem] border border-[#EBCDAA] bg-[#FFF9F0] p-3">
              <div className="flex items-center gap-2 px-2 pb-2 pt-1">
                <ListChecks size={16} className="text-orange" />
                <p className="flex-1 text-sm font-black text-ink">Queue</p>
                <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-bold ${connected ? 'bg-[#DDF4D5] text-[#347233]' : 'bg-[#FFE1D6] text-[#AD3212]'}`}>
                  <Radio size={11} /> {connected ? 'Live' : 'Reconnecting'}
                </span>
              </div>
              {!following && (
                <button type="button" onClick={() => setSelectedApplicationId(null)} className="mb-2 flex w-full items-center justify-center gap-1 rounded-xl bg-[#FFE4C8] px-3 py-1.5 text-xs font-bold text-[#82512D] hover:bg-[#FFD7AE]">
                  <Crosshair size={13} /> Follow the live job
                </button>
              )}
              <div className="space-y-2">
                {groups.map(group => (
                  <QueueGroup key={group.title} title={group.title} applications={group.apps} alert={group.alert}
                    selectedId={selectedApp?.id ?? null} onSelect={app => setSelectedApplicationId(app.id)} />
                ))}
                {!applications.length && (
                  <div className="rounded-2xl border border-dashed border-[#EBCDAA] bg-white p-4 text-sm font-bold text-[#805940]">
                    Nothing queued yet. Use Apply selected on the Jobs page.
                  </div>
                )}
              </div>
            </div>
          </aside>

          <section className="min-w-0 flex-1 space-y-4">
            {!selectedApp ? (
              <div className="grid min-h-[50vh] place-items-center rounded-[2rem] border border-dashed border-[#EBCDAA] bg-[#FFF9F0] p-10 text-center">
                <div><Inbox className="mx-auto text-orange" /><p className="mt-3 font-black text-ink">Pick a job from the queue</p><p className="mt-1 text-sm text-[#805940]">Its details, agent log, live flow and live browser open here.</p></div>
              </div>
            ) : (
              <>
                <div className="rounded-[1.75rem] border border-[#EBCDAA] bg-[#FFF9F0] p-5">
                  <div className="flex flex-wrap items-start gap-3">
                    <div className="min-w-0 flex-1">
                      <p className="text-[11px] font-black uppercase tracking-[.14em] text-orange">{following ? 'Following the live job' : 'Selected job'}</p>
                      <h1 className="mt-1 truncate text-2xl font-black text-ink">{job?.title ?? 'Untitled job'}</h1>
                      <p className="mt-0.5 text-sm text-[#805940]">{job?.company} · {job?.location}</p>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="rounded-full px-2.5 py-1 text-[10px] font-black uppercase tracking-[.12em] text-white" style={{ background: STATUS_DOT[selectedApp.status] ?? '#C9A27E' }}>{selectedApp.status.replace(/_/g, ' ')}</span>
                      <StatusActions application={selectedApp} onRetry={retryApplication} onStatus={changeStatus} size="xs" />
                    </div>
                  </div>
                  <p className="mt-3 rounded-xl bg-white px-3 py-2 text-sm font-bold text-[#875D42]">{stageLabel(selectedApp)}</p>
                </div>

                {selectedApp.pending_question && <AgentQuestionCard token={token} application={selectedApp} onUpdated={upsertApplication} />}

                {isReview && (
                  <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[#E9A38E] bg-[#FFF0EB] p-3">
                    <p className="flex items-center gap-2 text-sm font-bold text-[#AD3212]"><AlertTriangle size={16} /> Open in the {provider} window — answer the missing fields in “Application form” below and submit from here.</p>
                    {formUrl && <a href={formUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 rounded-lg bg-[#AD3212] px-3 py-1.5 text-xs font-bold text-white hover:bg-[#8B280F]"><Eye size={14} /> Open form URL</a>}
                  </div>
                )}

                <div className="grid gap-4 xl:grid-cols-2">
                  <Tile title="Job details" icon={<BriefcaseBusiness size={16} />} mode={tileMode('details')} onMode={setTile('details')}>
                    <dl className={`grid gap-4 ${tileMode('details') === 'expanded' ? 'sm:grid-cols-4' : 'sm:grid-cols-2'}`}>
                      <Detail label="Company">{job?.company ?? '—'}</Detail>
                      <Detail label="Location">{job?.location ?? '—'}</Detail>
                      <Detail label="Job board">{job?.source ?? '—'}</Detail>
                      <Detail label="Match">{job?.match_score != null ? `${job.match_score.toFixed(0)}% · ${job.ai_status.toLowerCase()}` : '—'}</Detail>
                      <Detail label="Resume used">{selectedApp.resume_agent?.chosen_label ?? selectedApp.resume_role ?? '—'}</Detail>
                      <Detail label="Queue">#{selectedApp.queue_position}</Detail>
                      <Detail label="Queued">{fmtDate(selectedApp.created_at)}</Detail>
                      <Detail label="Started">{fmtDate(selectedApp.started_at)}</Detail>
                      {selectedApp.applied_at && <Detail label="Applied">{fmtDate(selectedApp.applied_at)}</Detail>}
                      <Detail label="AI usage">{formatTokens(selectedApp.llm_total_tokens)} tokens · {selectedApp.llm_calls ?? 0} calls</Detail>
                    </dl>
                    <div className="mt-4 flex flex-wrap gap-2">
                      {job?.external_url && <a href={job.external_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded-full bg-[#FFE4C8] px-3 py-1.5 text-xs font-bold text-[#82512D] hover:bg-[#FFD7AE]">Job posting <ExternalLink size={12} /></a>}
                      {formUrl && <a href={formUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded-full bg-[#FFE4C8] px-3 py-1.5 text-xs font-bold text-[#82512D] hover:bg-[#FFD7AE]">Application form <ExternalLink size={12} /></a>}
                    </div>
                    {selectedApp.error && <p className="mt-3 rounded-xl bg-[#FFE1D6] px-3 py-2 text-xs font-bold text-[#AD3212]">{selectedApp.error}</p>}
                  </Tile>

                  <Tile title="Agent log" icon={<Brain size={16} />} mode={tileMode('log')} onMode={setTile('log')}>
                    <AgentLogPanel events={events} live={isRunning} application={selectedApp} height={tileMode('log') === 'expanded' ? 640 : 300} />
                  </Tile>

                  <Tile title="Live application flow" icon={<WorkflowIcon size={16} />} mode={tileMode('flow')} onMode={setTile('flow')}>
                    <div style={{ height: tileMode('flow') === 'expanded' ? 720 : 340 }} className="-m-4 overflow-hidden bg-[radial-gradient(circle_at_1px_1px,rgba(245,106,34,0.10)_1px,transparent_0)] bg-[length:24px_24px]">
                      <ReactFlow
                        key={`${selectedApp.id}-${nodes.length}-${tileMode('flow')}`}
                        nodes={nodes} edges={flowEdges} fitView
                        fitViewOptions={{ padding: 0.15, minZoom: 0.3, maxZoom: 1.15 }}
                        nodesDraggable={false} nodesConnectable={false} elementsSelectable={false} panOnDrag
                        defaultEdgeOptions={{ type: 'smoothstep' }} proOptions={{ hideAttribution: true }}
                      >
                        <Background color="#E6B98B" gap={18} size={1} />
                        <Controls showInteractive={false} />
                      </ReactFlow>
                    </div>
                  </Tile>

                  <Tile title="Resume" icon={<FileText size={16} />} mode={tileMode('resume')} onMode={setTile('resume')}>
                    <ResumePanel token={token} application={selectedApp} onUpdated={upsertApplication} height={tileMode('resume') === 'expanded' ? 820 : 420} />
                  </Tile>

                  <Tile title="Live browser" icon={<MonitorPlay size={16} />} mode={tileMode('browser')} onMode={setTile('browser')}>
                    {watchId ? (
                      <LiveBrowserPanel embedded token={token} applicationId={selectedApp.id} frame={browserFrame} provider={provider} />
                    ) : (
                      <p className="grid min-h-[200px] place-items-center rounded-2xl bg-[#2B160B] p-6 text-center text-sm font-bold text-[#F4D9BE]">The live view shows while a job is running or waiting for you.</p>
                    )}
                  </Tile>
                </div>

                {['HUMAN_REVIEW_REQUIRED', 'SUBMITTED', 'COMPLETED', 'REJECTED', 'FAILED'].includes(selectedApp.status) && (
                  <ApplicationFormPanel token={token} application={selectedApp} onUpdated={upsertApplication} />
                )}
                {isReview && (selectedApp.form_data as { session_open?: boolean } | undefined)?.session_open !== false && (
                  <InstructAgent token={token} application={selectedApp} onUpdated={upsertApplication} />
                )}
              </>
            )}
          </section>
        </div>
      </main>
    </>
  );
}
