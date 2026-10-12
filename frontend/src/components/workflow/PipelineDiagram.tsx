import { useEffect, useState } from 'react';
import { Background, Controls, MarkerType, MiniMap, ReactFlow, useEdgesState, useNodesState, type Edge, type Node } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Maximize2, RotateCcw, X } from 'lucide-react';

type Tone = 'find' | 'agent' | 'prompt' | 'human' | 'status' | 'decision' | 'toggleOn' | 'toggleOff';
const TONES: Record<Tone, { bg: string; border: string }> = {
  find: { bg: '#FFEEE0', border: '#F4B27E' },
  agent: { bg: '#FFF9F0', border: '#E8C9A4' },
  prompt: { bg: '#F4ECFF', border: '#B98BE6' },
  human: { bg: '#E8F1FF', border: '#8DB3E8' },
  status: { bg: '#E3F7D9', border: '#9ED28F' },
  decision: { bg: '#FFF0DE', border: '#F56A22' },
  toggleOn: { bg: '#E3F7D9', border: '#347233' },
  toggleOff: { bg: '#F0EBE4', border: '#A89584' },
};

// Nodes that only run while the Resume agent is ON (dimmed when it is off).
const RESUME_NODES = new Set(['categorize', 'rscore', 'rdecide', 'tailor', 'guard', 'portal']);

function node(id: string, x: number, y: number, title: string, detail: string, tone: Tone, promptKey?: string, dimmed = false): Node {
  const t = TONES[tone];
  const toggle = tone === 'toggleOn' || tone === 'toggleOff';
  return {
    id, position: { x, y },
    data: { label: `${title}\n${detail}`, promptKey, toggle },
    style: {
      width: 230, borderRadius: tone === 'decision' || toggle ? 28 : 16, border: `${tone === 'prompt' || tone === 'decision' || toggle ? 2 : 1}px ${dimmed ? 'dashed' : 'solid'} ${t.border}`,
      background: t.bg, color: '#542914', fontWeight: 700, fontSize: 12, lineHeight: 1.35, padding: '12px 14px', whiteSpace: 'pre-line' as const,
      cursor: promptKey || toggle ? 'pointer' : 'grab', opacity: dimmed ? 0.45 : 1,
    },
  };
}

function resumeNodes(on: boolean): Node[] {
  const off = !on;
  return [
    node('categorize', 300, -190, '🗂️ Job category (scorer 2)', 'Same scoring call files each job under a resume folder: frontend / backend / full stack …', 'find', undefined, off),
    node('rtoggle', 600, -190, `⚡ Resume agent: ${on ? 'ON' : 'OFF'}`, on ? 'Click to turn off. Uses extra tokens per job.' : 'Click to turn on: picks / tailors the best resume per job.', on ? 'toggleOn' : 'toggleOff'),
    node('rscore', 900, -190, '🧠 JD ↔ resume scorer (scorer 3)', 'Scores your resumes in the job category against the real JD (read from the posting if the search had none).', 'prompt', 'resume_scoring', off),
    node('rdecide', 1200, -190, '◆ Best score?', '≥ reuse (75): use it · tailor band (55–74): tailor · below: best existing.', 'decision', undefined, off),
    node('tailor', 1200, -360, '🧠 Tailor resume', 'From profile.md + details.md: swap in matching projects, surface JD keywords you really have. Re-scored; best wins.', 'prompt', 'resume_tailoring', off),
    node('guard', 1500, -360, '🛡️ Truth guard', 'Strips any skill, project, employer or number not in your profile. Saved to Resumes/<category>/.', 'agent', undefined, off),
    node('portal', 1500, -190, '📎 Upload memory', 'Already on LinkedIn / Indeed? Selected there, not re-uploaded. Switch resume anytime in Live Workflow.', 'agent', undefined, off),
  ];
}

