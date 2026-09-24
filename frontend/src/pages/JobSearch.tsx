import { useEffect, useMemo, useState, type FormEvent } from 'react';
import {
  ArrowDownAZ,
  BriefcaseBusiness,
  LoaderCircle,
  Search,
  SlidersHorizontal,
} from 'lucide-react';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { api, type Job } from '../lib/api';

type SortOption =
  | 'match-high'
  | 'match-low'
  | 'title-az'
  | 'title-za'
  | 'company-az'
  | 'company-za'
  | 'location-az'
  | 'location-za';

function isRelevantButTargetReached(job: Job) {
  const reason = (job.ai_reason ?? '').toLowerCase();

  return (
    job.ai_status === 'ACCEPTED' &&
    /target.*reached|limit.*reached|enough.*relevant|relevant.*count/.test(
      reason,
    )
  );
}

function sortJobs(jobs: Job[], sortBy: SortOption) {
  return [...jobs].sort((a, b) => {
    const scoreA = a.match_score ?? 0;
    const scoreB = b.match_score ?? 0;

    switch (sortBy) {
      case 'match-high':
        return scoreB - scoreA;

      case 'match-low':
        return scoreA - scoreB;

      case 'title-az':
        return (a.title ?? '').localeCompare(b.title ?? '');

      case 'title-za':
        return (b.title ?? '').localeCompare(a.title ?? '');

      case 'company-az':
        return (a.company ?? '').localeCompare(b.company ?? '');

      case 'company-za':
        return (b.company ?? '').localeCompare(a.company ?? '');

      case 'location-az':
        return (a.location ?? '').localeCompare(b.location ?? '');

      case 'location-za':
        return (b.location ?? '').localeCompare(a.location ?? '');

      default:
        return 0;
    }
  });
}

function Card({ job }: { job: Job }) {
  return (
    <a
      href={job.external_url}
      target="_blank"
      rel="noreferrer"
      className="block rounded-2xl border border-[#EBCDAA] bg-[#FFF9F0] p-5 transition hover:border-orange hover:shadow-soft"
    >
      <div className="flex items-start gap-3">
        <BriefcaseBusiness className="mt-1 text-orange" />

        <div className="min-w-0 flex-1">
          <div className="flex justify-between gap-3">
            <h3 className="truncate font-black">{job.title}</h3>

            <b
              className={`rounded-full px-2 py-1 text-[10px] ${
                job.ai_status === 'ACCEPTED'
                  ? 'bg-[#DDF4D5] text-[#347233]'
                  : 'bg-[#FFE1D6] text-[#AD3212]'
              }`}
            >
              {job.match_score?.toFixed(0) ?? 0}% match
            </b>
          </div>

          <p className="mt-1 text-sm text-[#865D42]">
            {job.company} · {job.location}
          </p>

          <p className="mt-3 text-sm text-[#875D42]">{job.ai_reason}</p>
        </div>
      </div>
    </a>
  );
}

