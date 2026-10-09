import { useState } from 'react';
import { CheckCircle2, LoaderCircle, MessageCircleQuestion, Send } from 'lucide-react';
import { api, type Application } from '../../lib/api';

/** The agent is stuck and asks you: pick a suggested action, tell it what to do, or say you did it yourself. */
export function AgentQuestionCard({ token, application, onUpdated }: { token: string; application: Application; onUpdated: (application: Application) => void }) {
  const question = application.pending_question;
  const [instruction, setInstruction] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState('');
  if (!question) return null;

  async function answer(key: string, payload: { option?: number; instruction?: string; done?: boolean }) {
    if (!question) return;
    setBusy(key); setError('');
    try { onUpdated(await api.answerQuestion(token, application.id, { question_id: question.id, ...payload })); setInstruction(''); }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not send your answer.'); }
    finally { setBusy(null); }
  }

  return (
    <div className="rounded-[2rem] border-2 border-orange bg-[#FFF4E7] p-5 shadow-[0_0_0_6px_rgba(245,106,34,0.10)]">
      <p className="flex items-center gap-2 text-xs font-black uppercase tracking-[.14em] text-orange"><MessageCircleQuestion size={15} /> The agent needs you</p>
      <p className="mt-2 text-lg font-black text-ink">{question.question}</p>
      {question.reason && <p className="mt-1 text-sm text-[#805940]">Why: {question.reason}</p>}
      {question.options.length > 0 && (
        <div className="mt-4 flex flex-wrap gap-2">
          {question.options.map(option => (
            <button key={option.id} type="button" disabled={busy !== null} onClick={() => answer(`o${option.id}`, { option: option.id })}
              className="inline-flex items-center gap-1.5 rounded-full bg-orange px-4 py-2 text-sm font-bold text-white shadow-sm hover:opacity-90 disabled:opacity-60">
              {busy === `o${option.id}` && <LoaderCircle size={14} className="animate-spin" />}{option.label}
            </button>
          ))}
        </div>
      )}
      <form className="mt-4 flex gap-2" onSubmit={event => { event.preventDefault(); if (instruction.trim()) void answer('i', { instruction: instruction.trim() }); }}>
        <input value={instruction} onChange={event => setInstruction(event.target.value)} disabled={busy !== null}
          placeholder="Or tell the agent what to do, e.g. “accept cookies, then click Continue”"
          className="flex-1 rounded-xl border border-[#E7BD91] bg-white px-3 py-2 text-sm outline-none focus:border-orange" />
        <button type="submit" disabled={busy !== null || !instruction.trim()} className="inline-flex items-center gap-1 rounded-xl bg-ink px-4 py-2 text-sm font-bold text-white disabled:opacity-50">
          {busy === 'i' ? <LoaderCircle size={14} className="animate-spin" /> : <Send size={14} />} Send
        </button>
      </form>
      <button type="button" disabled={busy !== null} onClick={() => answer('d', { done: true })}
        className="mt-3 inline-flex items-center gap-1.5 text-sm font-bold text-[#347233] hover:underline disabled:opacity-60">
        {busy === 'd' ? <LoaderCircle size={14} className="animate-spin" /> : <CheckCircle2 size={14} />} I've done it in the browser — continue
      </button>
      <p className="mt-1 text-xs text-[#805940]">Tip: you can also click directly in the live browser view below; the agent continues as soon as it sees the application form.</p>
      {error && <p className="mt-3 rounded-xl bg-[#FFE1D6] px-3 py-2 text-sm font-bold text-[#AD3212]">{error}</p>}
    </div>
  );
}