function buildNodes(model: string, resumeOn = false): Node[] {
  return [
    ...resumeNodes(resumeOn),
    node('find', 0, 0, '🔎 Find jobs', 'JobSpy scrapes LinkedIn / Indeed with your search.', 'find'),
    node('score', 300, 0, `🧮 Match scoring`, `${model || 'model'} + shared system prompt score each job 0–100.`, 'find'),
    node('pick', 600, 0, '✅ Review & Apply selected', 'You pick jobs on the Jobs page. LinkedIn / Indeed must be connected.', 'human'),
    node('open', 600, 170, '🌐 Open posting', 'New tab in your signed-in LinkedIn or Indeed window.', 'agent'),
    node('login', 300, 170, '🔐 LinkedIn / Indeed login check', 'Session expired? Log in in the same window.', 'agent'),
    node('check1', 0, 170, '🧠 Correct-page check', 'AI confirms the tab is THIS job (wrong-tab guard).', 'prompt', 'page_verification'),
    node('easy', 0, 340, '◆ Easy Apply / Indeed Apply?', 'Built-in apply flow on LinkedIn or Indeed?', 'decision'),
    node('ea', 0, 510, '📝 Easy Apply / Indeed Apply', 'Fills each step, clicks Next / Continue while nothing required is missing.', 'agent'),
    node('nav', 300, 340, '🧠 Job navigation', 'AI picks Apply → company site → form. Stuck? It asks you (buttons or your own instruction).', 'prompt', 'job_navigation'),
    node('check2', 600, 340, '🧠 Correct-page check', 'Form tab verified before anything is typed.', 'prompt', 'page_verification'),
    node('fill', 300, 510, '🧠 Form filling', 'Maps profile.md + details.md onto empty fields; unknown → ASK_USER. Attaches resume.', 'prompt', 'form_mapping'),
    node('answers', 600, 510, '🙋 Your answers', 'Live Workflow: fill missing fields & dropdowns, instruct the agent, or click in the live browser view.', 'human'),
    node('submit', 900, 510, '📨 Submit (you press it)', 'Only enabled when every required field is filled.', 'human'),
    node('statuses', 900, 680, '🏁 Statuses', 'Submitted · Completed · Rejected · Failed (manual moves from the queue).', 'status'),
    node('assist', 300, 680, '🧠 Browser assistant', 'Turns your typed instructions into clicks / field fills.', 'prompt', 'browser_assistant'),
    node('log', 900, 170, '📜 Agent log + tokens', 'Every AI decision, action and token count is saved per attempt.', 'status'),
    // Company portals (Workday, iCIMS, Taleo …)
    node('start', 1200, 340, '🚪 Workday start screen', 'Company you have an account with → Use My Last Application; new company → Autofill with Resume; else Apply Manually.', 'agent'),
    node('account', 1500, 340, '🔑 Sign up / sign in', 'Your job-portal login (Profile page). First time: creates the account and remembers it (workday: {company: true}); next time signs in.', 'agent'),
    node('captcha', 1500, 170, '🧩 CAPTCHA / email check', 'You solve it in the live view (or click the email link); the agent notices and continues by itself.', 'human'),
    node('pages', 1500, 510, '📄 Page by page', 'Uploads the resume, adds a Work Experience / Education entry per job & degree, fills each page, presses Save and Continue — stops at Review.', 'agent'),
    node('structure', 1200, 680, '🧠 Profile → structured data', 'Turns profile.md + details.md into name parts, address, phone code and dated jobs/degrees for long forms.', 'prompt', 'profile_structuring'),
  ];
}

const edge = (source: string, target: string, label?: string, dashed = false): Edge => ({
  id: `${source}-${target}`, source, target, label, type: 'smoothstep', animated: !dashed,
  style: { stroke: dashed ? '#C9A27E' : '#F56A22', strokeWidth: 2, strokeDasharray: dashed ? '5 5' : undefined },
  labelStyle: { fontWeight: 800, fill: '#8B4E23', fontSize: 11 }, labelBgStyle: { fill: '#FFF9F0' },
  markerEnd: { type: MarkerType.ArrowClosed, color: dashed ? '#C9A27E' : '#F56A22' },
});

const RESUME_EDGES: Edge[] = [
  edge('score', 'categorize', 'if ON', true), edge('login', 'rtoggle'), edge('rtoggle', 'rscore', 'ON'), edge('rscore', 'rdecide'),
  edge('rdecide', 'tailor', '55–74'), edge('tailor', 'guard'), edge('guard', 'rscore', 're-score', true), edge('rdecide', 'portal', 'chosen'),
  edge('portal', 'check1', undefined, true), edge('rtoggle', 'check1', 'OFF', true), edge('categorize', 'rscore', undefined, true),
];

