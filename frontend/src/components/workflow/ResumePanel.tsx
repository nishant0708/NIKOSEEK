import { useEffect, useMemo, useState } from 'react';
import { Download, FileText, LoaderCircle, Pin, Repeat2, Sparkles } from 'lucide-react';
import { Button } from '../ui/button';
import { api, type Application, type ResumeAgentStatus, type ResumeInfo, type ResumeSwitchResult } from '../../lib/api';

const STATUS_TEXT: Record<ResumeAgentStatus, { label: string; tone: string }> = {
  reused: { label: 'Existing resume scored high enough', tone: 'bg-[#E3F7D9] text-[#347233]' },
  generated: { label: 'Tailored a new resume', tone: 'bg-[#F4ECFF] text-[#7A3FB8]' },
  low_fit: { label: 'Weak fit — used the best existing', tone: 'bg-[#FFE1D6] text-[#AD3212]' },
  tailor_no_gain: { label: 'Tailoring did not beat the original', tone: 'bg-[#FFF0DE] text-[#92502A]' },
};

const SWITCH_TEXT: Record<ResumeSwitchResult, string> = {
  attached: 'Switched and attached to the open form.',
  not_on_this_step: 'Switched. This step has no resume field — ask the agent to go back to the resume step, or press Retry.',
  queued: 'Switched. It will be used when the form opens.',
  retry: 'Switched. Press Retry to apply with it.',
};

const CLOSED = ['SUBMITTED', 'COMPLETED', 'REJECTED'];

