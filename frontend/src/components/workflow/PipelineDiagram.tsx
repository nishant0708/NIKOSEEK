import { useEffect, useMemo, useState } from 'react';
import {
  BaseEdge, Background, Controls, EdgeLabelRenderer, Handle, MarkerType, MiniMap, Position, ReactFlow, useNodesState,
  type Edge, type EdgeProps, type Node, type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Maximize2, RotateCcw, X } from 'lucide-react';

/* ------------------------------------------------------------------ */
/* Colours                                                             */
/* ------------------------------------------------------------------ */
type Tone = 'find' | 'agent' | 'prompt' | 'human' | 'status' | 'decision' | 'toggleOn' | 'toggleOff' | 'autoOn' | 'autoOff';
const TONES: Record<Tone, { bg: string; border: string }> = {
  find: { bg: '#FFEEE0', border: '#F4B27E' },
  agent: { bg: '#FFF9F0', border: '#E8C9A4' },
  prompt: { bg: '#F4ECFF', border: '#B98BE6' },
  human: { bg: '#E8F1FF', border: '#8DB3E8' },
  status: { bg: '#E3F7D9', border: '#9ED28F' },
  decision: { bg: '#FFF0DE', border: '#F56A22' },
  toggleOn: { bg: '#E3F7D9', border: '#347233' },
  toggleOff: { bg: '#F0EBE4', border: '#A89584' },
  autoOn: { bg: '#E3F7D9', border: '#347233' },
  autoOff: { bg: '#FFF0DE', border: '#D4A06F' },
};
const THICK = new Set<Tone>(['prompt', 'decision', 'toggleOn', 'toggleOff', 'autoOn', 'autoOff']);

// Nodes that only run while the Resume agent is ON (dimmed when it is off).
const RESUME_NODES = new Set(['categorize', 'rscore', 'rdecide', 'tailor', 'guard', 'portal']);

/* ------------------------------------------------------------------ */
/* Geometry (everything is laid out on a fixed grid)                   */
/* ------------------------------------------------------------------ */
const NW = 300;            // node width
const NH = 150;            // node height
const PITCH = 440;         // column pitch
const ROWS = [0, 340, 720, 1040, 1360, 1640]; // row tops
const COLX = (c: number) => c * PITCH;
const CY = (r: number) => ROWS[r] + NH / 2;   // vertical centre of a row
const URL_Y = -230;        // "Paste a job URL" sits above Match scoring, inside group 1

type ToggleKey = 'resume' | 'auto_submit' | 'cron';
type CardData = { title: string; detail: string; tone: Tone; icon: string; promptKey?: string; toggle?: ToggleKey; badge?: string; badgeOn?: boolean; dimmed?: boolean };

/* ------------------------------------------------------------------ */
/* Custom node: card with handles on all four sides                    */
/* ------------------------------------------------------------------ */
const HANDLE: React.CSSProperties = { width: 9, height: 9, background: '#fff', border: '2px solid #C9A27E', minWidth: 0, minHeight: 0 };

function CardNode({ data }: NodeProps) {
  const d = data as unknown as CardData;
  const t = TONES[d.tone];
  const clickable = !!(d.promptKey || d.toggle);
  return (
    <div
      style={{
        position: 'relative', width: NW, height: NH, boxSizing: 'border-box', display: 'flex', alignItems: 'center', gap: 12, padding: '0 14px',
        borderRadius: 20, background: t.bg, border: `${THICK.has(d.tone) ? 2.5 : 1.5}px ${d.dimmed ? 'dashed' : 'solid'} ${t.border}`,
        opacity: d.dimmed ? 0.5 : 1, cursor: clickable ? 'pointer' : 'grab', boxShadow: '0 3px 10px rgba(84,41,20,0.10)', overflow: 'visible',
      }}
    >
      {/* every side has a source + target handle; edges pick one by id */}
      <Handle id="tl" type="target" position={Position.Left} style={HANDLE} isConnectable={false} />
      <Handle id="sl" type="source" position={Position.Left} style={HANDLE} isConnectable={false} />
      <Handle id="tr" type="target" position={Position.Right} style={HANDLE} isConnectable={false} />
      <Handle id="sr" type="source" position={Position.Right} style={HANDLE} isConnectable={false} />
      <Handle id="tt" type="target" position={Position.Top} style={HANDLE} isConnectable={false} />
      <Handle id="st" type="source" position={Position.Top} style={HANDLE} isConnectable={false} />
      <Handle id="tb" type="target" position={Position.Bottom} style={HANDLE} isConnectable={false} />
      <Handle id="sb" type="source" position={Position.Bottom} style={HANDLE} isConnectable={false} />

      <div style={{ flex: '0 0 52px', height: 52, borderRadius: 14, background: t.border, display: 'grid', placeItems: 'center', fontSize: 26 }}>{d.icon}</div>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontWeight: 800, fontSize: 14.5, lineHeight: 1.2, color: '#3B1D0C' }}>{d.title}</div>
        <div style={{ marginTop: 4, fontWeight: 500, fontSize: 11.5, lineHeight: 1.35, color: '#7A5238' }}>{d.detail}</div>
      </div>
      {d.badge && (
        <span style={{ position: 'absolute', top: -11, right: 16, padding: '2px 10px', borderRadius: 999, fontSize: 10.5, fontWeight: 800, color: '#fff', background: d.badgeOn ? '#347233' : '#A89584', border: '2px solid #fff' }}>
          {d.badge}
        </span>
      )}
    </div>
  );
}

