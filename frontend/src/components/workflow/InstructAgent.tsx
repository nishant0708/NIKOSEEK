import { useState } from 'react';
import { Bot, LoaderCircle, Send } from 'lucide-react';
import { api, type Application } from '../../lib/api';

const EXAMPLES = ['Click Continue', 'Choose India as the country', 'Accept the cookies banner', 'Select "No" for visa sponsorship'];

/** Free-text instructions for the agent on the open form (it never presses the final Submit for you). */
export function InstructAgent({ token, application, onUpdated }: { token: string; application: Application; onUpdated: (application: Application) => void }) {
  const [instruction, setInstruction] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function send(text: string) {
    if (!text.trim()) return;
    setBusy(true); setError('');
    try { onUpdated(await api.instructApplication(token, application.id, text.trim())); setInstruction(''); }
    catch (e) { setError(e instanceof Error ? e.message : 'The agent could not do that.'); }
    finally { setBusy(false); }
  }

  return (
    <div className="rounded-[2rem] border border-[#EBCDAA] bg-[#FFF9F0] p-5">
      <h3 className="flex items-center gap-2 text-lg font-black text-ink"><Bot size={18} className="text-orange" /> Tell the agent what to do</h3>
      <p className="mt-1 text-xs text-[#805940]">It clicks buttons and fills fields on the open tab for you. The final Submit always stays with you.</p>
      <form className="mt-3 flex gap-2" onSubmit={event => { event.preventDefault(); void send(instruction); }}>
        <input value={instruction} onChange={event => setInstruction(event.target.value)} disabled={busy} placeholder="e.g. click Continue, or pick 'Immediately' for notice period"
          className="flex-1 rounded-xl border border-[#E7BD91] bg-white px-3 py-2 text-sm outline-none focus:border-orange" />
        <button type="submit" disabled={busy || !instruction.trim()} className="inline-flex items-center gap-1 rounded-xl bg-orange px-4 py-2 text-sm font-bold text-white disabled:opacity-50">
          {busy ? <LoaderCircle size={14} className="animate-spin" /> : <Send size={14} />} Do it
        </button>
      </form>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {EXAMPLES.map(example => (
          <button key={example} type="button" disabled={busy} onClick={() => setInstruction(example)} className="rounded-full bg-[#FFE4C8] px-2.5 py-1 text-[11px] font-bold text-[#82512D] hover:bg-[#FFD7AE]">{example}</button>
        ))}
      </div>
      {error && <p className="mt-3 rounded-xl bg-[#FFE1D6] px-3 py-2 text-sm font-bold text-[#AD3212]">{error}</p>}
    </div>
  );
}
