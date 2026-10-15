import { useEffect, useState } from 'react';
import { AlarmClock, Bot, Save } from 'lucide-react';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import type { CronAgentSettings } from '../../lib/api';

type Props = { settings: CronAgentSettings | null; busy: boolean; onSave: (settings: CronAgentSettings) => void };

export function CronAgentCard({ settings, busy, onSave }: Props) {
  const [draft, setDraft] = useState<CronAgentSettings | null>(settings);
  useEffect(() => setDraft(settings), [settings]);
  if (!draft) return <section className="mt-7 h-44 animate-pulse rounded-[2rem] bg-[#FFF9F0]" />;

  const sourceOn = (source: 'linkedin' | 'indeed') => draft.sources.includes(source);
  function toggleSource(source: 'linkedin' | 'indeed') {
    setDraft(current => {
      if (!current) return current;
      const sources = current.sources.includes(source) ? current.sources.filter(item => item !== source) : [...current.sources, source];
      return { ...current, sources: sources.length ? sources : current.sources };
    });
  }
  const save = () => onSave({ ...draft, role: draft.role.trim(), sources: draft.sources, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || draft.timezone || 'Asia/Kolkata' });

  return (
    <section id="cron-agent-settings" className="mt-7 rounded-[2rem] border border-[#C8B1E6] bg-[#FBF8FF] p-6 sm:p-8">
      <div className="flex flex-wrap items-start gap-3">
        <div className="rounded-2xl bg-[#F0E7FF] p-3 text-[#7541AE]"><Bot size={22} /></div>
        <div className="min-w-0 flex-1"><h2 className="text-xl font-black">Ambient job agent</h2><p className="mt-1 max-w-2xl text-sm text-[#705783]">On its schedule, it fetches and scores fresh jobs, then queues accepted matches for application. Any application needing your input remains open in its browser tab for review.</p></div>
        <label className="inline-flex cursor-pointer items-center gap-2 rounded-full border border-[#B98BE6] bg-white px-3 py-2 text-sm font-black text-[#633796]"><input type="checkbox" className="accent-[#7541AE]" checked={draft.enabled} onChange={event => setDraft({ ...draft, enabled: event.target.checked })} /> {draft.enabled ? 'Scheduled ON' : 'Scheduled OFF'}</label>
      </div>
      <div className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <label className="block text-sm font-bold sm:col-span-2"><span>Role interested in</span><Input required value={draft.role} onChange={event => setDraft({ ...draft, role: event.target.value })} placeholder="Frontend Developer, Backend Engineer, Full-stack…" className="mt-2 bg-white" /></label>
        <label className="block text-sm font-bold"><span>Jobs per run</span><Input type="number" min="1" max="50" value={draft.jobs_per_run} onChange={event => setDraft({ ...draft, jobs_per_run: Math.max(1, Number(event.target.value) || 1) })} className="mt-2 bg-white" /></label>
        <fieldset className="text-sm font-bold"><legend>Sources</legend><div className="mt-2 flex h-11 items-center gap-4 rounded-xl border border-[#D4BCEB] bg-white px-3"><label className="flex items-center gap-1.5"><input type="checkbox" checked={sourceOn('linkedin')} onChange={() => toggleSource('linkedin')} /> LinkedIn</label><label className="flex items-center gap-1.5"><input type="checkbox" checked={sourceOn('indeed')} onChange={() => toggleSource('indeed')} /> Indeed</label></div></fieldset>
        <label className="block text-sm font-bold"><span>Posted within</span><select value={draft.hours_old} onChange={event => setDraft({ ...draft, hours_old: Number(event.target.value) })} className="mt-2 h-11 w-full rounded-xl border border-[#D4BCEB] bg-white px-3"><option value={24}>Last 24 hours</option><option value={48}>Last 48 hours</option><option value={72}>Last 3 days</option></select></label>
        <label className="block text-sm font-bold"><span>Run every</span><select value={draft.interval_hours} onChange={event => setDraft({ ...draft, interval_hours: Number(event.target.value) })} className="mt-2 h-11 w-full rounded-xl border border-[#D4BCEB] bg-white px-3"><option value={6}>6 hours</option><option value={12}>12 hours</option><option value={24}>24 hours</option><option value={48}>48 hours</option></select></label>
        <label className="block text-sm font-bold"><span>Scheduled start time</span><Input type="time" value={draft.scheduled_time} onChange={event => setDraft({ ...draft, scheduled_time: event.target.value })} className="mt-2 bg-white" /><span className="mt-1 block text-[11px] font-normal text-[#705783]">Runs on this time grid in your local timezone ({Intl.DateTimeFormat().resolvedOptions().timeZone || draft.timezone}).</span></label>
        <label className="block text-sm font-bold"><span>Location</span><Input value={draft.location} onChange={event => setDraft({ ...draft, location: event.target.value })} className="mt-2 bg-white" /></label>
        {sourceOn('indeed') && <label className="block text-sm font-bold"><span>Indeed country</span><Input value={draft.indeed_country ?? ''} onChange={event => setDraft({ ...draft, indeed_country: event.target.value || null })} className="mt-2 bg-white" /></label>}
      </div>
      {draft.last_run_at && <p className="mt-4 flex items-center gap-2 text-xs font-bold text-[#705783]"><AlarmClock size={14} /> Last checked {new Date(draft.last_run_at).toLocaleString()}{draft.last_error ? ` — ${draft.last_error}` : ''}</p>}
      <Button className="mt-5" onClick={save} disabled={busy || (draft.enabled && !draft.role.trim())}><Save size={16} /> {busy ? 'Saving…' : 'Save scheduled agent'}</Button>
    </section>
  );
}