/* Coloured background panel that groups a phase (not draggable, no pointer events) */
type GroupData = { title: string; bg: string; border: string; color: string; w: number; h: number };
function GroupNode({ data }: NodeProps) {
  const d = data as unknown as GroupData;
  return (
    <div style={{ width: d.w, height: d.h, borderRadius: 28, background: d.bg, border: `2px solid ${d.border}`, pointerEvents: 'none', boxSizing: 'border-box' }}>
      <div style={{ padding: '14px 24px', fontWeight: 900, fontSize: 17, letterSpacing: 0.6, color: d.color }}>{d.title}</div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Custom edge: rounded orthogonal route + pill label                  */
/* ------------------------------------------------------------------ */
type P = [number, number];
type Kind = 'straight' | 'hvh' | 'vhv' | 'vh' | 'hv';
type RouteData = { kind?: Kind; midX?: number; chanY?: number; tdx?: number; label?: string; lp?: P; dashed?: boolean; dim?: boolean };

function roundedPath(pts: P[], r = 16) {
  let d = `M ${pts[0][0]} ${pts[0][1]}`;
  for (let i = 1; i < pts.length - 1; i++) {
    const [x0, y0] = pts[i - 1], [x1, y1] = pts[i], [x2, y2] = pts[i + 1];
    const d1 = Math.hypot(x1 - x0, y1 - y0), d2 = Math.hypot(x2 - x1, y2 - y1);
    if (!d1 || !d2) continue;
    const rr = Math.min(r, d1 / 2, d2 / 2);
    d += ` L ${x1 + ((x0 - x1) / d1) * rr} ${y1 + ((y0 - y1) / d1) * rr} Q ${x1} ${y1} ${x1 + ((x2 - x1) / d2) * rr} ${y1 + ((y2 - y1) / d2) * rr}`;
  }
  const last = pts[pts.length - 1];
  return d + ` L ${last[0]} ${last[1]}`;
}

function RouteEdge({ sourceX, sourceY, targetX, targetY, markerEnd, data }: EdgeProps) {
  const d = (data ?? {}) as RouteData;
  const sx = sourceX, sy = sourceY, tx = targetX + (d.tdx ?? 0), ty = targetY;
  let pts: P[];
  switch (d.kind) {
    case 'hvh': { const mx = d.midX ?? (sx + tx) / 2; pts = [[sx, sy], [mx, sy], [mx, ty], [tx, ty]]; break; }
    case 'vhv': { const cy = d.chanY ?? (sy + ty) / 2; pts = [[sx, sy], [sx, cy], [tx, cy], [tx, ty]]; break; }
    case 'vh': pts = [[sx, sy], [sx, ty], [tx, ty]]; break;
    case 'hv': pts = [[sx, sy], [tx, sy], [tx, ty]]; break;
    default: pts = [[sx, sy], [tx, ty]];
  }
  let lp = d.lp;
  if (!lp) {
    let best = -1;
    for (let i = 0; i < pts.length - 1; i++) {
      const len = Math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]);
      if (len > best) { best = len; lp = [(pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2]; }
    }
  }
  const color = d.dashed ? '#B8956F' : '#F56A22';
  return (
    <>
      <BaseEdge path={roundedPath(pts)} markerEnd={markerEnd} style={{ stroke: color, strokeWidth: 2.6, strokeDasharray: d.dashed ? '7 6' : undefined, opacity: d.dim ? 0.35 : 1 }} />
      {d.label && lp && (
        <EdgeLabelRenderer>
          <div
            className="nodrag nopan"
            style={{
              position: 'absolute', transform: `translate(-50%, -50%) translate(${lp[0]}px, ${lp[1]}px)`, pointerEvents: 'none',
              background: '#FFFFFF', border: '1.5px solid #E8C9A4', borderRadius: 999, padding: '2px 10px', fontSize: 11.5, fontWeight: 800,
              color: '#8B4E23', whiteSpace: 'nowrap', opacity: d.dim ? 0.5 : 1,
            }}
          >
            {d.label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

const nodeTypes = { card: CardNode, sticky: GroupNode };
const edgeTypes = { route: RouteEdge };

/* ------------------------------------------------------------------ */
/* Nodes                                                               */
/* ------------------------------------------------------------------ */
function card(id: string, col: number, row: number, icon: string, title: string, detail: string, tone: Tone, opts: { promptKey?: string; dimmed?: boolean; toggle?: ToggleKey; badge?: string; badgeOn?: boolean } = {}): Node {
  const data: CardData = { title, detail, tone, icon, ...opts };
  return {
    id, type: 'card', position: { x: COLX(col), y: ROWS[row] }, data: data as unknown as Record<string, unknown>,
    style: { width: NW, height: NH }, width: NW, height: NH,
  };
}

function groupNode(id: string, c0: number, c1: number, r0: number, r1: number, title: string, bg: string, border: string, color: string, top?: number): Node {
  const x = COLX(c0) - 24, y = (top ?? ROWS[r0]) - 56;
  const w = COLX(c1) + NW + 24 - x, h = ROWS[r1] + NH + 26 - y;
  const data: GroupData = { title, bg, border, color, w, h };
  return {
    id, type: 'sticky', position: { x, y }, data: data as unknown as Record<string, unknown>, style: { width: w, height: h },
    draggable: false, selectable: false, focusable: false, connectable: false, zIndex: -1,
  };
}

function buildNodes(model: string, resumeOn = false, autoSubmitOn = false, cronOn = false, cronRole = ''): Node[] {
  const off = !resumeOn;
  return [
    groupNode('g1', 0, 6, 0, 0, '1  DISCOVER, SCORE & OPEN THE JOB', '#FFF3DC', '#F1D08A', '#A8691A', URL_Y),
    groupNode('g2', 0, 6, 1, 1, '2  RESUME AGENT  (runs only while ON)', '#E6F6EA', '#9ED6AE', '#1E7A3E'),
    groupNode('g3', 0, 3, 2, 3, '3  LINKEDIN / INDEED APPLY', '#F0EAFD', '#C3AEF2', '#6D3FC9'),
    groupNode('g4', 4, 7, 2, 3, '5  FILL, REVIEW & SUBMIT', '#E7F0FD', '#A9C6F2', '#2D61B8'),
    groupNode('g5', 2, 4, 4, 5, '4  COMPANY PORTAL  (Workday, iCIMS, Taleo)', '#FFEEDD', '#F2BE92', '#B25A15'),

    // Row 0 - discover & open
    card('cron', 0, 0, '⏱️', `Ambient agent: ${cronOn ? 'ON' : 'OFF'}`,
      cronOn ? `Every scheduled interval: fetches ${cronRole || 'your role'} jobs, scores them and queues accepted matches. Click to edit.` : 'Click to configure automated search and application.',
      cronOn ? 'toggleOn' : 'toggleOff', { toggle: 'cron', badge: cronOn ? 'ON' : 'OFF', badgeOn: cronOn }),
    card('find', 1, 0, '🔎', 'Find jobs', 'JobSpy scrapes LinkedIn / Indeed with your search.', 'find'),
    card('score', 2, 0, '🧮', 'Match scoring', `${model || 'model'} + shared system prompt score each job 0–100.`, 'find'),
    { ...card('url', 2, 0, '🔗', 'Paste a job URL', 'A LinkedIn / Indeed posting you found yourself. Goes straight to the queue: no match scoring.', 'human'), position: { x: COLX(2), y: URL_Y } },
    card('pick', 3, 0, '✅', 'Queue jobs', 'Accepted matches you pick, jobs the ambient agent queues, and URLs you paste. LinkedIn / Indeed must be connected.', 'human'),
    card('open', 4, 0, '🌐', 'Open posting', 'New tab in your signed-in LinkedIn or Indeed window.', 'agent'),
    card('login', 5, 0, '🔐', 'Login check', 'Session expired? Log in in the same window.', 'agent'),
    card('log', 6, 0, '📜', 'Agent log + tokens', 'Every AI decision, action and token count is saved per attempt.', 'status'),

    // Row 1 - resume agent
    card('rtoggle', 0, 1, '⚡', `Resume agent: ${resumeOn ? 'ON' : 'OFF'}`,
      resumeOn ? 'Click to turn off. Uses extra tokens per job.' : 'Click to turn on: picks / tailors the best resume per job.',
      resumeOn ? 'toggleOn' : 'toggleOff', { toggle: 'resume', badge: resumeOn ? 'ON' : 'OFF', badgeOn: resumeOn }),
    card('categorize', 1, 1, '🗂️', 'Job category (scorer 2)', 'Files each job under a resume folder: frontend / backend / full stack …', 'find', { dimmed: off }),
    card('rscore', 2, 1, '🧠', 'JD ↔ resume scorer (scorer 3)', 'Scores your resumes in the category against the real job description.', 'prompt', { promptKey: 'resume_scoring', dimmed: off }),
    card('rdecide', 3, 1, '◆', 'Best score?', '≥ 75: reuse it · 55–74: tailor · below 55: best existing.', 'decision', { dimmed: off }),
    card('tailor', 4, 1, '🧠', 'Tailor resume', 'Swaps in matching projects and JD keywords you really have. Re-scored; best wins.', 'prompt', { promptKey: 'resume_tailoring', dimmed: off }),
    card('guard', 5, 1, '🛡️', 'Truth guard', 'Strips any skill, project, employer or number not in your profile.', 'agent', { dimmed: off }),
    card('portal', 6, 1, '📎', 'Upload memory', 'Already on LinkedIn / Indeed? Selected there, not re-uploaded.', 'agent', { dimmed: off }),

    // Rows 2-3 - LinkedIn / Indeed apply
    card('check1', 0, 2, '🧠', 'Correct-page check', 'AI confirms the tab is THIS job (wrong-tab guard).', 'prompt', { promptKey: 'page_verification' }),
    card('easy', 1, 2, '◆', 'Easy Apply / Indeed Apply?', 'Built-in apply flow on LinkedIn or Indeed?', 'decision'),
    card('ea', 2, 2, '📝', 'Easy Apply / Indeed Apply', 'Fills each step, clicks Next while nothing required is missing.', 'agent'),
    card('nav', 2, 3, '🧠', 'Job navigation', 'AI picks Apply → company site → form. Stuck? It asks you.', 'prompt', { promptKey: 'job_navigation' }),
    card('check2', 3, 3, '🧠', 'Correct-page check', 'Form tab verified before anything is typed.', 'prompt', { promptKey: 'page_verification' }),

    // Rows 2-3 - fill, review & submit
    card('fill', 4, 2, '🧠', 'Form filling', 'Maps profile.md + details.md onto empty fields; unknown → ASK_USER. Attaches resume.', 'prompt', { promptKey: 'form_mapping' }),
    card('answers', 5, 2, '🙋', 'Your answers', 'Fill missing fields & dropdowns, instruct the agent, or click in the live browser view.', 'human'),
    card('submit', 6, 2, '📨', 'Submit (you press it)', 'Manual submission when automatic submission is off.', 'human'),
    card('statuses', 7, 2, '🏁', 'Statuses', 'Submitted · Completed · Rejected · Failed (manual moves from the queue).', 'status'),
    card('structure', 4, 3, '🧠', 'Profile → structured data', 'Name parts, address, phone code and dated jobs / degrees for long forms.', 'prompt', { promptKey: 'profile_structuring' }),
    card('assist', 5, 3, '🧠', 'Browser assistant', 'Turns your typed instructions into clicks / field fills.', 'prompt', { promptKey: 'browser_assistant' }),
    card('autosubmit', 6, 3, '⚡', `Auto submit: ${autoSubmitOn ? 'ON' : 'OFF'}`,
      autoSubmitOn ? 'Submits complete applications; pauses when required answers are missing.' : 'Click to enable. Complete applications submit without a review step.',
      autoSubmitOn ? 'autoOn' : 'autoOff', { toggle: 'auto_submit', badge: autoSubmitOn ? 'ON' : 'OFF', badgeOn: autoSubmitOn }),

    // Rows 4-5 - company portal
    card('start', 2, 4, '🚪', 'Workday start screen', 'Existing account: reuse last application. New company: autofill with resume. Else apply manually.', 'agent'),
    card('account', 3, 4, '🔑', 'Sign up / sign in', 'Uses your job-portal login. First time creates the account, next time signs in.', 'agent'),
    card('pages', 4, 4, '📄', 'Page by page', 'Uploads resume, fills each page, Save and Continue. Stops at Review.', 'agent'),
    card('captcha', 3, 5, '🧩', 'CAPTCHA / email check', 'You solve it in the live view (or click the email link); the agent continues by itself.', 'human'),
  ];
}

/* ------------------------------------------------------------------ */
/* Edges                                                               */
/* ------------------------------------------------------------------ */
type Side = 'l' | 'r' | 't' | 'b';
function E(source: string, target: string, sh: Side, th: Side, kind: Kind, o: Omit<RouteData, 'kind'> = {}): Edge {
  return {
    id: `${source}-${target}`, source, target, sourceHandle: `s${sh}`, targetHandle: `t${th}`, type: 'route',
    data: { kind, ...o } as unknown as Record<string, unknown>, selectable: false, focusable: false,
  };
}

// Horizontal "channel" lines that run between rows (kept clear of every node)
const CH = { login: 205, score: 240, choose: 268, rescore: 540, portal: 600 };

const BASE_EDGES: Edge[] = [
  // discover & open
  E('cron', 'find', 'r', 'l', 'straight', { label: 'scheduled', dashed: true }),
  E('find', 'score', 'r', 'l', 'straight'),
  E('score', 'pick', 'r', 'l', 'straight'),
  E('url', 'pick', 'r', 't', 'hv', { label: 'skips scoring', lp: [COLX(3) - 20, URL_Y + NH / 2] }),
  E('pick', 'open', 'r', 'l', 'straight'),
  E('open', 'login', 'r', 'l', 'straight'),
  E('open', 'log', 't', 't', 'vhv', { chanY: ROWS[0] - 34, dashed: true }),
  E('login', 'rtoggle', 'b', 't', 'vhv', { chanY: CH.login, label: 'next' }),
  E('score', 'categorize', 'b', 't', 'vhv', { chanY: CH.score, label: 'if resume ON', dashed: true }),

  // resume agent
  E('rtoggle', 'categorize', 'r', 'l', 'straight', { label: 'ON' }),
  E('categorize', 'rscore', 'r', 'l', 'straight'),
  E('rscore', 'rdecide', 'r', 'l', 'straight'),
  E('rdecide', 'tailor', 'r', 'l', 'straight', { label: '55–74' }),
  E('tailor', 'guard', 'r', 'l', 'straight'),
  E('guard', 'rscore', 'b', 'b', 'vhv', { chanY: CH.rescore, label: 're-score', dashed: true }),
  E('rdecide', 'portal', 't', 't', 'vhv', { chanY: CH.choose, label: 'chosen' }),
  E('portal', 'check1', 'b', 't', 'vhv', { chanY: CH.portal, tdx: 60, label: 'resume ready', dashed: true }),
  E('rtoggle', 'check1', 'b', 't', 'straight', { label: 'OFF', dashed: true, lp: [COLX(0) + NW / 2, 665] }),

  // LinkedIn / Indeed apply
  E('check1', 'easy', 'r', 'l', 'straight'),
  E('easy', 'ea', 'r', 'l', 'straight', { label: 'yes' }),
  E('easy', 'nav', 'b', 'l', 'vh', { label: 'no', lp: [COLX(1) + NW / 2, (ROWS[2] + NH + CY(3)) / 2] }),
  E('nav', 'check2', 'r', 'l', 'straight'),
  E('check2', 'fill', 'r', 'l', 'hvh'),
  E('ea', 'fill', 'r', 'l', 'straight', { label: 'each step', lp: [COLX(2) + NW + 110, CY(2)] }),

  // company portal
  E('nav', 'start', 'b', 't', 'straight', { label: 'company portal', lp: [COLX(2) + NW / 2, (ROWS[3] + NH + ROWS[4] - 56 + 26 + ROWS[3] + NH) / 2 + 22] }),
  E('start', 'account', 'r', 'l', 'straight'),
  E('account', 'pages', 'r', 'l', 'straight'),
  E('account', 'captcha', 'b', 't', 'straight', { label: 'if shown', dashed: true }),
  E('structure', 'pages', 'b', 't', 'straight', { dashed: true }),
  E('structure', 'fill', 't', 'b', 'straight', { dashed: true }),
  E('pages', 'answers', 'r', 'l', 'hvh', { label: 'missing answers / Review', lp: [COLX(4) + NW + (PITCH - NW) / 2, (ROWS[3] + NH + ROWS[4]) / 2] }),

  // fill, review & submit
  E('fill', 'answers', 'r', 'l', 'straight'),
  E('answers', 'submit', 'r', 'l', 'straight', { label: 'manual', lp: [COLX(5) + NW + (PITCH - NW) / 2 + 12, CY(2) - 18] }),
  E('answers', 'autosubmit', 'r', 'l', 'hvh', { midX: COLX(5) + NW + 30, label: 'when enabled', lp: [COLX(5) + NW + 30, (CY(2) + CY(3)) / 2] }),
  E('answers', 'assist', 'b', 't', 'straight', { label: 'instruct', dashed: true }),
  E('submit', 'statuses', 'r', 'l', 'straight'),
  E('autosubmit', 'statuses', 'r', 'b', 'hv'),
];

function buildEdges(resumeOn: boolean): Edge[] {
  return BASE_EDGES.map(e => {
    const offPath = e.id === 'rtoggle-check1';
    const dim = offPath ? resumeOn : !resumeOn && (RESUME_NODES.has(e.source) || RESUME_NODES.has(e.target));
    const dashed = !!(e.data as RouteData).dashed;
    const color = dashed ? '#B8956F' : '#F56A22';
    return {
      ...e,
      data: { ...(e.data as object), dim } as unknown as Record<string, unknown>,
      markerEnd: { type: MarkerType.ArrowClosed, color, width: 18, height: 18 },
    };
  });
}

/* ------------------------------------------------------------------ */
/* Canvas                                                              */
/* ------------------------------------------------------------------ */
type CanvasProps = { model: string; height: number | string; onPromptClick?: (key: string) => void; fullscreen?: boolean; resumeOn?: boolean; autoSubmitOn?: boolean; cronOn?: boolean; cronRole?: string; onToggleResume?: () => void; onToggleAutoSubmit?: () => void; onEditCron?: () => void };

function Canvas({ model, height, onPromptClick, fullscreen, resumeOn = false, autoSubmitOn = false, cronOn = false, cronRole = '', onToggleResume, onToggleAutoSubmit, onEditCron }: CanvasProps) {
  const [nodes, setNodes, onNodesChange] = useNodesState(buildNodes(model, resumeOn, autoSubmitOn, cronOn, cronRole));
  const edges = useMemo(() => buildEdges(resumeOn), [resumeOn]);

  // Keep dragged positions; only refresh the text / look of cards.
  useEffect(() => {
    const fresh = new Map(buildNodes(model, resumeOn, autoSubmitOn, cronOn, cronRole).map(n => [n.id, n]));
    setNodes(current => current.map(n => {
      const f = fresh.get(n.id);
      return f && n.type === 'card' ? { ...n, data: f.data } : n;
    }));
  }, [model, resumeOn, autoSubmitOn, cronOn, cronRole, setNodes]);

  return (
    <div style={{ height }} className="relative bg-[#FFFCF7]">
      <ReactFlow
        nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes} onNodesChange={onNodesChange}
        fitView fitViewOptions={{ padding: 0.06 }} nodesConnectable={false} edgesFocusable={false} proOptions={{ hideAttribution: true }}
        minZoom={0.12} maxZoom={1.6}
        onNodeClick={(_, n) => {
          const data = n.data as { promptKey?: string; toggle?: ToggleKey };
          if (data.toggle === 'resume') { onToggleResume?.(); return; }
          if (data.toggle === 'auto_submit') { onToggleAutoSubmit?.(); return; }
          if (data.toggle === 'cron') { onEditCron?.(); return; }
          if (data.promptKey && onPromptClick) onPromptClick(data.promptKey);
        }}
      >
        <Background color="#E6B98B" gap={22} size={1.2} />
        <Controls showInteractive={false} />
        {fullscreen && <MiniMap pannable zoomable nodeColor={n => (n.type === 'sticky' ? 'transparent' : TONES[((n.data as unknown as CardData)?.tone) ?? 'agent']?.border ?? '#E8C9A4')} />}
      </ReactFlow>
      <button type="button" onClick={() => setNodes(buildNodes(model, resumeOn, autoSubmitOn, cronOn, cronRole))} className="absolute right-3 top-3 z-10 inline-flex items-center gap-1 rounded-full bg-white/90 px-3 py-1.5 text-xs font-bold text-[#82512D] shadow-sm hover:bg-white">
        <RotateCcw size={13} /> Reset layout
      </button>
    </div>
  );
}

/** Full job-search + apply pipeline. Nodes are draggable; purple nodes open their prompt editor. */
export function PipelineDiagram({ model, onPromptClick, resumeOn = false, autoSubmitOn = false, cronOn = false, cronRole = '', onToggleResume, onToggleAutoSubmit, onEditCron }: { model: string; onPromptClick?: (key: string) => void; resumeOn?: boolean; autoSubmitOn?: boolean; cronOn?: boolean; cronRole?: string; onToggleResume?: () => void; onToggleAutoSubmit?: () => void; onEditCron?: () => void }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  const legend = (
    <div className="flex flex-wrap items-center gap-2 text-[11px] font-bold">
      {([['find', 'Job search'], ['agent', 'Apply agent'], ['prompt', 'Editable prompt (click)'], ['human', 'You'], ['status', 'Result / log'], [resumeOn ? 'toggleOn' : 'toggleOff', 'Toggle (click)'], [autoSubmitOn ? 'autoOn' : 'autoOff', 'Auto submit']] as [Tone, string][]).map(([tone, label]) => (
        <span key={tone + label} className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1" style={{ background: TONES[tone].bg, border: `1px solid ${TONES[tone].border}`, color: '#542914' }}>{label}</span>
      ))}
      <span className="inline-flex items-center gap-1.5 text-[#805940]"><span style={{ width: 22, borderTop: '2.5px solid #F56A22' }} /> main flow</span>
      <span className="inline-flex items-center gap-1.5 text-[#805940]"><span style={{ width: 22, borderTop: '2.5px dashed #B8956F' }} /> optional / side path</span>
    </div>
  );

  return (
    <>
      <div className="mt-7 overflow-hidden rounded-[2rem] border border-[#EBCDAA] bg-[#FFF9F0]">
        <div className="flex flex-wrap items-center gap-3 border-b border-[#EBCDAA] px-5 py-3">
          <p className="font-black text-ink">Workflow</p>
          {legend}
          <button type="button" onClick={() => setOpen(true)} className="ml-auto inline-flex items-center gap-1.5 rounded-full bg-orange px-4 py-1.5 text-xs font-bold text-white hover:opacity-90">
            <Maximize2 size={13} /> See full workflow
          </button>
        </div>
        <Canvas model={model} height={760} onPromptClick={onPromptClick} resumeOn={resumeOn} autoSubmitOn={autoSubmitOn} cronOn={cronOn} cronRole={cronRole} onToggleResume={onToggleResume} onToggleAutoSubmit={onToggleAutoSubmit} onEditCron={onEditCron} />
      </div>

      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#2B160B]/60 p-4 backdrop-blur-sm" onClick={() => setOpen(false)}>
          <div className="flex h-[92vh] w-[96vw] flex-col overflow-hidden rounded-[2rem] border border-[#EBCDAA] bg-[#FFF9F0] shadow-2xl" onClick={event => event.stopPropagation()}>
            <div className="flex flex-wrap items-center gap-3 border-b border-[#EBCDAA] px-6 py-4">
              <p className="text-lg font-black text-ink">Full workflow</p>
              {legend}
              <span className="text-xs text-[#805940]">Drag nodes · scroll to zoom · click a purple node to edit its prompt</span>
              <button type="button" onClick={() => setOpen(false)} className="ml-auto rounded-full p-2 text-[#82512D] hover:bg-[#FFE4C8]" aria-label="Close"><X size={18} /></button>
            </div>
            <div className="flex-1">
              <Canvas model={model} height="100%" fullscreen resumeOn={resumeOn} autoSubmitOn={autoSubmitOn} cronOn={cronOn} cronRole={cronRole} onToggleResume={onToggleResume} onToggleAutoSubmit={onToggleAutoSubmit} onEditCron={() => { setOpen(false); onEditCron?.(); }} onPromptClick={key => { setOpen(false); onPromptClick?.(key); }} />
            </div>
          </div>
        </div>
      )}
    </>
  );
}