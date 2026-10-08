import { useEffect, useRef, useState } from 'react';
import { ArrowRight, CheckCircle, ExternalLink, KeyRound, Link2, LoaderCircle, LogOut } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Button } from '../components/ui/button';
import { api, type IndeedSession } from '../lib/api';

/** A dedicated Indeed session; it never shares LinkedIn's browser profile. */
export function ConnectIndeed({ token, onConnected }: { token: string; onConnected: () => void }) {
  const [session, setSession] = useState<IndeedSession | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const timer = useRef<number | null>(null);
  const stopPolling = () => { if (timer.current) { window.clearInterval(timer.current); timer.current = null; } };
  const startPolling = () => { stopPolling(); timer.current = window.setInterval(async () => {
    try { const next = await api.indeedSession(token); setSession(next); if (next.status !== 'LOGIN_IN_PROGRESS') stopPolling(); } catch { /* keep polling */ }
  }, 2000); };

  useEffect(() => {
    api.indeedSession(token).then(current => { setSession(current); if (current.status === 'LOGIN_IN_PROGRESS') startPolling(); })
      .catch(e => setError(e instanceof Error ? e.message : 'Could not load Indeed status.'));
    return stopPolling;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);
  async function connect() { setBusy(true); setError(''); try { setSession(await api.connectIndeed(token)); startPolling(); } catch (e) { setError(e instanceof Error ? e.message : 'Could not open the Indeed window.'); } finally { setBusy(false); } }
  async function disconnect() { setBusy(true); setError(''); try { setSession(await api.disconnectIndeed(token)); } catch (e) { setError(e instanceof Error ? e.message : 'Could not disconnect Indeed.'); } finally { setBusy(false); } }
  const status = session?.status ?? 'NEEDS_LOGIN'; const waiting = status === 'LOGIN_IN_PROGRESS'; const ready = status === 'READY';

  return <main className="min-h-screen bg-cream px-5 py-12"><section className="mx-auto max-w-2xl rounded-[2rem] border border-[#EAC79F] bg-[#FFF9F0] p-7 shadow-soft sm:p-10">
    <div className="grid h-14 w-14 place-items-center rounded-2xl bg-[#FFE0BB] text-orange"><Link2 size={27} /></div>
    <p className="mt-5 text-xs font-black uppercase tracking-[.16em] text-orange">Required step · Connect Indeed</p><h1 className="mt-2 text-4xl font-black">Sign in to Indeed once.</h1>
    <p className="mt-3 text-[#805940]">We open a separate real Indeed browser window on this computer. Complete sign-in and any verification there; this window is reused for your Indeed applications.</p>
    <ol className="mt-6 space-y-2 text-sm font-bold text-[#6B4329]"><li>1. Click <b>Open Indeed login</b> — a browser window pops up.</li><li>2. We find your Indeed account with your saved email, then select <b>Use a passkey</b>. Approve the passkey prompt from your browser or device. <Link to="/profile" className="text-orange underline">Save your email</Link> first if needed.</li><li>3. Keep that window open (minimising is fine). This page turns green automatically.</li></ol>
    <div className={`mt-7 flex items-center gap-3 rounded-2xl px-4 py-3 text-sm font-bold ${ready ? 'bg-[#E3F7D9] text-[#347233]' : waiting ? 'bg-[#FFF0DE] text-[#92502A]' : 'bg-[#FFE1D6] text-[#AD3212]'}`}>{ready ? <CheckCircle size={18} /> : waiting ? <LoaderCircle className="animate-spin" size={18} /> : <KeyRound size={18} />}{ready ? 'Indeed is connected.' : waiting ? 'Waiting for you to finish signing in in the Indeed window…' : 'Indeed is not connected yet.'}</div>
    {session?.error && !ready && !waiting && <p className="mt-3 rounded-xl bg-[#FFE1D6] px-4 py-3 text-sm font-bold text-[#AD3212]">{session.error}</p>}{error && <p className="mt-3 rounded-xl bg-[#FFE1D6] px-4 py-3 text-sm font-bold text-[#AD3212]">{error}</p>}
    <div className="mt-7 flex flex-wrap gap-3">{ready ? <><Button size="lg" onClick={onConnected}>Continue to jobs <ArrowRight size={18} /></Button><Button size="lg" variant="ghost" onClick={disconnect} disabled={busy}><LogOut size={16} /> Disconnect</Button></> : <Button size="lg" onClick={connect} disabled={busy || waiting}>{busy || waiting ? <LoaderCircle className="animate-spin" size={18} /> : <ExternalLink size={18} />}{waiting ? 'Waiting for Indeed login…' : session?.error ? 'Try again' : 'Open Indeed login'}</Button>}</div>
  </section></main>;
}
