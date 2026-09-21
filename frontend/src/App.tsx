import { useEffect, useState, type FormEvent } from 'react';
import { ArrowRight, Check, Flame, LoaderCircle, Sparkles } from 'lucide-react';
import { BrowserRouter, Navigate, Route, Routes, useNavigate } from 'react-router-dom';
import { AppNavbar } from './components/layout/AppNavbar';
import { Button } from './components/ui/button';
import { Input } from './components/ui/input';
import { api, type User } from './lib/api';
import { JobSearch } from './pages/JobSearch';
import { ProfileReview } from './pages/ProfileReview';
import { ResumeOnboarding } from './pages/ResumeOnboarding';
import { Workflow } from './pages/Workflow';

type Notice = { kind: 'error' | 'success'; text: string } | null;

export default function App() {
  return <BrowserRouter><AppRoutes /></BrowserRouter>;
}

function AppRoutes() {
  const navigate = useNavigate();
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('jobflow_token'));
  const [user, setUser] = useState<User | null>(null);
  const [checking, setChecking] = useState(Boolean(token));

  useEffect(() => {
    if (!token) { setChecking(false); return; }
    setChecking(true);
    Promise.all([api.me(token), api.resumeStatus(token), api.profile(token)])
      .then(([me, resume, profile]) => {
        setUser(me);
        if (!resume.has_resume || !profile.markdown) navigate('/onboarding', { replace: true });
      })
      .catch(() => { localStorage.removeItem('jobflow_token'); setToken(null); setUser(null); navigate('/', { replace: true }); })
      .finally(() => setChecking(false));
  }, [token, navigate]);

  const completeAuth = (result: { access_token: string; user: User }, isSignup: boolean) => {
    localStorage.setItem('jobflow_token', result.access_token);
    setUser(result.user);
    setToken(result.access_token);
    navigate(isSignup ? '/onboarding' : '/');
  };
  const logout = () => { localStorage.removeItem('jobflow_token'); setToken(null); setUser(null); navigate('/'); };

  if (checking) return <Loading />;
  if (!token || !user) return <Auth onDone={completeAuth} />;

  return <><AppNavbar user={user} onLogout={logout} /><Routes>
    <Route path="/" element={<JobSearch token={token} />} />
    <Route path="/workflow" element={<Workflow token={token} />} />
    <Route path="/profile" element={<ProfileReview token={token} onDone={() => navigate('/')} />} />
    <Route path="/onboarding" element={<ResumeOnboarding token={token} onDone={() => navigate('/profile')} />} />
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes></>;
}

function Brand() { return <div className="flex items-center gap-3"><div className="grid h-11 w-11 place-items-center rounded-2xl bg-orange text-white shadow-[0_6px_0_#C94F12]"><Flame size={22} fill="white" /></div><span className="text-xl font-black tracking-tight text-ink">Niko<span className="text-orange">Seek</span></span></div>; }
function Loading() { return <div className="grid min-h-screen place-items-center bg-cream font-bold text-[#80502F]"><span className="flex items-center gap-3"><LoaderCircle className="animate-spin text-orange" />Gathering your workspace…</span></div>; }
function Alert({ notice }: { notice: NonNullable<Notice> }) { return <div className={`mt-5 rounded-xl px-4 py-3 text-sm font-bold ${notice.kind === 'error' ? 'bg-[#FFE1D6] text-[#AD3212]' : 'bg-[#E3F7D9] text-[#347233]'}`}>{notice.text}</div>; }

function Auth({ onDone }: { onDone: (result: { access_token: string; user: User }, isSignup: boolean) => void }) {
  const [signup, setSignup] = useState(false);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setNotice(null);
    try { onDone(signup ? await api.signup(username, password) : await api.login(username, password), signup); }
    catch (error) { setNotice({ kind: 'error', text: error instanceof Error ? error.message : 'Could not continue.' }); }
    finally { setBusy(false); }
  }
  return <main className="relative min-h-screen overflow-hidden bg-cream px-5 py-6 text-ink sm:p-9"><div className="orange-orb left-[-100px] top-[-100px]" /><div className="orange-orb bottom-[-160px] right-[-100px]" /><nav className="relative mx-auto flex max-w-6xl items-center justify-between"><Brand /><span className="rounded-full bg-[#FFE7CB] px-4 py-2 text-xs font-bold text-[#92502A]">Career autopilot, slowly ✦</span></nav><section className="relative mx-auto grid min-h-[calc(100vh-105px)] max-w-6xl items-center gap-12 py-10 lg:grid-cols-[1.05fr_.8fr]"><div className="max-w-xl"><div className="mb-5 inline-flex items-center gap-2 rounded-full border border-[#F4C596] bg-[#FFF4E7] px-3 py-1.5 text-xs font-extrabold uppercase tracking-wider text-[#B85019]"><Sparkles size={14} /> Your next role starts here</div><h1 className="font-display text-5xl font-black leading-[.92] tracking-[-.06em] sm:text-7xl">Less job hunt.<br /><span className="text-orange">More main character</span> energy.</h1><p className="mt-7 max-w-md text-lg leading-relaxed text-[#754C32]">Search fresh roles, manage your profile, and configure your job workflow.</p><div className="mt-9 flex gap-5 text-sm font-bold text-[#80502F]"><span className="flex items-center gap-2"><b className="grid h-6 w-6 place-items-center rounded-full bg-orange text-white"><Check size={15} /></b>No noise</span><span className="flex items-center gap-2"><b className="grid h-6 w-6 place-items-center rounded-full bg-orange text-white"><Check size={15} /></b>Your files, private</span></div></div><form onSubmit={submit} className="rounded-[2rem] border border-[#EAC79F] bg-[#FFF9F0]/90 p-6 shadow-soft backdrop-blur sm:p-9"><p className="text-sm font-extrabold uppercase tracking-[.17em] text-orange">{signup ? 'Start your flow' : 'Welcome back'}</p><h2 className="mt-2 text-3xl font-black tracking-tight">{signup ? 'Let’s get you hired.' : 'Pick up where you left off.'}</h2><div className="mt-7 space-y-4"><label className="block text-sm font-bold">Username<Input required minLength={3} value={username} onChange={e => setUsername(e.target.value)} placeholder="e.g. nishant.codes" className="mt-2" /></label><label className="block text-sm font-bold">Password<Input required minLength={8} type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder="8+ characters" className="mt-2" /></label></div>{notice && <Alert notice={notice} />}<Button className="mt-7 w-full" size="lg" disabled={busy}>{busy && <LoaderCircle className="animate-spin" size={18} />}{signup ? 'Create my space' : 'Enter NikoSeek'}<ArrowRight size={18} /></Button><p className="mt-7 text-center text-sm text-[#865D42]">{signup ? 'Already building your flow?' : 'New around here?'} <button type="button" className="font-extrabold text-orange hover:underline" onClick={() => { setSignup(!signup); setNotice(null); }}>{signup ? 'Log in' : 'Create an account'}</button></p></form></section></main>;
}
