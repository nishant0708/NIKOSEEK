import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, ArrowRight, Bot, CheckCircle, ChevronDown, ChevronRight, FileText, ListChecks, LoaderCircle, PencilLine, Send, User as UserIcon } from 'lucide-react';
import { Button } from '../ui/button';
import { api, type Application, type ApplicationFormData, type FormField } from '../../lib/api';

const SOURCE_BADGE: Record<string, { label: string; className: string; icon: typeof Bot }> = {
  ai: { label: 'AI', className: 'bg-[#F4ECFF] text-[#7A3FB8]', icon: Bot },
  user: { label: 'You', className: 'bg-[#E8F1FF] text-[#2E6BB8]', icon: UserIcon },
  prefilled: { label: 'Prefilled', className: 'bg-[#E3F7D9] text-[#347233]', icon: CheckCircle },
};

function FieldControl({ field, value, onChange, disabled }: { field: FormField; value: string; onChange: (value: string) => void; disabled: boolean }) {
  const base = 'w-full rounded-xl border border-[#E7BD91] bg-white px-3 py-2 text-sm text-ink outline-none focus:border-orange disabled:bg-[#FFF4E7]';
  const options = field.options ?? [];
  if ((field.kind === 'radio' || field.kind === 'checkbox') && options.length) {
    return (
      <div className="flex flex-wrap gap-2">
        {options.map(option => (
          <button key={option} type="button" disabled={disabled} onClick={() => onChange(option)}
            className={`rounded-full border px-3 py-1.5 text-xs font-bold transition ${value === option ? 'border-orange bg-orange text-white' : 'border-[#E7BD91] bg-white text-[#6B4329] hover:border-orange'}`}>
            {option}
          </button>
        ))}
      </div>
    );
  }
  if ((field.kind === 'select' || field.kind === 'combobox' || field.kind === 'listbox') && options.length) {
    return (
      <select className={base} value={value} disabled={disabled} onChange={event => onChange(event.target.value)}>
        <option value="">Select an option…</option>
        {options.map(option => <option key={option} value={option}>{option}</option>)}
      </select>
    );
  }
  if (field.kind === 'textarea') {
    return <textarea className={`${base} min-h-24`} value={value} disabled={disabled} onChange={event => onChange(event.target.value)} />;
  }
  return <input className={base} value={value} disabled={disabled} onChange={event => onChange(event.target.value)} placeholder={field.kind === 'combobox' ? 'Type to search…' : ''} />;
}

