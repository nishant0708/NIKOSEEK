import { useEffect, useState } from 'react';
import { Bot, LoaderCircle, RotateCcw, Save } from 'lucide-react';
import { Button } from '../ui/button';
import { api, type AgentPrompt } from '../../lib/api';

/** Edit the prompts the live apply agent uses (navigation, correct-page check, form filling). */
export function AgentPromptsEditor({ token }: { token: string }) {
  const [prompts, setPrompts] = useState<AgentPrompt[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [message, setMessage] = useState<{ key: string; kind: 'ok' | 'error'; text: string } | null>(null);

  const load = (items: AgentPrompt[]) => { setPrompts(items); setDrafts(Object.fromEntries(items.map(item => [item.key, item.content]))); };
  useEffect(() => { api.agentPrompts(token).then(load).catch(e => setMessage({ key: '', kind: 'error', text: e instanceof Error ? e.message : 'Could not load prompts.' })); }, [token]);

  async function save(key: string) {
    setBusyKey(key); setMessage(null);
    try { load(await api.saveAgentPrompt(token, key, drafts[key] ?? '')); setMessage({ key, kind: 'ok', text: 'Saved. Used from the next application.' }); }
    catch (e) { setMessage({ key, kind: 'error', text: e instanceof Error ? e.message : 'Could not save.' }); }
    finally { setBusyKey(null); }
  }
  async function reset(key: string) {
    setBusyKey(key); setMessage(null);
    try { load(await api.resetAgentPrompt(token, key)); setMessage({ key, kind: 'ok', text: 'Reset to the default prompt.' }); }
    catch (e) { setMessage({ key, kind: 'error', text: e instanceof Error ? e.message : 'Could not reset.' }); }
    finally { setBusyKey(null); }
  }

  return (
    <section className="mt-7 rounded-[2rem] border border-[#EBCDAA] bg-[#FFF9F0] p-6 sm:p-8">
      <div className="flex items-center gap-2"><Bot className="text-orange" size={20} /><h2 className="text-xl font-black">Apply agent prompts</h2></div>
      <p className="mt-2 max-w-3xl text-sm text-[#805940]">These prompts drive the live apply worker. Placeholders like <code className="rounded bg-[#F5E9DA] px-1">{'{{job_title}}'}</code> are replaced at run time and must stay in the prompt. Keep the JSON reply format so the agent can read the answer.</p>
      {message && !message.key && <p className="mt-3 rounded-xl bg-[#FFE1D6] p-3 text-sm font-bold text-[#AD3212]">{message.text}</p>}
      <div className="mt-6 space-y-6">
        {prompts.map(prompt => {
          const dirty = (drafts[prompt.key] ?? '') !== prompt.content;
          return (
            <div key={prompt.key} id={`prompt-${prompt.key}`} className="scroll-mt-24 rounded-2xl border border-[#E8C9A4] bg-white p-5">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-lg font-black text-ink">{prompt.title}</h3>
                <span className={`rounded-full px-2 py-0.5 text-[10px] font-black uppercase tracking-[.12em] ${prompt.customized ? 'bg-[#FFE4C8] text-[#82512D]' : 'bg-[#F0EBE4] text-[#805940]'}`}>{prompt.customized ? 'Customized' : 'Default'}</span>
                {dirty && <span className="rounded-full bg-[#FFF0DE] px-2 py-0.5 text-[10px] font-black text-[#92502A]">Unsaved</span>}
              </div>
              <p className="mt-1 text-sm text-[#805940]">{prompt.description}</p>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {prompt.variables.map(variable => {
                  const present = (drafts[prompt.key] ?? '').includes(`{{${variable}}}`);
                  return <code key={variable} className={`rounded-full px-2 py-0.5 text-[11px] font-bold ${present ? 'bg-[#E3F7D9] text-[#347233]' : 'bg-[#FFE1D6] text-[#AD3212]'}`}>{`{{${variable}}}`}</code>;
                })}
              </div>
              <p className="mt-2 text-xs text-[#805940]">Must reply with: <code className="rounded bg-[#F5E9DA] px-1">{prompt.output}</code></p>
              <textarea value={drafts[prompt.key] ?? ''} onChange={event => setDrafts(current => ({ ...current, [prompt.key]: event.target.value }))} rows={14}
                className="mt-3 w-full resize-y rounded-xl border border-[#E7BD91] bg-[#FFFDF8] p-4 font-mono text-xs leading-relaxed text-ink outline-none focus:border-orange" />
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <Button type="button" onClick={() => save(prompt.key)} disabled={busyKey !== null || !dirty}>{busyKey === prompt.key ? <LoaderCircle className="animate-spin" size={16} /> : <Save size={16} />} Save prompt</Button>
                <Button type="button" variant="ghost" onClick={() => reset(prompt.key)} disabled={busyKey !== null || (!prompt.customized && !dirty)}><RotateCcw size={16} /> Reset to default</Button>
                {message?.key === prompt.key && <span className={`text-sm font-bold ${message.kind === 'ok' ? 'text-[#347233]' : 'text-[#AD3212]'}`}>{message.text}</span>}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
