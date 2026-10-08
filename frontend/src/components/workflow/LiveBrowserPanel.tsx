import { useRef, useState, type KeyboardEvent, type MouseEvent, type WheelEvent } from 'react';
import { ArrowLeft, Keyboard, Maximize2, Minimize2, MonitorPlay, MousePointerClick, RotateCw, ScreenShare } from 'lucide-react';
import { api, type BrowserFrame, type BrowserInputEvent } from '../../lib/api';

const SPECIAL_KEYS = new Set(['Enter', 'Tab', 'Backspace', 'Delete', 'Escape', 'ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'PageUp', 'PageDown']);

/**
 * Live view of the exact browser tab the agent is driving (LinkedIn / Indeed block real iframes,
 * so the backend streams screenshots and forwards your clicks, typing and scrolling).
 */
export function LiveBrowserPanel({ token, applicationId, frame, provider }: { token: string; applicationId: number; frame: BrowserFrame | null; provider: string }) {
  const [interactive, setInteractive] = useState(true);
  const [expanded, setExpanded] = useState(false);
  const [error, setError] = useState('');
  const [typing, setTyping] = useState('');
  const box = useRef<HTMLDivElement>(null);
  const scrollTimer = useRef<number | null>(null);
  const scrollAcc = useRef(0);

  async function send(event: BrowserInputEvent) {
    setError('');
    try { await api.browserInput(token, applicationId, event); }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not reach the browser tab.'); }
  }

  function onClick(event: MouseEvent<HTMLImageElement>) {
    if (!interactive) return;
    const rect = event.currentTarget.getBoundingClientRect();
    void send({ kind: 'click', x: (event.clientX - rect.left) / rect.width, y: (event.clientY - rect.top) / rect.height });
    box.current?.focus();
  }

  function onWheel(event: WheelEvent<HTMLDivElement>) {
    if (!interactive) return;
    scrollAcc.current += event.deltaY;
    if (scrollTimer.current) window.clearTimeout(scrollTimer.current);
    scrollTimer.current = window.setTimeout(() => { const dy = scrollAcc.current; scrollAcc.current = 0; void send({ kind: 'scroll', dy }); }, 120);
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (!interactive || event.metaKey || event.ctrlKey) return;
    if (SPECIAL_KEYS.has(event.key)) { event.preventDefault(); void send({ kind: 'key', key: event.key }); return; }
    if (event.key.length === 1) { event.preventDefault(); void send({ kind: 'type', text: event.key }); }
  }

  const image = frame?.application_id === applicationId ? frame.image : null;
  return (
    <div className={`rounded-[2rem] border border-[#EBCDAA] bg-[#FFF9F0] p-4 ${expanded ? 'fixed inset-3 z-50 flex flex-col shadow-2xl' : ''}`}>
      <div className="flex flex-wrap items-center gap-2 px-1">
        <h3 className="flex items-center gap-2 text-lg font-black text-ink"><MonitorPlay size={18} className="text-orange" /> Live browser</h3>
        <span className="rounded-full bg-[#FFE4C8] px-2.5 py-0.5 text-[11px] font-black text-[#82512D]">{provider} window</span>
        <span className="min-w-0 flex-1 truncate font-mono text-[11px] text-[#805940]">{frame?.application_id === applicationId ? frame.url : ''}</span>
        <button type="button" title="Back" onClick={() => send({ kind: 'back' })} className="rounded-full p-1.5 text-[#82512D] hover:bg-[#FFE4C8]"><ArrowLeft size={15} /></button>
        <button type="button" title="Reload" onClick={() => send({ kind: 'reload' })} className="rounded-full p-1.5 text-[#82512D] hover:bg-[#FFE4C8]"><RotateCw size={15} /></button>
        <button type="button" title="Bring this tab to the front of the real browser window (Chrome only streams the visible tab)" onClick={() => send({ kind: 'front' })} className="inline-flex items-center gap-1 rounded-full bg-[#FFE4C8] px-2.5 py-1 text-[11px] font-bold text-[#82512D] hover:bg-[#FFD7AE]"><ScreenShare size={13} /> Show this tab</button>
        <button type="button" onClick={() => setInteractive(v => !v)} className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-bold ${interactive ? 'bg-orange text-white' : 'bg-[#EFE7E1] text-[#6B4329]'}`}>
          <MousePointerClick size={12} /> {interactive ? 'Control on' : 'View only'}
        </button>
        <button type="button" title={expanded ? 'Shrink' : 'Enlarge'} onClick={() => setExpanded(v => !v)} className="rounded-full p-1.5 text-[#82512D] hover:bg-[#FFE4C8]">{expanded ? <Minimize2 size={15} /> : <Maximize2 size={15} />}</button>
      </div>
      <div ref={box} tabIndex={0} onKeyDown={onKeyDown} onWheel={onWheel}
        className={`mt-3 overflow-hidden rounded-2xl border border-[#E8C9A4] bg-[#2B160B] outline-none focus:ring-2 focus:ring-orange ${expanded ? 'flex-1' : ''}`}>
        {image ? (
          <img src={image} alt="Agent browser tab" onClick={onClick} draggable={false}
            className={`block w-full select-none ${expanded ? 'h-full object-contain' : ''} ${interactive ? 'cursor-pointer' : 'cursor-default'}`} />
        ) : (
          <div className="grid aspect-[1366/900] place-items-center p-6 text-center text-sm font-bold text-[#F4D9BE]">
            {frame?.application_id === applicationId && frame.reason ? frame.reason : 'Connecting to the browser tab…'}
          </div>
        )}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 px-1 text-[11px] text-[#805940]">
        <span className="inline-flex items-center gap-1"><Keyboard size={12} /> Click the picture to click in the tab; with it focused, typing goes to the tab.</span>
        <form className="ml-auto flex items-center gap-1" onSubmit={event => { event.preventDefault(); if (typing) { void send({ kind: 'type', text: typing }); setTyping(''); } }}>
          <input value={typing} onChange={event => setTyping(event.target.value)} placeholder="Type text into the focused field…" className="w-56 rounded-full border border-[#E7BD91] bg-white px-3 py-1 text-xs outline-none focus:border-orange" />
          <button type="submit" className="rounded-full bg-[#FFE4C8] px-2.5 py-1 font-bold text-[#82512D]">Send</button>
        </form>
      </div>
      {error && <p className="mt-2 rounded-xl bg-[#FFE1D6] px-3 py-2 text-xs font-bold text-[#AD3212]">{error}</p>}
    </div>
  );
}
