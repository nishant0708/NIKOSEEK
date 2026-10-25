import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { BriefcaseBusiness, CheckSquare, ExternalLink, Filter, Globe, Layers, Link2, LoaderCircle, Search, Sparkles, Square } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { api, INDEED_NOT_CONNECTED, LINKEDIN_NOT_CONNECTED, type Job, type JobPage, type JobSearchProgress } from '../lib/api';

type View = 'all' | 'linkedin' | 'indeed' | 'search' | 'url';
type Verdict = 'accepted' | 'rejected' | 'pending';

function jobKey(job: Job) {
  return String(job.id ?? job.external_url);
}

function Card({ job, selected, selectable, onToggle, onScore, scoring }: { job: Job; selected?: boolean; selectable?: boolean; onToggle?: () => void; onScore?: () => void; scoring?: boolean }) {
  return (
    <article className={`rounded-2xl border bg-[#FFF9F0] p-5 transition hover:border-orange hover:shadow-soft ${selected ? 'border-orange shadow-soft' : 'border-[#EBCDAA]'}`}>
      <div className="flex items-start gap-3">
        {selectable ? (
          <button type="button" onClick={onToggle} aria-label={selected ? 'Deselect job' : 'Select job'} className="mt-1 rounded-lg text-orange transition hover:scale-105">
            {selected ? <CheckSquare size={22} fill="currentColor" /> : <Square size={22} />}
          </button>
        ) : (
          <BriefcaseBusiness className="mt-1 text-orange" />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-3">
            <div className="min-w-0">
              <h3 className="truncate font-black">{job.title}</h3>
              <p className="mt-1 text-sm text-[#865D42]">{job.company} · {job.location}</p>
            </div>
            <div className="flex min-w-0 flex-wrap items-center gap-1.5">
              <span className="rounded-full bg-[#FFF0DE] px-2 py-1 text-[10px] font-black uppercase tracking-[.1em] text-[#92502A]">{job.source}</span>
              {job.fetched_by_cron && <span title="Fetched by the scheduled agent" className="rounded-full bg-[#F0E7FF] px-2 py-1 text-[10px] font-black uppercase tracking-[.1em] text-[#7541AE]">Cron</span>}
              {job.resume_category && <span title="Resume category (resume agent)" className="rounded-full bg-[#F4ECFF] px-2 py-1 text-[10px] font-black text-[#7A3FB8]">{job.resume_category}</span>}
              <b className={`rounded-full px-2 py-1 text-[10px] ${job.ai_status === 'ACCEPTED' ? 'bg-[#DDF4D5] text-[#347233]' : job.ai_status === 'PENDING' ? 'bg-[#FFF0BD] text-[#805940]' : 'bg-[#FFE1D6] text-[#AD3212]'}`}>
                {job.ai_status === 'PENDING' ? 'Pending score' : `${job.match_score?.toFixed(0) ?? 0}% match`}
              </b>
            </div>
          </div>
          <p className="mt-3 text-sm text-[#875D42]">{job.ai_reason}</p>
          <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] font-bold text-[#A16A45]">
            {job.date_posted && <span>Posted {job.date_posted}</span>}
            {job.scraped_at && <span>Saved {new Date(job.scraped_at).toLocaleDateString()}</span>}
          </div>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <a href={job.external_url} target="_blank" rel="noreferrer"
              className="inline-flex items-center gap-1 rounded-full bg-[#FFE4C8] px-3 py-1.5 text-xs font-bold text-[#82512D] transition hover:bg-[#FFD7AE]">
              Open posting <ExternalLink size={13} />
            </a>
            {job.ai_status === 'PENDING' && onScore && (
              <Button type="button" size="sm" onClick={onScore} disabled={scoring}>
                {scoring ? <LoaderCircle className="animate-spin" size={15} /> : <Sparkles size={15} />}
                {scoring ? 'Scoring…' : 'Score now'}
              </Button>
            )}
          </div>
        </div>
      </div>
    </article>
  );
}

function Field({ label, hint, children, className = '' }: { label: string; hint?: string; children: ReactNode; className?: string }) {
  return (
    <label className={`block ${className}`}>
      <span className="text-xs font-black uppercase tracking-[.12em] text-[#82512D]">{label}</span>
      <div className="mt-1.5">{children}</div>
      {hint && <span className="mt-1 block text-[11px] text-[#A16A45]">{hint}</span>}
    </label>
  );
}

function SideItem({ active, icon, label, count, onClick }: { active: boolean; icon: ReactNode; label: string; count?: number; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick}
      className={`flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left text-sm font-bold transition ${active ? 'bg-orange text-white shadow-soft' : 'text-[#6B4329] hover:bg-[#FFE4C8]'}`}>
      {icon}
      <span className="flex-1">{label}</span>
      {count !== undefined && <span className={`rounded-full px-2 py-0.5 text-[11px] ${active ? 'bg-white/25' : 'bg-[#FFE4C8] text-[#82512D]'}`}>{count}</span>}
    </button>
  );
}

export function JobSearch({ token }: { token: string }) {
  const navigate = useNavigate();
  const [view, setView] = useState<View>('all');
  const [verdict, setVerdict] = useState<Verdict>('accepted');
  const [filter, setFilter] = useState('');
  // Search form
  const [term, setTerm] = useState('');
  const [source, setSource] = useState('linkedin');
  const [location, setLocation] = useState('India');
  const [indeedCountry, setIndeedCountry] = useState('India');
  const [experience, setExperience] = useState('0-1 years');
  const [postedWithin, setPostedWithin] = useState<number | null>(168);
  const [salary, setSalary] = useState('');
  const [wanted, setWanted] = useState('10');
  const [jobPage, setJobPage] = useState<JobPage | null>(null);
  const [lastSearch, setLastSearch] = useState<{ term: string; source: string; runId: number } | null>(null);
  const [page, setPage] = useState(1);
  const [tag, setTag] = useState('');
  const [sort, setSort] = useState('newest');
  const [loadingJobs, setLoadingJobs] = useState(false);
  const [reload, setReload] = useState(0);
  const [selectedJobs, setSelectedJobs] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [applyBusy, setApplyBusy] = useState(false);
  const [jobUrl, setJobUrl] = useState('');
  const [urlBusy, setUrlBusy] = useState(false);
  const [scoringJobId, setScoringJobId] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [searchStatus, setSearchStatus] = useState('');
  const [searchEvents, setSearchEvents] = useState<string[]>([]);
  const searchActive = useRef(false);

  useEffect(() => {
    const socket = new WebSocket(api.jobSearchSocketUrl(token));
    socket.onmessage = event => {
      let update: JobSearchProgress | null = null;
      try { update = JSON.parse(event.data) as JobSearchProgress; } catch { return; }
      // This broker also serves background cron work for the same user. Only
      // attach live manual-search events to the visible active search.
      if (!searchActive.current || update?.type !== 'job_search.progress' || update.fetch_mode !== 'manual') return;
      setSearchStatus(update.message);
      setSearchEvents(current => [...current, update!.message].slice(-6));
    };
    return () => socket.close();
  }, [token]);

  useEffect(() => { setSelectedJobs([]); setPage(1); setTag(''); }, [view, verdict]);

  useEffect(() => {
    let active = true;
    setLoadingJobs(true);
    api.jobs(token, {
      page, page_size: 12, source: view === 'all' || view === 'search' || view === 'url' ? undefined : view,
      status_filter: verdict.toUpperCase(), q: filter.trim() || undefined, tag: tag || undefined, sort,
      run_id: view === 'search' ? lastSearch?.runId : undefined,
    }).then(result => { if (active) setJobPage(result); })
      .catch(error => { if (active) setError(error instanceof Error ? error.message : 'Could not load jobs.'); })
      .finally(() => { if (active) setLoadingJobs(false); });
    return () => { active = false; };
  }, [token, view, verdict, filter, tag, sort, page, lastSearch?.runId, reload]);

  async function search(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    searchActive.current = true;
    setError('');
    setSearchStatus('Connecting to live search updates…');
    setSearchEvents([]);
    try {
      const result = await api.searchJobs(token, {
        sources: [source],
        search_term: term,
        location,
        country_indeed: source === 'indeed' ? indeedCountry : null,
        experience,
        min_salary_lpa: salary ? Number(salary) : null,
        target_relevant_count: Number(wanted),
        // Fetch a candidate pool larger than the requested accepted match count.
        results_wanted: Math.min(100, Math.max(30, Number(wanted) * 10)),
        hours_old: postedWithin,
      });
      setLastSearch({ term, source, runId: result.run_id });
      setView('search');
      setVerdict('accepted');
      setPage(1);
      setReload(current => current + 1);
      setSearchStatus(`Found ${result.fetched_count} jobs · ${result.accepted_jobs.length}/${wanted} matches`);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Search failed.');
    } finally {
      searchActive.current = false;
      setBusy(false);
    }
  }

  const shown = jobPage?.items ?? [];
  const selectedAccepted = shown.filter(job => job.ai_status === 'ACCEPTED' && selectedJobs.includes(jobKey(job)));
  const selectedPending = shown.filter(job => job.ai_status === 'PENDING' && selectedJobs.includes(jobKey(job)));
  const allSelected = shown.length > 0 && shown.every(job => job.ai_status === 'ACCEPTED' && selectedJobs.includes(jobKey(job)));
  const allPendingSelected = shown.length > 0 && shown.every(job => job.ai_status === 'PENDING' && selectedJobs.includes(jobKey(job)));
  const counts = jobPage?.source_counts ?? { all: 0, linkedin: 0, indeed: 0 };

  function toggleJob(job: Job) {
    const key = jobKey(job);
    setSelectedJobs(current => (current.includes(key) ? current.filter(item => item !== key) : [...current, key]));
  }

  async function queueApply() {
    const jobIds = selectedAccepted.map(job => job.id).filter((id): id is number => typeof id === 'number');
    if (!jobIds.length) return;
    setApplyBusy(true);
    setError('');
    try {
      await api.applyJobs(token, { job_ids: jobIds });
      setSelectedJobs([]);
      setReload(current => current + 1);
      navigate('/workflow/live');
    } catch (e) {
      if (e instanceof Error && e.message === LINKEDIN_NOT_CONNECTED) { navigate('/connect-linkedin'); return; }
      if (e instanceof Error && e.message === INDEED_NOT_CONNECTED) { navigate('/connect-indeed'); return; }
      setError(e instanceof Error ? e.message : 'Could not queue the selected jobs.');
    } finally {
      setApplyBusy(false);
    }
  }

  // A posting you found yourself: straight to the apply queue, no match scoring.
  async function queueFromUrl(event: FormEvent) {
    event.preventDefault();
    if (!jobUrl.trim()) return;
    setUrlBusy(true);
    setError('');
    try {
      await api.applyFromUrl(token, jobUrl.trim());
      setJobUrl('');
      navigate('/workflow/live');
    } catch (e) {
      if (e instanceof Error && e.message === LINKEDIN_NOT_CONNECTED) { navigate('/connect-linkedin'); return; }
      if (e instanceof Error && e.message === INDEED_NOT_CONNECTED) { navigate('/connect-indeed'); return; }
      setError(e instanceof Error ? e.message : 'Could not queue that job.');
    } finally {
      setUrlBusy(false);
    }
  }

  async function scoreJob(job: Job) {
    if (typeof job.id !== 'number') return;
    setScoringJobId(job.id);
    setError('');
    try {
      await api.scoreJob(token, job.id);
      setReload(current => current + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not score this job.');
    } finally {
      setScoringJobId(null);
    }
  }

  async function scoreSelectedPending() {
    const jobsToScore = selectedPending.filter(job => typeof job.id === 'number');
    if (!jobsToScore.length) return;
    setScoringJobId(-1);
    setError('');
    try {
      for (let start = 0; start < jobsToScore.length; start += 10) {
        await api.scoreJobs(token, jobsToScore.slice(start, start + 10).map(job => job.id!));
      }
      setSelectedJobs([]);
      setReload(current => current + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not score the selected jobs.');
    } finally {
      setScoringJobId(null);
    }
  }

  const title = { all: 'All jobs', linkedin: 'LinkedIn jobs', indeed: 'Indeed jobs', search: 'Search', url: 'Add a job by URL' }[view];

  return (
    <main className="min-h-screen bg-cream px-5 py-7">
      <div className="mx-auto flex p-2 flex-col gap-6 lg:flex-row">
        <aside className="lg:sticky lg:top-24 lg:h-fit lg:w-60 lg:shrink-0">
          <div className="rounded-[1.75rem] border border-[#EBCDAA] bg-[#FFF9F0] p-3">
            <p className="px-3 pb-2 pt-1 text-[11px] font-black uppercase tracking-[.16em] text-orange">Jobs</p>
            <div className="space-y-1">
              <SideItem active={view === 'all'} icon={<Layers size={16} />} label="All jobs" count={counts.all} onClick={() => setView('all')} />
              <SideItem active={view === 'linkedin'} icon={<Globe size={16} />} label="LinkedIn jobs" count={counts.linkedin} onClick={() => setView('linkedin')} />
              <SideItem active={view === 'indeed'} icon={<BriefcaseBusiness size={16} />} label="Indeed jobs" count={counts.indeed} onClick={() => setView('indeed')} />
              <SideItem active={view === 'search'} icon={<Search size={16} />} label="Search" count={view === 'search' ? jobPage?.total : undefined} onClick={() => setView('search')} />
              <SideItem active={view === 'url'} icon={<Link2 size={16} />} label="Add by URL" onClick={() => setView('url')} />
            </div>
          </div>
        </aside>

        <section className="min-w-0 flex-1">
          <p className="text-xs font-black uppercase tracking-[.16em] text-orange">AI job matcher</p>
          <h1 className="mt-1 text-3xl font-black">{title}</h1>

          {view === 'search' && (
            <form onSubmit={search} className="mt-5 rounded-[1.75rem] border border-[#EBCDAA] bg-[#FFF9F0] p-5">
              <div className="flex items-center gap-2 text-sm font-black text-ink"><Sparkles size={16} className="text-orange" /> Find and score new jobs</div>
              <p className="mt-1 text-xs text-[#805940]">New results are scored against your profile and added to All jobs — nothing you already have disappears.</p>
              <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                <Field label="Role" className="sm:col-span-2 lg:col-span-2">
                  <Input required value={term} onChange={e => setTerm(e.target.value)} placeholder="e.g. Backend Developer" className="bg-white" />
                </Field>
                <Field label="Job board">
                  <div className="flex rounded-2xl border border-[#E7BD91] bg-white p-1">
                    {(['linkedin', 'indeed'] as const).map(s => (
                      <button key={s} type="button" onClick={() => setSource(s)}
                        className={`flex-1 rounded-xl py-2 text-sm font-bold capitalize transition ${source === s ? 'bg-orange text-white' : 'text-[#82512D] hover:bg-[#FFF0DE]'}`}>
                        {s === 'linkedin' ? 'LinkedIn' : 'Indeed'}
                      </button>
                    ))}
                  </div>
                </Field>
                <Field label="Location" hint="City or country">
                  <Input value={location} onChange={e => setLocation(e.target.value)} placeholder="e.g. Pune, India" className="bg-white" />
                </Field>
                {source === 'indeed' && (
                  <Field label="Indeed country" hint="Which Indeed site to search">
                    <Input required value={indeedCountry} onChange={e => setIndeedCountry(e.target.value)} placeholder="e.g. India" className="bg-white" />
                  </Field>
                )}
                <Field label="Experience" hint="Used for scoring">
                  <Input value={experience} onChange={e => setExperience(e.target.value)} placeholder="e.g. 0-1 years" className="bg-white" />
                </Field>
                <Field label="Posted within" hint="Filter by the job's posting date">
                  <select value={postedWithin ?? ''} onChange={e => setPostedWithin(e.target.value ? Number(e.target.value) : null)}
                    className="h-11 w-full rounded-2xl border border-[#E7BD91] bg-white px-3 text-sm">
                    <option value={24}>Past 24 hours</option>
                    <option value={72}>Past 3 days</option>
                    <option value={720}>Past month</option>
                    <option value="">All</option>
                  </select>
                </Field>
                <Field label="Min salary (LPA)" hint="Optional — leave empty for any">
                  <Input type="number" value={salary} onChange={e => setSalary(e.target.value)} placeholder="e.g. 10" className="bg-white" />
                </Field>
                <Field label="Matches wanted" hint="Stops after this many good matches">
                  <Input type="number" min="1" max="50" value={wanted} onChange={e => setWanted(e.target.value)} className="bg-white" />
                </Field>
              </div>
              <Button className="mt-5 w-full" disabled={busy}>
                {busy ? <LoaderCircle className="animate-spin" /> : <Search size={18} />}
                {busy ? 'Searching and scoring…' : 'Score and find jobs'}
              </Button>
            </form>
          )}

          {view === 'url' && (
            <form onSubmit={queueFromUrl} className="mt-5 rounded-[1.75rem] border border-[#EBCDAA] bg-[#FFF9F0] p-5">
              <div className="flex items-center gap-2 text-sm font-black text-ink"><Link2 size={16} className="text-orange" /> Apply to a job you found yourself</div>
              <p className="mt-1 text-xs text-[#805940]">Paste a LinkedIn or Indeed job URL. It goes straight to the apply queue: AI match scoring is skipped, everything else (resume agent, apply agent, your review) runs as usual.</p>
              <label className="mt-4 block text-xs font-black uppercase tracking-[.12em] text-[#82512D]">Job URL
                <Input required type="text" inputMode="url" value={jobUrl} onChange={e => setJobUrl(e.target.value)}
                  placeholder="https://www.linkedin.com/jobs/view/4470153536/" className="mt-2 bg-white normal-case tracking-normal" />
              </label>
              <p className="mt-2 text-xs text-[#805940]">Works with linkedin.com/jobs/view/… , a LinkedIn search link with currentJobId=…, or an Indeed link with jk=… .</p>
              <Button className="mt-5 w-full" disabled={urlBusy || !jobUrl.trim()}>
                {urlBusy ? <LoaderCircle className="animate-spin" /> : <Link2 size={18} />}
                {urlBusy ? 'Adding to the queue…' : 'Add to apply queue'}
              </Button>
            </form>
          )}

          {error && <p className="mt-4 rounded-xl bg-[#FFE1D6] p-4 font-bold text-[#AD3212]">{error}</p>}
          {busy && (
            <div className="mt-4 rounded-xl border border-[#EBCDAA] bg-[#FFF0DE] px-4 py-3 text-sm font-bold text-[#92502A]" aria-live="polite">
              <div className="flex items-center gap-3"><LoaderCircle size={16} className="animate-spin" /><span>{searchStatus}</span></div>
              {searchEvents.length > 1 && <ol className="mt-2 space-y-1 border-t border-[#EBCDAA] pt-2 text-xs font-medium text-[#805940]">
                {searchEvents.slice(0, -1).map((message, index) => <li key={`${index}-${message}`}>• {message}</li>)}
              </ol>}
            </div>
          )}

          {view === 'url' ? null : view === 'search' && !lastSearch ? (
            <p className="mt-5 rounded-2xl bg-[#FFF9F0] p-6 text-sm font-bold text-[#805940]">Results of your search show up here (and in All jobs).</p>
          ) : (
            <>
              {view === 'search' && lastSearch && (
                <p className="mt-5 text-sm font-bold text-[#805940]">Results for “{lastSearch.term}” on {lastSearch.source === 'indeed' ? 'Indeed' : 'LinkedIn'}</p>
              )}
              <div className="mt-5 flex flex-wrap items-center gap-2">
                <button onClick={() => setVerdict('accepted')} className={`rounded-full px-4 py-2 text-sm font-bold ${verdict === 'accepted' ? 'bg-orange text-white' : 'bg-[#FFE4C8]'}`}>Accepted{verdict === 'accepted' ? ` (${jobPage?.total ?? 0})` : ''}</button>
                <button onClick={() => setVerdict('rejected')} className={`rounded-full px-4 py-2 text-sm font-bold ${verdict === 'rejected' ? 'bg-[#A54222] text-white' : 'bg-[#FFE4C8]'}`}>Rejected{verdict === 'rejected' ? ` (${jobPage?.total ?? 0})` : ''}</button>
                <button onClick={() => setVerdict('pending')} className={`rounded-full px-4 py-2 text-sm font-bold ${verdict === 'pending' ? 'bg-[#805940] text-white' : 'bg-[#FFE4C8]'}`}>Pending score{verdict === 'pending' ? ` (${jobPage?.total ?? 0})` : ''}</button>
                <div className="relative min-w-[200px] flex-1 sm:max-w-xs">
                  <Filter size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#A16A45]" />
                  <input value={filter} onChange={e => { setFilter(e.target.value); setPage(1); }} placeholder="Search title, company, place or tag"
                    className="h-10 w-full rounded-full border border-[#E7BD91] bg-white pl-8 pr-3 text-sm outline-none focus:border-orange" />
                </div>
                <select value={tag} onChange={e => { setTag(e.target.value); setPage(1); }} className="h-10 rounded-full border border-[#E7BD91] bg-white px-3 text-sm font-bold text-[#82512D]">
                  <option value="">All tags</option>
                  {(jobPage?.tags ?? []).map(value => <option key={value} value={value}>{value}</option>)}
                </select>
                <select value={sort} onChange={e => { setSort(e.target.value); setPage(1); }} className="h-10 rounded-full border border-[#E7BD91] bg-white px-3 text-sm font-bold text-[#82512D]">
                  <option value="newest">Newest saved</option><option value="oldest">Oldest saved</option>
                  <option value="match_high">Best match</option><option value="match_low">Lowest match</option>
                </select>
                {verdict === 'pending' && shown.length > 0 && (
                  <div className="ml-auto flex flex-wrap gap-2">
                    <Button type="button" variant="secondary" size="sm" onClick={() => setSelectedJobs(allPendingSelected ? [] : shown.map(jobKey))} className="bg-[#FFE4C8] text-[#82512D] hover:bg-[#FFD7AE]">
                      {allPendingSelected ? 'Clear all' : 'Select all'}
                    </Button>
                    <Button type="button" size="sm" onClick={scoreSelectedPending} disabled={!selectedPending.length || scoringJobId !== null}>
                      {scoringJobId === -1 ? <LoaderCircle className="animate-spin" /> : <Sparkles size={15} />}
                      Score selected ({selectedPending.length})
                    </Button>
                  </div>
                )}
                {verdict === 'accepted' && shown.length > 0 && (
                  <div className="ml-auto flex flex-wrap gap-2">
                    <Button type="button" variant="secondary" size="sm" onClick={() => setSelectedJobs(allSelected ? [] : shown.map(jobKey))} className="bg-[#FFE4C8] text-[#82512D] hover:bg-[#FFD7AE]">
                      {allSelected ? 'Clear all' : 'Select all'}
                    </Button>
                    <Button type="button" size="sm" onClick={queueApply} disabled={!selectedAccepted.length || applyBusy}>
                      {applyBusy ? <LoaderCircle className="animate-spin" /> : null}
                      Apply selected ({selectedAccepted.length})
                    </Button>
                  </div>
                )}
              </div>
              <div className="mt-4 grid gap-3">
                {loadingJobs && <p className="flex items-center gap-2 rounded-2xl bg-[#FFF0DE] p-4 text-sm font-bold text-[#92502A]"><LoaderCircle size={16} className="animate-spin" /> Loading jobs…</p>}
                {shown.map((job, index) => (
                  <Card key={job.id ?? index} job={job} selectable={(verdict === 'accepted' && job.ai_status === 'ACCEPTED') || (verdict === 'pending' && job.ai_status === 'PENDING')}
                    selected={selectedJobs.includes(jobKey(job))} onToggle={() => toggleJob(job)}
                    onScore={verdict === 'pending' ? () => scoreJob(job) : undefined} scoring={scoringJobId === job.id || scoringJobId === -1} />
                ))}
                {!shown.length && <p className="rounded-2xl bg-[#FFF9F0] p-6 text-sm font-bold text-[#805940]">{filter ? 'No jobs match this filter.' : 'No jobs here yet — use Search to find some.'}</p>}
              </div>
              {(jobPage?.total_pages ?? 1) > 1 && <div className="mt-5 flex items-center justify-center gap-3">
                <Button type="button" variant="secondary" size="sm" disabled={page <= 1 || loadingJobs} onClick={() => setPage(current => current - 1)}>Previous</Button>
                <span className="text-sm font-bold text-[#805940]">Page {jobPage?.page} of {jobPage?.total_pages} · {jobPage?.total} jobs</span>
                <Button type="button" variant="secondary" size="sm" disabled={page >= (jobPage?.total_pages ?? 1) || loadingJobs} onClick={() => setPage(current => current + 1)}>Next</Button>
              </div>}
            </>
          )}
        </section>
      </div>
    </main>
  );
}
