import { Fragment, useEffect, useRef, useState } from 'react';
import { ArrowRight, CheckCircle, ExternalLink, Eye, KeyRound, LoaderCircle, LogOut, PencilLine } from 'lucide-react';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { api, type LinkedInSession } from '../lib/api';

function InlineMarkdown({ value }: { value: string }) {
  return <>{value.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**')) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (part.startsWith('`') && part.endsWith('`')) return <code key={index} className="rounded bg-[#F5E9DA] px-1 py-0.5 font-mono text-[.9em]">{part.slice(1, -1)}</code>;
    return <Fragment key={index}>{part}</Fragment>;
  })}</>;
}

function MarkdownPreview({ markdown }: { markdown: string }) {
  return <div className="min-h-[420px] overflow-auto rounded-2xl border border-[#E8C9A4] bg-[#FFFDF8] p-5 text-sm leading-relaxed text-[#4E2B19]">
    {markdown.split('\n').map((line, index) => {
      if (line.startsWith('### ')) return <h3 key={index} className="mt-5 text-base font-black first:mt-0"><InlineMarkdown value={line.slice(4)} /></h3>;
      if (line.startsWith('## ')) return <h2 key={index} className="mt-6 text-xl font-black first:mt-0"><InlineMarkdown value={line.slice(3)} /></h2>;
      if (line.startsWith('# ')) return <h1 key={index} className="text-2xl font-black first:mt-0"><InlineMarkdown value={line.slice(2)} /></h1>;
      if (/^[-*] /.test(line)) return <div key={index} className="flex gap-2"><span className="text-orange">•</span><span><InlineMarkdown value={line.slice(2)} /></span></div>;
      if (!line.trim()) return <div key={index} className="h-3" />;
      return <p key={index}><InlineMarkdown value={line} /></p>;
    })}
  </div>;
}

