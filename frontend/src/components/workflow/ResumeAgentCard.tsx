import { useEffect, useState, type ChangeEvent } from 'react';
import { FileText, LoaderCircle, Save } from 'lucide-react';
import { Button } from '../ui/button';
import type { ResumeAgentSettings } from '../../lib/api';

/** Resume agent switch + score bands (also toggled from the ⚡ node in the workflow diagram). */
export function ResumeAgentCard({ settings, busy, onSave }: { settings: ResumeAgentSettings | null; busy: boolean; onSave: (next: ResumeAgentSettings) => void }) {
  const [draft, setDraft] = useState<ResumeAgentSettings | null>(settings);
  useEffect(() => setDraft(settings), [settings]);
  if (!draft || !settings) return null;
  const invalid = !(draft.tailor_min >= 0 && draft.tailor_min < draft.reuse_threshold && draft.reuse_threshold <= 100);
  const dirty = JSON.stringify(draft) !== JSON.stringify(settings);
  const num = (key: 'reuse_threshold' | 'tailor_min' | 'max_rounds') => (event: ChangeEvent<HTMLInputElement>) => setDraft({ ...draft, [key]: Number(event.target.value) });

  return (
    <section id="resume-agent" className="mt-7 scroll-mt-24 rounded-[2rem] border border-[#EBCDAA] bg-[#FFF9F0] p-6 sm:p-8">
      <div className="flex flex-wrap items-center gap-3">
        <FileText className="text-orange" size={20} />
        <h2 className="text-xl font-black">Resume agent</h2>
        <button type="button" role="switch" aria-checked={settings.enabled} disabled={busy} onClick={() => onSave({ ...settings, enabled: !settings.enabled })}
          className={`relative ml-auto h-7 w-14 rounded-full transition ${settings.enabled ? 'bg-[#347233]' : 'bg-[#C9B8A8]'} disabled:opacity-60`}>
          <span className={`absolute top-1 h-5 w-5 rounded-full bg-white shadow transition-all ${settings.enabled ? 'left-8' : 'left-1'}`} />
        </button>
        <span className={`text-sm font-black ${settings.enabled ? 'text-[#347233]' : 'text-[#805940]'}`}>{settings.enabled ? 'ON' : 'OFF'}</span>
      </div>
      <p className="mt-2 max-w-3xl text-sm text-[#805940]">
        Before each application it scores your resumes in the job’s category against the real job description. A high score reuses that resume.
        In the tailor band it builds a tuned resume from <code className="rounded bg-[#F5E9DA] px-1">profile.md</code> + <code className="rounded bg-[#F5E9DA] px-1">details.md</code>, and anything
        not in your profile is stripped. The new resume is saved in that category folder and uploaded to the job site, or selected there if it is already uploaded.
        Uses extra AI tokens per job, so it only runs while ON.
      </p>
      <div className="mt-5 grid gap-4 sm:grid-cols-4">
        <label className="text-sm font-bold">Reuse at ≥<input type="number" min={1} max={100} value={draft.reuse_threshold} onChange={num('reuse_threshold')} className="mt-2 h-11 w-full rounded-xl border border-[#E7BD91] bg-white px-3 text-sm font-bold outline-none" /></label>
        <label className="text-sm font-bold">Tailor from ≥<input type="number" min={0} max={99} value={draft.tailor_min} onChange={num('tailor_min')} className="mt-2 h-11 w-full rounded-xl border border-[#E7BD91] bg-white px-3 text-sm font-bold outline-none" /></label>
        <label className="text-sm font-bold">Tailoring rounds<input type="number" min={1} max={3} value={draft.max_rounds} onChange={num('max_rounds')} className="mt-2 h-11 w-full rounded-xl border border-[#E7BD91] bg-white px-3 text-sm font-bold outline-none" /></label>
        <label className="text-sm font-bold">Model<select value={draft.model} onChange={e => setDraft({ ...draft, model: e.target.value })} className="mt-2 h-11 w-full rounded-xl border border-[#E7BD91] bg-white px-3 text-sm font-bold outline-none">
          <option value="">Same as match scoring</option>
          <option value="gemini-3.5-flash-lite">Gemini 3.5 Flash Lite</option>
          <option value="gemini-3.5-flash">Gemini 3.5 Flash</option>
          <option value="gemini-3.5-pro">Gemini 3.5 Pro</option>
        </select></label>
      </div>
      <p className="mt-3 text-xs font-bold text-[#805940]">
        ≥ {draft.reuse_threshold}: use the existing resume · {draft.tailor_min}–{Math.max(draft.tailor_min, draft.reuse_threshold - 1)}: tailor a new one · below {draft.tailor_min}: weak fit, apply with the best existing one (no tailoring tokens).
      </p>
      {invalid && <p className="mt-2 text-xs font-bold text-[#AD3212]">“Tailor from” must be lower than “Reuse at”.</p>}
      <Button className="mt-4" onClick={() => onSave(draft)} disabled={busy || invalid || !dirty}>{busy ? <LoaderCircle size={16} className="animate-spin" /> : <Save size={16} />} Save resume agent settings</Button>
    </section>
  );
}