/** Which resume this application uses: a live preview, the resume agent's scores, and a switcher. */
export function ResumePanel({ token, application, onUpdated, height = 420 }: {
  token: string; application: Application; onUpdated: (app: Application) => void; height?: number;
}) {
  const [resumes, setResumes] = useState<ResumeInfo[]>([]);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState('');
  const [choice, setChoice] = useState(application.resume_key);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const decision = application.resume_agent ?? {};
  const currentKey = application.resume_key;
  const attachedName = typeof application.form_data?.resume_uploaded === 'string' ? application.form_data.resume_uploaded : null;

  // Re-list when the chosen resume changes (the agent may just have generated one).
  useEffect(() => {
    api.resumes(token).then(setResumes).catch(() => setResumes([]));
  }, [token, currentKey]);
  useEffect(() => { setChoice(currentKey); setMessage(null); }, [currentKey, application.id]);

  useEffect(() => {
    if (!currentKey) return;
    let url: string | null = null;
    let cancelled = false;
    setPreviewUrl(null); setPreviewError('');
    api.resumeFile(token, currentKey)
      .then(blob => { if (cancelled) return; url = URL.createObjectURL(blob); setPreviewUrl(url); })
      .catch(e => !cancelled && setPreviewError(e instanceof Error ? e.message : 'Could not load the resume.'));
    return () => { cancelled = true; if (url) URL.revokeObjectURL(url); };
  }, [token, currentKey]);

  const current = resumes.find(r => r.storage_key === currentKey);
  const scoreOf = useMemo(() => {
    const scores: Record<string, number> = Object.fromEntries((decision.candidates ?? []).map(c => [c.resume_key, c.score]));
    if (decision.chosen_key && decision.chosen_score != null) scores[decision.chosen_key] = decision.chosen_score;
    return scores;
  }, [decision.candidates, decision.chosen_key, decision.chosen_score]);
  const byCategory = useMemo(() => {
    const groups: Record<string, ResumeInfo[]> = {};
    resumes.forEach(r => { (groups[r.category] ??= []).push(r); });
    return groups;
  }, [resumes]);
  const isPdf = currentKey?.toLowerCase().endsWith('.pdf');
  const closed = CLOSED.includes(application.status);

  async function switchResume() {
    if (!choice || choice === currentKey) return;
    setBusy(true); setMessage(null);
    try {
      const { application: updated, result } = await api.switchResume(token, application.id, choice);
      onUpdated(updated);
      setMessage({ kind: result === 'attached' || result === 'queued' ? 'ok' : 'error', text: SWITCH_TEXT[result] });
    } catch (e) {
      setMessage({ kind: 'error', text: e instanceof Error ? e.message : 'Could not switch the resume.' });
    } finally { setBusy(false); }
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <FileText size={16} className="text-orange" />
        <span className="font-black text-ink">{current?.label ?? decision.chosen_label ?? application.resume_role ?? 'Resume'}</span>
        {current?.origin === 'generated' && <span className="inline-flex items-center gap-1 rounded-full bg-[#F4ECFF] px-2 py-0.5 text-[10px] font-black text-[#7A3FB8]"><Sparkles size={11} />Generated</span>}
        {attachedName && <span className="rounded-full bg-[#E3F7D9] px-2 py-1 text-[10px] font-bold text-[#347233]">Used: {attachedName}</span>}
        {scoreOf[currentKey] != null && <span className="rounded-full bg-[#FFE4C8] px-2 py-0.5 text-[11px] font-black text-[#82512D]">{Number(scoreOf[currentKey]).toFixed(0)}% JD fit</span>}
        {decision.pinned && <span className="inline-flex items-center gap-1 rounded-full bg-[#E8F1FF] px-2 py-0.5 text-[10px] font-black text-[#2E6BB8]"><Pin size={11} />Picked by you</span>}
        {decision.status && !decision.pinned && <span className={`rounded-full px-2 py-0.5 text-[10px] font-black ${STATUS_TEXT[decision.status].tone}`}>{STATUS_TEXT[decision.status].label}</span>}
        {previewUrl && <a href={previewUrl} download={current?.upload_name ?? 'resume'} className="ml-auto inline-flex items-center gap-1 rounded-full bg-[#FFE4C8] px-3 py-1 text-xs font-bold text-[#82512D] hover:bg-[#FFD7AE]"><Download size={12} /> Download</a>}
      </div>

      {current && (current.sources?.length ?? 0) > 0 && (
        <div className="rounded-xl border border-[#EBCDAA] bg-white p-3">
          <p className="text-[10px] font-black uppercase tracking-[.12em] text-[#A16A45]">Saved on job boards</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {current.sources?.map(item => (
              <span key={item.source} className="rounded-full bg-[#FFF0DE] px-2.5 py-1 text-xs font-bold text-[#82512D]">
                {item.source === 'linkedin' ? 'LinkedIn' : 'Indeed'} · {item.display_name}
              </span>
            ))}
          </div>
          <p className="mt-2 text-[11px] text-[#805940]">Board-specific names are tracked separately, so Indeed and LinkedIn can show different filenames for this resume.</p>
        </div>
      )}

      {(decision.candidates?.length ?? 0) > 0 && (
        <div className="rounded-xl border border-[#EBCDAA] bg-white p-3">
          <p className="text-[10px] font-black uppercase tracking-[.12em] text-[#A16A45]">Resume agent{decision.category ? ` · category ${decision.category}` : ''}{decision.thresholds ? ` · reuse ≥ ${decision.thresholds.reuse}, tailor ≥ ${decision.thresholds.tailor_min}` : ''}</p>
          <div className="mt-2 space-y-1.5">
            {decision.candidates!.map(c => (
              <div key={c.resume_key} className="text-xs">
                <div className="flex items-center gap-2 font-bold text-[#542914]"><span className="min-w-0 flex-1 truncate">{c.label}</span><span>{c.score.toFixed(0)}</span></div>
                <div className="mt-0.5 h-1.5 rounded-full bg-[#F5E9DA]"><div className="h-1.5 rounded-full bg-orange" style={{ width: `${Math.max(2, Math.min(100, c.score))}%` }} /></div>
              </div>
            ))}
            {decision.status === 'generated' && decision.chosen_score != null && (
              <p className="pt-1 text-xs font-bold text-[#7A3FB8]">Tailored: {decision.chosen_score.toFixed(0)} (from {decision.base_score?.toFixed(0)})</p>
            )}
          </div>
          {(decision.keywords_added?.length ?? 0) > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              <span className="text-[11px] font-bold text-[#805940]">Surfaced from your profile:</span>
              {decision.keywords_added!.map(k => <span key={k} className="rounded-full bg-[#E3F7D9] px-2 py-0.5 text-[11px] font-bold text-[#347233]">{k}</span>)}
            </div>
          )}
        </div>
      )}

      <div style={{ height }} className="overflow-hidden rounded-xl border border-[#EBCDAA] bg-white">
        {!currentKey ? <p className="p-4 text-sm font-bold text-[#805940]">No resume chosen yet.</p>
          : previewError ? <p className="p-4 text-sm font-bold text-[#AD3212]">{previewError}</p>
          : !previewUrl ? <p className="flex items-center gap-2 p-4 text-sm font-bold text-[#805940]"><LoaderCircle size={14} className="animate-spin" /> Loading preview…</p>
          : isPdf ? <iframe title="Resume preview" src={previewUrl} className="h-full w-full" />
          : <p className="p-4 text-sm font-bold text-[#805940]">DOCX can’t be previewed here — use Download.</p>}
      </div>

      {!closed && resumes.length > 1 && (
        <div className="flex flex-wrap items-center gap-2">
          <select value={choice} onChange={e => setChoice(e.target.value)} className="h-10 min-w-0 flex-1 rounded-xl border border-[#E7BD91] bg-white px-3 text-sm font-bold text-ink outline-none">
            {Object.entries(byCategory).map(([category, items]) => (
              <optgroup key={category} label={category}>
                {items.map(r => <option key={r.storage_key} value={r.storage_key}>{r.label}{r.origin === 'generated' ? ' ✦' : ''}{scoreOf[r.storage_key] != null ? ` — ${Number(scoreOf[r.storage_key]).toFixed(0)}%` : ''}</option>)}
              </optgroup>
            ))}
          </select>
          <Button type="button" size="sm" onClick={switchResume} disabled={busy || !choice || choice === currentKey}>
            {busy ? <LoaderCircle size={14} className="animate-spin" /> : <Repeat2 size={14} />} Use this resume
          </Button>
        </div>
      )}
      {message && <p className={`rounded-xl px-3 py-2 text-xs font-bold ${message.kind === 'ok' ? 'bg-[#E3F7D9] text-[#347233]' : 'bg-[#FFF0DE] text-[#92502A]'}`}>{message.text}</p>}
    </div>
  );
}