export function ProfileReview({ token, onDone, onLinkedinChange }: { token: string; onDone: () => void; onLinkedinChange?: (connected: boolean) => void }) {
  const [markdown, setMarkdown] = useState('');
  const [linkedinEmail, setLinkedinEmail] = useState('');
  const [linkedinPassword, setLinkedinPassword] = useState('');
  const [passwordSet, setPasswordSet] = useState(false);
  const [editingPassword, setEditingPassword] = useState(false);
  const [busy, setBusy] = useState(true);
  const [savingLinkedin, setSavingLinkedin] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [session, setSession] = useState<LinkedInSession | null>(null);
  const [sessionBusy, setSessionBusy] = useState(false);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const poll = useRef<number | null>(null);

  const applySession = (next: LinkedInSession) => {
    setSession(next);
    onLinkedinChange?.(next.status === 'READY');
    if (next.status !== 'LOGIN_IN_PROGRESS' && poll.current) { window.clearInterval(poll.current); poll.current = null; }
  };
  const startPolling = () => {
    if (poll.current) window.clearInterval(poll.current);
    poll.current = window.setInterval(() => { api.linkedinSession(token).then(applySession).catch(() => undefined); }, 2000);
  };

  useEffect(() => {
    api.linkedinSession(token).then(next => { applySession(next); if (next.status === 'LOGIN_IN_PROGRESS') startPolling(); }).catch(() => undefined);
    return () => { if (poll.current) window.clearInterval(poll.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  async function connectLinkedin() {
    setSessionBusy(true); setError('');
    try { applySession(await api.connectLinkedin(token)); startPolling(); }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not open the LinkedIn window.'); }
    finally { setSessionBusy(false); }
  }

  async function disconnectLinkedin() {
    setSessionBusy(true); setError(''); setConfirmDisconnect(false);
    try { applySession(await api.disconnectLinkedin(token)); setNotice('LinkedIn disconnected. Connect again before applying to jobs.'); }
    catch (e) { setError(e instanceof Error ? e.message : 'Could not disconnect LinkedIn.'); }
    finally { setSessionBusy(false); }
  }

  useEffect(() => {
    Promise.all([api.profile(token), api.linkedinCredentials(token)])
      .then(([profile, linkedin]) => {
        setMarkdown(profile.markdown ?? '');
        setLinkedinEmail(linkedin.email);
        setPasswordSet(linkedin.password_set);
        setEditingPassword(!linkedin.password_set);
      })
      .catch(e => setError(e.message))
      .finally(() => setBusy(false));
  }, [token]);

  async function save() {
    setBusy(true);
    setError('');
    try {
      await api.saveProfile(token, markdown);
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save profile.');
    } finally {
      setBusy(false);
    }
  }

  async function saveLinkedin() {
    setSavingLinkedin(true);
    setError('');
    setNotice('');
    try {
      const result = await api.saveLinkedinCredentials(token, {
        email: linkedinEmail,
        password: editingPassword && linkedinPassword ? linkedinPassword : undefined,
      });
      setLinkedinEmail(result.email);
      setPasswordSet(result.password_set);
      setLinkedinPassword('');
      setEditingPassword(!result.password_set);
      setNotice('LinkedIn credentials saved.');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save LinkedIn credentials.');
    } finally {
      setSavingLinkedin(false);
    }
  }

  return (
    <main className="min-h-screen bg-cream px-5 py-12">
      <section className="mx-auto max-w-6xl rounded-[2rem] border border-[#EAC79F] bg-[#FFF9F0] p-7 shadow-soft sm:p-10">
        <PencilLine className="text-orange" />
        <p className="mt-5 text-xs font-black uppercase tracking-[.16em] text-orange">Step 3 · Review profile</p>
        <h1 className="mt-2 text-4xl font-black">Make it sound like you.</h1>
        <p className="mt-3 text-[#805940]">Edit the Markdown on the left and verify the live formatting preview on the right before saving.</p>
        <div className="mt-7 grid gap-5 lg:grid-cols-2">
          <div>
            <div className="mb-2 flex items-center gap-2 text-xs font-black uppercase tracking-[.14em] text-[#895B3E]"><PencilLine size={14} /> Markdown editor</div>
            <textarea value={markdown} onChange={e => setMarkdown(e.target.value)} disabled={busy} spellCheck={false} className="min-h-[420px] w-full rounded-2xl border border-[#E8C9A4] bg-[#FFFDF8] p-5 font-mono text-sm leading-relaxed outline-none focus:border-orange disabled:cursor-not-allowed" />
          </div>
          <div>
            <div className="mb-2 flex items-center gap-2 text-xs font-black uppercase tracking-[.14em] text-[#895B3E]"><Eye size={14} /> Live preview</div>
            <MarkdownPreview markdown={markdown} />
          </div>
        </div>
        <div className="mt-8 rounded-[1.75rem] border border-[#E8C9A4] bg-[#FFFDF8] p-5">
          <div className="flex items-center gap-2">
            <KeyRound className="text-orange" size={18} />
            <h2 className="text-xl font-black">LinkedIn login</h2>
          </div>
          <div className={`mt-4 flex flex-wrap items-center gap-3 rounded-2xl px-4 py-3 text-sm font-bold ${session?.status === 'READY' ? 'bg-[#E3F7D9] text-[#347233]' : session?.status === 'LOGIN_IN_PROGRESS' ? 'bg-[#FFF0DE] text-[#92502A]' : 'bg-[#FFE1D6] text-[#AD3212]'}`}>
            {session?.status === 'READY' ? <CheckCircle size={18} /> : session?.status === 'LOGIN_IN_PROGRESS' ? <LoaderCircle className="animate-spin" size={18} /> : <KeyRound size={18} />}
            <span className="flex-1">
              {session?.status === 'READY'
                ? <>LinkedIn connected{session.last_login_at ? <span className="font-normal"> · since {new Date(session.last_login_at).toLocaleString()}</span> : null}</>
                : session?.status === 'LOGIN_IN_PROGRESS' ? 'Waiting for you to finish signing in in the LinkedIn window…' : 'LinkedIn is not connected — applying is disabled until you connect.'}
            </span>
            {session?.status === 'READY' ? (
              confirmDisconnect ? <>
                <Button type="button" size="sm" variant="secondary" onClick={disconnectLinkedin} disabled={sessionBusy}>{sessionBusy ? <LoaderCircle className="animate-spin" size={14} /> : <LogOut size={14} />} Yes, disconnect</Button>
                <Button type="button" size="sm" variant="ghost" onClick={() => setConfirmDisconnect(false)}>Cancel</Button>
              </> : <Button type="button" size="sm" variant="secondary" onClick={() => setConfirmDisconnect(true)}><LogOut size={14} /> Disconnect</Button>
            ) : (
              <Button type="button" size="sm" onClick={connectLinkedin} disabled={sessionBusy || session?.status === 'LOGIN_IN_PROGRESS'}>
                {sessionBusy || session?.status === 'LOGIN_IN_PROGRESS' ? <LoaderCircle className="animate-spin" size={14} /> : <ExternalLink size={14} />} Connect LinkedIn
              </Button>
            )}
          </div>
          {session?.error && session.status === 'NEEDS_LOGIN' && <p className="mt-2 text-xs font-bold text-[#AD3212]">{session.error}</p>}
          <p className="mt-3 text-sm text-[#805940]">Optional: save credentials so the login window is pre-filled. The password is encrypted in your account config and never sent back to this page.</p>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <label className="block text-sm font-bold">LinkedIn email
              <Input className="mt-2 bg-white" value={linkedinEmail} onChange={e => setLinkedinEmail(e.target.value)} placeholder="you@email.com" autoComplete="username" />
            </label>
            <label className="block text-sm font-bold">LinkedIn password
              <Input
                className="mt-2 bg-white"
                type="password"
                value={linkedinPassword}
                onChange={e => setLinkedinPassword(e.target.value)}
                disabled={!editingPassword}
                placeholder={passwordSet && !editingPassword ? '••••••••' : 'Type a new password'}
                autoComplete="new-password"
              />
            </label>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            {passwordSet && !editingPassword && (
              <Button type="button" variant="secondary" onClick={() => { setEditingPassword(true); setLinkedinPassword(''); }}>
                Edit password
              </Button>
            )}
            {editingPassword && passwordSet && (
              <Button type="button" variant="ghost" onClick={() => { setEditingPassword(false); setLinkedinPassword(''); }}>
                Cancel
              </Button>
            )}
            <Button type="button" onClick={saveLinkedin} disabled={savingLinkedin || !linkedinEmail}>
              {savingLinkedin ? <LoaderCircle className="animate-spin" size={16} /> : null}
              Save LinkedIn credentials
            </Button>
          </div>
        </div>
        {notice && <p className="mt-4 rounded-xl bg-[#E3F7D9] p-3 font-bold text-[#347233]">{notice}</p>}
        {error && <p className="mt-4 rounded-xl bg-[#FFE1D6] p-3 font-bold text-[#AD3212]">{error}</p>}
        <Button size="lg" className="mt-6 w-full" onClick={save} disabled={busy}>{busy ? <LoaderCircle className="animate-spin" /> : <>Save profile & find jobs <ArrowRight size={18} /></>}</Button>
      </section>
    </main>
  );
}