export function ApplicationFormPanel({ token, application, onUpdated }: { token: string; application: Application; onUpdated: (application: Application) => void }) {
  const form = (application.form_data ?? {}) as ApplicationFormData;
  const fields = form.fields ?? [];
  const signature = `${application.id}:${form.step ?? 1}:${fields.map(f => `${f.key}=${f.label}=${f.value}`).join('|')}`;
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [remember, setRemember] = useState(true);
  const [onlyMissing, setOnlyMissing] = useState(true);
  const [busy, setBusy] = useState<'fill' | 'continue' | 'submit' | 'recheck' | null>(null);
  const [error, setError] = useState('');
  const [confirmSubmit, setConfirmSubmit] = useState(false);

  // New step / new values from the browser -> reset the local draft to what the form now contains.
  useEffect(() => {
    setDraft(Object.fromEntries(fields.map(field => [field.key, field.value ?? ''])));
    setConfirmSubmit(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);

  const changed = useMemo(() => Object.fromEntries(
    fields.filter(field => (draft[field.key] ?? '') !== (field.value ?? '') && (draft[field.key] ?? '').trim() !== '')
      .map(field => [field.key, draft[field.key]]),
  ), [draft, fields]);
  const changedCount = Object.keys(changed).length;
  const missingRequired = fields.filter(field => field.required && !field.value);
  const missing = fields.filter(field => !field.value);
  const shown = onlyMissing && missing.length ? fields.filter(field => !field.value || field.error || changed[field.key]) : fields;
  const finished = ['SUBMITTED', 'COMPLETED', 'REJECTED', 'FAILED'].includes(application.status);
  const submitted = application.status === 'SUBMITTED';
  const sessionOpen = form.session_open !== false && !finished;
  const answers = form.answers ?? [];
  const [showAnswers, setShowAnswers] = useState(finished);
  const locked = !sessionOpen || busy !== null;
  // Long forms: collapsible. Open by default only while it needs your answers.
  const [open, setOpen] = useState(sessionOpen && missingRequired.length > 0);
  useEffect(() => { if (sessionOpen && missingRequired.length > 0) setOpen(true); // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);

  async function run(kind: 'fill' | 'continue' | 'submit' | 'recheck', action: () => Promise<Application>) {
    setBusy(kind); setError('');
    try { onUpdated(await action()); }
    catch (e) { setError(e instanceof Error ? e.message : 'Something went wrong.'); }
    finally { setBusy(null); setConfirmSubmit(false); }
  }

  if (!fields.length && !form.mode) return null;

  return (
    <div className="rounded-[2rem] border border-[#EBCDAA] bg-[#FFF9F0] p-5">
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" onClick={() => setOpen(v => !v)} className="flex items-center gap-2 text-left" aria-expanded={open}>
          {open ? <ChevronDown size={16} className="text-[#A16A45]" /> : <ChevronRight size={16} className="text-[#A16A45]" />}
          <h3 className="flex items-center gap-2 text-lg font-black text-ink"><FileText size={18} className="text-orange" /> Application form</h3>
        </button>
        <span className="rounded-full bg-[#FFE4C8] px-3 py-1 text-[11px] font-black uppercase tracking-[.12em] text-[#82512D]">
          {form.mode === 'easy_apply' ? `LinkedIn Easy Apply · step ${form.step ?? 1}` : form.mode === 'indeed_apply' ? `Indeed Apply · step ${form.step ?? 1}` : form.mode === 'ats' ? `Company portal · page ${form.step ?? 1}` : 'Company form'}
        </span>
        {form.resume_uploaded && <span className="rounded-full bg-[#E3F7D9] px-3 py-1 text-[11px] font-bold text-[#347233]">Resume attached</span>}
        {finished && <span className="rounded-full bg-[#DDF4D5] px-3 py-1 text-[11px] font-black uppercase tracking-[.12em] text-[#347233]">{application.status.toLowerCase()}</span>}
      </div>
      {form.url && <p className="mt-1 truncate font-mono text-[11px] text-[#805940]">{form.url}</p>}

      {!open && (
        <button type="button" onClick={() => setOpen(true)} className="mt-3 w-full rounded-xl bg-white px-4 py-2.5 text-left text-sm font-bold text-[#805940] hover:bg-[#FFF4E7]">
          {fields.length} fields · {missingRequired.length ? `${missingRequired.length} required still need your answer` : 'nothing required missing'}{answers.length ? ` · ${answers.length} answers so far` : ''} — show
        </button>
      )}
      {open && <>

      {!sessionOpen && application.status === 'HUMAN_REVIEW_REQUIRED' && (
        <p className="mt-4 flex items-center gap-2 rounded-xl bg-[#FFE1D6] px-4 py-3 text-sm font-bold text-[#AD3212]"><AlertTriangle size={16} /> The form is no longer open in the browser (tab closed or backend restarted). Use Retry in the queue to run this job again.</p>
      )}

      {sessionOpen && (
        <div className={`mt-4 rounded-xl px-4 py-3 text-sm font-bold ${missingRequired.length ? 'bg-[#FFF0DE] text-[#92502A]' : 'bg-[#E3F7D9] text-[#347233]'}`}>
          {missingRequired.length
            ? `${missingRequired.length} required field(s) need your answer. Fill them below and press “Fill in browser”.`
            : form.has_next ? 'This step is complete — continue to the next step.'
              : form.can_submit ? 'All required fields are filled. Review the form in the browser window, then submit.'
                : 'Review the form in the browser window.'}
        </div>
      )}

      {!finished && <>
      <div className="mt-4 flex items-center justify-between text-xs font-bold text-[#805940]">
        <span>{fields.length} fields · {missing.length} empty · {missingRequired.length} required missing</span>
        {missing.length > 0 && (
          <label className="flex cursor-pointer items-center gap-2"><input type="checkbox" checked={onlyMissing} onChange={e => setOnlyMissing(e.target.checked)} /> Show only missing</label>
        )}
      </div>

      <div className="mt-3 space-y-3">
        {shown.map(field => {
          const badge = field.source ? SOURCE_BADGE[field.source] : null;
          const BadgeIcon = badge?.icon;
          const isChanged = changed[field.key] !== undefined;
          return (
            <div key={field.key} className={`rounded-2xl border bg-white p-3 ${field.error ? 'border-[#E9A38E]' : !field.value && field.required ? 'border-[#F4B27E]' : 'border-[#EBCDAA]'}`}>
              <div className="mb-2 flex items-start gap-2">
                <p className="flex-1 text-sm font-bold text-[#542914]">
                  {field.group && <span className="block text-[11px] font-normal text-[#805940]">{field.group}</span>}
                  {field.label}{field.required && !/\*\s*$/.test(field.label) && <span className="text-[#AD3212]"> *</span>}
                </p>
                {isChanged ? <span className="rounded-full bg-[#FFF0DE] px-2 py-0.5 text-[10px] font-black text-[#92502A]"><PencilLine size={10} className="mr-1 inline" />edited</span>
                  : badge && BadgeIcon ? <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-black ${badge.className}`}><BadgeIcon size={10} />{badge.label}</span>
                    : <span className="rounded-full bg-[#FFE1D6] px-2 py-0.5 text-[10px] font-black text-[#AD3212]">Missing</span>}
              </div>
              <FieldControl field={field} value={draft[field.key] ?? ''} disabled={locked} onChange={value => setDraft(current => ({ ...current, [field.key]: value }))} />
              {field.hint && field.hint !== field.error && <p className="mt-1 text-xs text-[#805940]">{field.hint}</p>}
              {field.error && <p className="mt-1 text-xs font-bold text-[#AD3212]">{field.error}</p>}
            </div>
          );
        })}
        {!shown.length && <p className="rounded-xl border border-dashed border-[#EBCDAA] bg-white p-4 text-sm font-bold text-[#805940]">No fields on this step.</p>}
      </div>

      </>}

      {answers.length > 0 && (
        <div className="mt-5 rounded-2xl border border-[#EBCDAA] bg-white p-3">
          <button type="button" onClick={() => setShowAnswers(v => !v)} className="flex w-full items-center gap-2 text-sm font-black text-ink">
            {showAnswers ? <ChevronDown size={16} /> : <ChevronRight size={16} />}<ListChecks size={16} className="text-orange" /> What was filled ({answers.length})
            <span className="ml-auto text-[11px] font-bold text-[#805940]">saved with this application</span>
          </button>
          {showAnswers && (
            <table className="mt-3 w-full text-left text-xs">
              <thead><tr className="border-b border-[#EBCDAA] text-[#805940]">{(form.mode === 'easy_apply' || form.mode === 'indeed_apply' || form.mode === 'ats') && <th className="py-1.5 pr-2">Step</th>}<th className="py-1.5 pr-2">Question</th><th className="py-1.5 pr-2">Answer</th><th className="py-1.5">By</th></tr></thead>
              <tbody>
                {answers.map((answer, index) => (
                  <tr key={`${answer.step}-${answer.label}-${index}`} className="border-b border-[#EBCDAA]/50 align-top">
                    {(form.mode === 'easy_apply' || form.mode === 'indeed_apply' || form.mode === 'ats') && <td className="py-1.5 pr-2 text-[#805940]">{answer.step}</td>}
                    <td className="py-1.5 pr-2 font-bold text-[#542914]">{answer.label}</td>
                    <td className="break-all py-1.5 pr-2 text-[#542914]">{answer.value}</td>
                    <td className="py-1.5 text-[#805940]">{answer.source === 'ai' ? 'AI' : answer.source === 'user' ? 'You' : answer.source === 'prefilled' ? 'Prefilled' : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {error && <p className="mt-4 rounded-xl bg-[#FFE1D6] px-4 py-3 text-sm font-bold text-[#AD3212]">{error}</p>}

      {sessionOpen && (
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <Button type="button" onClick={() => run('fill', () => api.fillApplicationFields(token, application.id, changed, remember))} disabled={locked || !changedCount}>
            {busy === 'fill' ? <LoaderCircle className="animate-spin" size={16} /> : <PencilLine size={16} />} Fill in browser{changedCount ? ` (${changedCount})` : ''}
          </Button>
          <Button type="button" variant="secondary" onClick={() => run('recheck', () => api.recheckApplication(token, application.id))} disabled={locked}
            title="The AI re-validates every field on this step against your profile, saved answers and notes, and fixes what doesn't match">
            {busy === 'recheck' ? <LoaderCircle className="animate-spin" size={16} /> : <ListChecks size={16} />} Check with my profile
          </Button>
          {form.has_next && (
            <Button type="button" variant="secondary" onClick={() => run('continue', () => api.continueApplication(token, application.id))} disabled={locked || changedCount > 0 || missingRequired.length > 0}>
              {busy === 'continue' ? <LoaderCircle className="animate-spin" size={16} /> : <ArrowRight size={16} />} Continue to next step
            </Button>
          )}
          {form.submit_available && !form.has_next && (
            confirmSubmit ? <>
              <Button type="button" onClick={() => run('submit', () => api.submitApplication(token, application.id))} disabled={locked}>
                {busy === 'submit' ? <LoaderCircle className="animate-spin" size={16} /> : <Send size={16} />} Yes, submit now
              </Button>
              <Button type="button" variant="ghost" onClick={() => setConfirmSubmit(false)} disabled={busy !== null}>Cancel</Button>
            </> : (
              <Button type="button" variant="secondary" onClick={() => setConfirmSubmit(true)} disabled={locked || !form.can_submit || changedCount > 0}>
                <Send size={16} /> Submit application
              </Button>
            )
          )}
          <label className="ml-auto flex cursor-pointer items-center gap-2 text-xs font-bold text-[#805940]">
            <input type="checkbox" checked={remember} onChange={e => setRemember(e.target.checked)} /> Save my answers to my profile details
          </label>
        </div>
      )}
      </>}
    </div>
  );
}