const EDGES: Edge[] = [
  edge('find', 'score'), edge('score', 'pick'), edge('pick', 'open'), edge('open', 'login'),
  edge('check1', 'easy'), edge('easy', 'ea', 'yes'), edge('easy', 'nav', 'no'), edge('nav', 'check2'), edge('check2', 'fill'),
  edge('ea', 'fill', 'each step'), edge('fill', 'answers'), edge('answers', 'submit'), edge('submit', 'statuses'),
  edge('open', 'log', undefined, true), edge('answers', 'assist', 'instruct', true),
  edge('nav', 'start', 'company portal'), edge('start', 'account'), edge('account', 'captcha', 'if shown', true), edge('account', 'pages'),
  edge('pages', 'answers', 'missing answers / Review'), edge('structure', 'fill', undefined, true), edge('structure', 'pages', undefined, true),
];

type CanvasProps = { model: string; height: number | string; onPromptClick?: (key: string) => void; fullscreen?: boolean; resumeOn?: boolean; onToggleResume?: () => void };

function Canvas({ model, height, onPromptClick, fullscreen, resumeOn = false, onToggleResume }: CanvasProps) {
  const [nodes, setNodes, onNodesChange] = useNodesState(buildNodes(model, resumeOn));
  const [edges, , onEdgesChange] = useEdgesState([...EDGES, ...RESUME_EDGES]);
  // Keep dragged positions; only refresh the text / look of nodes that depend on the model or the toggle.
  useEffect(() => {
    const fresh = new Map(buildNodes(model, resumeOn).map(n => [n.id, n]));
    setNodes(current => current.map(n => (n.id === 'score' || n.id === 'rtoggle' || RESUME_NODES.has(n.id)) && fresh.get(n.id)
      ? { ...n, data: fresh.get(n.id)!.data, style: fresh.get(n.id)!.style } : n));
  }, [model, resumeOn, setNodes]);
  return (
    <div style={{ height }} className="relative bg-[radial-gradient(circle_at_1px_1px,rgba(245,106,34,0.10)_1px,transparent_0)] bg-[length:24px_24px]">
      <ReactFlow nodes={nodes} edges={edges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} fitView fitViewOptions={{ padding: 0.12 }}
        nodesConnectable={false} proOptions={{ hideAttribution: true }} minZoom={0.3} maxZoom={1.8}
        onNodeClick={(_, n) => {
          const data = n.data as { promptKey?: string; toggle?: boolean };
          if (data.toggle) { onToggleResume?.(); return; }
          if (data.promptKey && onPromptClick) onPromptClick(data.promptKey);
        }}>
        <Background color="#E6B98B" gap={18} size={1} />
        <Controls showInteractive={false} />
        {fullscreen && <MiniMap pannable zoomable nodeColor={n => (n.style?.background as string) ?? '#FFF9F0'} />}
      </ReactFlow>
      <button type="button" onClick={() => setNodes(buildNodes(model, resumeOn))} className="absolute right-3 top-3 z-10 inline-flex items-center gap-1 rounded-full bg-white/90 px-3 py-1.5 text-xs font-bold text-[#82512D] shadow-sm hover:bg-white">
        <RotateCcw size={13} /> Reset layout
      </button>
    </div>
  );
}

/** Full job-search + apply pipeline. Nodes are draggable; purple nodes open their prompt editor. */
export function PipelineDiagram({ model, onPromptClick, resumeOn = false, onToggleResume }: { model: string; onPromptClick?: (key: string) => void; resumeOn?: boolean; onToggleResume?: () => void }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  const legend = (
    <div className="flex flex-wrap gap-2 text-[11px] font-bold">
      {([['find', 'Job search'], ['agent', 'Apply agent'], ['prompt', 'Editable prompt (click)'], ['human', 'You'], ['status', 'Result / log'], [resumeOn ? 'toggleOn' : 'toggleOff', 'Toggle (click)']] as [Tone, string][]).map(([tone, label]) => (
        <span key={tone} className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1" style={{ background: TONES[tone].bg, border: `1px solid ${TONES[tone].border}`, color: '#542914' }}>{label}</span>
      ))}
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
        <Canvas model={model} height={520} onPromptClick={onPromptClick} resumeOn={resumeOn} onToggleResume={onToggleResume} />
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
              <Canvas model={model} height="100%" fullscreen resumeOn={resumeOn} onToggleResume={onToggleResume} onPromptClick={key => { setOpen(false); onPromptClick?.(key); }} />
            </div>
          </div>
        </div>
      )}
    </>
  );
}
