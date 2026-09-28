import { useEffect, useState, type FormEvent } from 'react';
import { CheckSquare, ExternalLink, LoaderCircle, Search, Square, BriefcaseBusiness } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { api, LINKEDIN_NOT_CONNECTED, type Job } from '../lib/api';

function jobKey(job: Job) {
  return String(job.id ?? job.external_url);
}

function Card({
  job,
  selected,
  selectable,
  onToggle,
}: {
  job: Job;
  selected?: boolean;
  selectable?: boolean;
  onToggle?: () => void;
}) {
  return (
    <article
      className={`rounded-2xl border bg-[#FFF9F0] p-5 transition hover:border-orange hover:shadow-soft ${selected ? 'border-orange shadow-soft' : 'border-[#EBCDAA]'}`}
    >
      <div className="flex items-start gap-3">
        {selectable ? (
          <button
            type="button"
            onClick={onToggle}
            aria-label={selected ? 'Deselect job' : 'Select job'}
            className="mt-1 rounded-lg text-orange transition hover:scale-105"
          >
            {selected ? <CheckSquare size={22} fill="currentColor" /> : <Square size={22} />}
          </button>
        ) : (
          <BriefcaseBusiness className="mt-1 text-orange" />
        )}
        <div className="min-w-0 flex-1">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <h3 className="truncate font-black">{job.title}</h3>
              <p className="mt-1 text-sm text-[#865D42]">
                {job.company} · {job.location}
              </p>
            </div>
            <b
              className={`rounded-full px-2 py-1 text-[10px] ${job.ai_status === 'ACCEPTED' ? 'bg-[#DDF4D5] text-[#347233]' : 'bg-[#FFE1D6] text-[#AD3212]'}`}
            >
              {job.match_score?.toFixed(0) ?? 0}% match
            </b>
          </div>
          <p className="mt-3 text-sm text-[#875D42]">{job.ai_reason}</p>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <a
              href={job.external_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 rounded-full bg-[#FFE4C8] px-3 py-1.5 text-xs font-bold text-[#82512D] transition hover:bg-[#FFD7AE]"
            >
              Open posting <ExternalLink size={13} />
            </a>
          </div>
        </div>
      </div>
    </article>
  );
}

export function JobSearch({ token }: { token: string }) {
  const navigate = useNavigate();
  const [term, setTerm] = useState('');
  const [source, setSource] = useState('linkedin');
  const [location, setLocation] = useState('India');
  const [experience, setExperience] = useState('0-1 years');
  const [salary, setSalary] = useState('');
  const [wanted, setWanted] = useState('10');
  const [accepted, setAccepted] = useState<Job[]>([]);
  const [rejected, setRejected] = useState<Job[]>([]);
  const [selectedJobs, setSelectedJobs] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [applyBusy, setApplyBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [showRejected, setShowRejected] = useState(false);

  useEffect(() => {
    api.jobs(token)
      .then(items => {
        setAccepted(items.filter(job => job.ai_status === 'ACCEPTED'));
        setRejected(items.filter(job => job.ai_status === 'REJECTED'));
      })
      .catch(() => undefined);
  }, [token]);

  useEffect(() => {
    setSelectedJobs([]);
  }, [accepted, rejected]);

  async function search(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await api.searchJobs(token, {
        sources: [source],
        search_term: term,
        location,
        experience,
        min_salary_lpa: salary ? Number(salary) : null,
        target_relevant_count: Number(wanted),
        results_wanted: 30,
        hours_old: 168,
      });
      setAccepted(result.accepted_jobs);
      setRejected(result.rejected_jobs);
      setShowRejected(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Search failed.');
    } finally {
      setBusy(false);
    }
  }

  const shown = showRejected ? rejected : accepted;
  const selectedAcceptedJobs = accepted.filter(job => selectedJobs.includes(jobKey(job)));
  const allAcceptedSelected = accepted.length > 0 && selectedAcceptedJobs.length === accepted.length;

  function toggleJob(job: Job) {
    const key = jobKey(job);
    setSelectedJobs(current => (current.includes(key) ? current.filter(item => item !== key) : [...current, key]));
  }

  function toggleAllAccepted() {
    setSelectedJobs(current => (current.length === accepted.length ? [] : accepted.map(job => jobKey(job))));
  }

  async function queueApply() {
    const jobIds = selectedAcceptedJobs.map(job => job.id).filter((jobId): jobId is number => typeof jobId === 'number');
    if (!jobIds.length) {
      return;
    }
    setApplyBusy(true);
    setError('');
    try {
      await api.applyJobs(token, { job_ids: jobIds });
      setAccepted(current => current.filter(job => !jobIds.includes(job.id ?? -1)));
      setSelectedJobs([]);
      navigate('/workflow/live');
    } catch (e) {
      if (e instanceof Error && e.message === LINKEDIN_NOT_CONNECTED) {
        navigate('/connect-linkedin');
        return;
      }
      setError(e instanceof Error ? e.message : 'Could not queue the selected jobs.');
    } finally {
      setApplyBusy(false);
    }
  }

  return (
    <main className="min-h-screen bg-cream px-5 py-9">
      <section className="mx-auto max-w-6xl">
        <p className="text-xs font-black uppercase tracking-[.16em] text-orange">AI job matcher</p>
        <h1 className="mt-2 text-4xl font-black">Find the jobs that fit.</h1>
        <form onSubmit={search} className="mt-6 grid gap-3 rounded-[2rem] bg-ink p-5 sm:grid-cols-2 lg:grid-cols-3">
          <Input required value={term} onChange={e => setTerm(e.target.value)} placeholder="Role, e.g. Backend Developer" />
          <select value={source} onChange={e => setSource(e.target.value)} className="rounded-2xl bg-[#FFF9F0] px-4 text-sm font-bold">
            <option value="linkedin">LinkedIn</option>
            <option value="indeed">Indeed</option>
          </select>
          <Input value={location} onChange={e => setLocation(e.target.value)} placeholder="Location" />
          <Input value={experience} onChange={e => setExperience(e.target.value)} placeholder="Experience, e.g. 0-1 years" />
          <Input type="number" value={salary} onChange={e => setSalary(e.target.value)} placeholder="Expected salary (LPA)" />
          <Input type="number" min="1" max="50" value={wanted} onChange={e => setWanted(e.target.value)} placeholder="Relevant jobs wanted" />
          <Button className="sm:col-span-2 lg:col-span-3" disabled={busy}>
            {busy ? <LoaderCircle className="animate-spin" /> : <Search size={18} />}
            Score and find jobs
          </Button>
        </form>

        {error && <p className="mt-4 rounded-xl bg-[#FFE1D6] p-4 font-bold text-[#AD3212]">{error}</p>}
        {notice && <p className="mt-4 rounded-xl bg-[#E3F7D9] p-4 font-bold text-[#347233]">{notice}</p>}

        <div className="mt-8 flex flex-wrap gap-3">
          <button
            onClick={() => setShowRejected(false)}
            className={`rounded-full px-4 py-2 text-sm font-bold ${!showRejected ? 'bg-orange text-white' : 'bg-[#FFE4C8]'}`}
          >
            Accepted ({accepted.length})
          </button>
          <button
            onClick={() => setShowRejected(true)}
            className={`rounded-full px-4 py-2 text-sm font-bold ${showRejected ? 'bg-[#A54222] text-white' : 'bg-[#FFE4C8]'}`}
          >
            Rejected ({rejected.length})
          </button>
          {!showRejected && accepted.length > 0 && (
            <div className="ml-auto flex flex-wrap gap-2">
              <Button type="button" variant="secondary" size="sm" onClick={toggleAllAccepted} className="bg-[#FFE4C8] text-[#82512D] hover:bg-[#FFD7AE]">
                {allAcceptedSelected ? 'Clear selection' : 'Select all accepted'}
              </Button>
              <Button type="button" size="sm" onClick={queueApply} disabled={!selectedAcceptedJobs.length || applyBusy}>
                {applyBusy ? <LoaderCircle className="animate-spin" /> : null}
                Apply selected ({selectedAcceptedJobs.length})
              </Button>
            </div>
          )}
        </div>

        {!showRejected && accepted.length > 0 && (
          <p className="mt-3 text-sm font-bold text-[#805940]">
            Select the accepted jobs you want to queue, then use Apply selected. Queued jobs leave this page and show up in Live workflow.
          </p>
        )}

        <div className="mt-5 grid gap-3">
          {shown.map((job, index) => (
            <Card
              key={job.id ?? index}
              job={job}
              selectable={!showRejected && job.ai_status === 'ACCEPTED'}
              selected={selectedJobs.includes(jobKey(job))}
              onToggle={() => toggleJob(job)}
            />
          ))}
          {!shown.length && <p className="rounded-2xl bg-[#FFF9F0] p-6 text-sm font-bold text-[#805940]">No jobs to show yet.</p>}
        </div>
      </section>
    </main>
  );
}
