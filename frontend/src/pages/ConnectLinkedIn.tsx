import { useEffect, useRef, useState } from 'react';
import { ArrowRight, CheckCircle, ExternalLink, KeyRound, Link2, LoaderCircle, LogOut } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Button } from '../components/ui/button';
import { api, type LinkedInSession } from '../lib/api';

const POLL_MS = 2000;

/**
 * Mandatory step before applying: opens a real LinkedIn window (driven by the backend),
 * the user signs in once (2FA / CAPTCHA included) and that window is reused for every application.
 */
export function ConnectLinkedIn({ token, onConnected }: { token: string; onConnected: () => void }) {
  const [session, setSession] = useState<LinkedInSession | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const timer = useRef<number | null>(null);

  const stopPolling = () => { if (timer.current) { window.clearInterval(timer.current); timer.current = null; } };
  const startPolling = () => {
    stopPolling();
    timer.current = window.setInterval(async () => {
      try {
        const next = await api.linkedinSession(token);
        setSession(next);
        if (next.status !== 'LOGIN_IN_PROGRESS') stopPolling();
      } catch { /* keep polling */ }
    }, POLL_MS);
  };

  useEffect(() => {
    api.linkedinSession(token)
      .then(current => { setSession(current); if (current.status === 'LOGIN_IN_PROGRESS') startPolling(); })
      .catch(e => setError(e instanceof Error ? e.message : 'Could not load LinkedIn status.'));
    return stopPolling;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  async function connect() {
    setBusy(true); setError('');
    try { setSession(await api.connectLinkedin(token)); startPolling(); }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not open the LinkedIn window.'); }
    finally { setBusy(false); }
  }

  async function disconnect() {
    setBusy(true); setError('');
    try { setSession(await api.disconnectLinkedin(token)); }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not disconnect LinkedIn.'); }
    finally { setBusy(false); }
  }

  const status = session?.status ?? 'NEEDS_LOGIN';
  const waiting = status === 'LOGIN_IN_PROGRESS';
  const ready = status === 'READY';

  return <main className="min-h-screen bg-cream px-5 py-12">
    <section className="mx-auto max-w-2xl rounded-[2rem] border border-[#EAC79F] bg-[#FFF9F0] p-7 shadow-soft sm:p-10">
      <div className="grid h-14 w-14 place-items-center rounded-2xl bg-[#FFE0BB] text-orange"><Link2 size={27} /></div>
      <p className="mt-5 text-xs font-black uppercase tracking-[.16em] text-orange">Required step · Connect LinkedIn</p>
      <h1 className="mt-2 text-4xl font-black">Sign in to LinkedIn once.</h1>
      <p className="mt-3 text-[#805940]">We open a real LinkedIn browser window on this computer. Sign in there (finish any 2FA or “verify it’s you” check). The window stays signed in and every application runs inside it, so there are no repeated logins.</p>

      <ol className="mt-6 space-y-2 text-sm font-bold text-[#6B4329]">
        <li>1. Click <b>Open LinkedIn login</b> — a browser window pops up.</li>
        <li>2. Sign in. {session?.credentials.password_set ? 'Your saved credentials are pre-filled.' : <>Optional: <Link to="/profile" className="text-orange underline">save credentials</Link> to pre-fill them.</>}</li>
        <li>3. Keep that window open (minimising is fine). This page turns green automatically.</li>
      </ol>

      <div className={`mt-7 flex items-center gap-3 rounded-2xl px-4 py-3 text-sm font-bold ${ready ? 'bg-[#E3F7D9] text-[#347233]' : waiting ? 'bg-[#FFF0DE] text-[#92502A]' : 'bg-[#FFE1D6] text-[#AD3212]'}`}>
        {ready ? <CheckCircle size={18} /> : waiting ? <LoaderCircle className="animate-spin" size={18} /> : <KeyRound size={18} />}
        {ready ? 'LinkedIn is connected.' : waiting ? 'Waiting for you to finish signing in in the LinkedIn window…' : 'LinkedIn is not connected yet.'}
      </div>
      {session?.error && !ready && !waiting && <p className="mt-3 rounded-xl bg-[#FFE1D6] px-4 py-3 text-sm font-bold text-[#AD3212]">{session.error}</p>}
      {error && <p className="mt-3 rounded-xl bg-[#FFE1D6] px-4 py-3 text-sm font-bold text-[#AD3212]">{error}</p>}

      <div className="mt-7 flex flex-wrap gap-3">
        {ready ? <>
          <Button size="lg" onClick={onConnected}>Continue to jobs <ArrowRight size={18} /></Button>
          <Button size="lg" variant="ghost" onClick={disconnect} disabled={busy}><LogOut size={16} /> Disconnect</Button>
        </> : <Button size="lg" onClick={connect} disabled={busy || waiting}>
          {busy || waiting ? <LoaderCircle className="animate-spin" size={18} /> : <ExternalLink size={18} />}
          {waiting ? 'Waiting for LinkedIn login…' : session?.error ? 'Try again' : 'Open LinkedIn login'}
        </Button>}
      </div>
    </section>
  </main>;
}