export function JobSearch({ token }: { token: string }) {
  const [term, setTerm] = useState('');
  const [source, setSource] = useState('linkedin');
  const [location, setLocation] = useState('India');
  const [indeedCountry, setIndeedCountry] = useState('India');
  const [experience, setExperience] = useState('0-1 years');
  const [salary, setSalary] = useState('');
  const [wanted, setWanted] = useState('10');
  const [accepted, setAccepted] = useState<Job[]>([]);
  const [rejected, setRejected] = useState<Job[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [hasSearched, setHasSearched] = useState(false);
  const [lastSearchSource, setLastSearchSource] = useState('');
  const [showRejected, setShowRejected] = useState(false);
  const [sortBy, setSortBy] = useState<SortOption>('match-high');

  useEffect(() => {
    api
      .jobs(token)
      .then(items => {
        setAccepted(items.filter(job => job.ai_status === 'ACCEPTED'));
        setRejected(items.filter(job => job.ai_status === 'REJECTED'));
      })
      .catch(() => undefined);
  }, [token]);

  async function search(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError('');

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
        ...(source === 'indeed' ? { indeed_country: indeedCountry } : {}),
      });

      setAccepted(result.accepted_jobs);
      setRejected(result.rejected_jobs);
      setLastSearchSource(source);
      setHasSearched(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Search failed.');
    } finally {
      setBusy(false);
    }
  }

  const sortedJobs = useMemo(() => {
    return sortJobs(
      showRejected
        ? rejected
        : accepted.filter(job => !isRelevantButTargetReached(job)),
      sortBy,
    );
  }, [accepted, rejected, showRejected, sortBy]);

  const alsoRelevantJobs = useMemo(() => {
    return sortJobs(accepted.filter(isRelevantButTargetReached), sortBy);
  }, [accepted, sortBy]);

  return (
    <main className="min-h-screen bg-cream px-5 py-9">
      <section className="mx-auto max-w-6xl">
        <p className="text-xs font-black uppercase tracking-[.16em] text-orange">
          AI job matcher
        </p>

        <h1 className="mt-2 text-4xl font-black">Find the jobs that fit.</h1>

        <form
          onSubmit={search}
          className="mt-6 grid gap-3 rounded-[2rem] bg-ink p-5 sm:grid-cols-2 lg:grid-cols-3"
        >
          <Input
            required
            value={term}
            onChange={e => setTerm(e.target.value)}
            placeholder="Role, e.g. Backend Developer"
          />

          <select
            value={source}
            onChange={e => setSource(e.target.value)}
            className="rounded-2xl bg-[#FFF9F0] px-4 text-sm font-bold"
          >
            <option value="linkedin">LinkedIn</option>
            <option value="indeed">Indeed</option>
            <option value="naukri">Naukri</option>
          </select>

          <div className="flex overflow-hidden rounded-2xl bg-[#FFF9F0]">
            <Input
              value={location}
              onChange={e => setLocation(e.target.value)}
              placeholder="Location"
              className="rounded-none border-0 bg-transparent"
            />

            {source === 'indeed' && (
              <select
                aria-label="Indeed country"
                value={indeedCountry}
                onChange={e => setIndeedCountry(e.target.value)}
                className="border-l border-[#E7BD91] bg-transparent px-3 text-sm font-bold text-ink outline-none"
              >
                <option value="India">India</option>
                <option value="USA">USA</option>
                <option value="UK">UK</option>
                <option value="Canada">Canada</option>
                <option value="Australia">Australia</option>
                <option value="Germany">Germany</option>
                <option value="France">France</option>
                <option value="Singapore">Singapore</option>
                <option value="United Arab Emirates">UAE</option>
              </select>
            )}
          </div>

          <Input
            value={experience}
            onChange={e => setExperience(e.target.value)}
            placeholder="Experience, e.g. 0-1 years"
          />

          <Input
            type="number"
            value={salary}
            onChange={e => setSalary(e.target.value)}
            placeholder="Expected salary (LPA)"
          />

          <Input
            type="number"
            min="1"
            max="50"
            value={wanted}
            onChange={e => setWanted(e.target.value)}
            placeholder="Relevant jobs wanted"
          />

          <Button className="sm:col-span-2 lg:col-span-3" disabled={busy}>
            {busy ? (
              <LoaderCircle className="animate-spin" />
            ) : (
              <Search size={18} />
            )}
            Score and find jobs
          </Button>
        </form>

        {error && (
          <p className="mt-4 rounded-xl bg-[#FFE1D6] p-4 font-bold text-[#AD3212]">
            {error}
          </p>
        )}

        <div className="mt-8 flex flex-col justify-between gap-4 sm:flex-row sm:items-center">
          <div className="flex gap-3">
            <button
              onClick={() => setShowRejected(false)}
              className={`rounded-full px-4 py-2 text-sm font-bold ${
                !showRejected ? 'bg-orange text-white' : 'bg-[#FFE4C8]'
              }`}
            >
              Accepted ({accepted.length})
            </button>

            <button
              onClick={() => setShowRejected(true)}
              className={`rounded-full px-4 py-2 text-sm font-bold ${
                showRejected
                  ? 'bg-[#A54222] text-white'
                  : 'bg-[#FFE4C8]'
              }`}
            >
              Rejected ({rejected.length})
            </button>
          </div>

          <label className="flex items-center gap-2 text-sm font-bold text-[#805940]">
            <SlidersHorizontal size={17} className="text-orange" />

            <span className="sr-only">Sort jobs</span>

            <select
              value={sortBy}
              onChange={e => setSortBy(e.target.value as SortOption)}
              className="rounded-xl border border-[#E8C9A4] bg-[#FFF9F0] px-3 py-2 text-sm font-bold text-[#4E2B19] outline-none focus:border-orange"
            >
              <option value="match-high">Match percentage: High to low</option>
              <option value="match-low">Match percentage: Low to high</option>
              <option value="title-az">Job title: A–Z</option>
              <option value="title-za">Job title: Z–A</option>
              <option value="company-az">Company: A–Z</option>
              <option value="company-za">Company: Z–A</option>
              <option value="location-az">Location: A–Z</option>
              <option value="location-za">Location: Z–A</option>
            </select>

            <ArrowDownAZ size={17} className="text-orange" />
          </label>
        </div>

        <div className="mt-5 grid gap-3">
          {sortedJobs.map((job, index) => (
            <Card key={job.id ?? index} job={job} />
          ))}

          {!sortedJobs.length && (
            <p className="rounded-2xl border border-dashed border-[#E5BB91] p-10 text-center text-[#875D42]">
              {hasSearched
                ? `No jobs were returned by ${lastSearchSource} for this search. Try a broader role, location, or date range.`
                : 'Search to populate this board.'}
            </p>
          )}
        </div>

        {!showRejected && alsoRelevantJobs.length > 0 && (
          <section className="mt-10">
            <div className="mb-4 flex items-start gap-3">
              <BriefcaseBusiness className="mt-1 text-orange" size={20} />

              <div>
                <p className="text-xs font-black uppercase tracking-[.16em] text-orange">
                  More matches
                </p>

                <h2 className="mt-1 text-2xl font-black">Also relevant</h2>

                <p className="mt-1 text-sm text-[#875D42]">
                  These jobs also match your profile. They are accepted, and
                  appear here because the requested number of relevant jobs
                  had already been reached.
                </p>
              </div>
            </div>

            <div className="grid gap-3">
              {alsoRelevantJobs.map((job, index) => (
                <Card key={`also-relevant-${job.id ?? index}`} job={job} />
              ))}
            </div>
          </section>
        )}
      </section>
    </main>
  );
}
