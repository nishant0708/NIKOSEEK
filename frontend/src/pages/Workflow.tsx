import { useEffect, useState } from 'react';
import { Network, Settings2 } from 'lucide-react';
import { Button } from '../components/ui/button';
import { AgentPromptsEditor } from '../components/workflow/AgentPromptsEditor';
import { PipelineDiagram } from '../components/workflow/PipelineDiagram';
import { api, type AgentConfig } from '../lib/api';

export function Workflow({ token }: { token: string }) {
  const [config, setConfig] = useState<AgentConfig | null>(null);
  const [model, setModel] = useState('');
  const [prompt, setPrompt] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => { api.agentConfig(token).then(result => { setConfig(result); setModel(result.match_scoring.model); setPrompt(result.system_prompt); }).catch(error => setNotice(error instanceof Error ? error.message : 'Could not load workflow settings.')); }, [token]);
  async function saveModel() { setBusy(true); setNotice(''); try { const result = await api.saveAgentConfig(token, model); setConfig(result); setNotice('Scoring model saved.'); } catch (error) { setNotice(error instanceof Error ? error.message : 'Could not save the model.'); } finally { setBusy(false); } }
  async function savePrompt() { setBusy(true); setNotice(''); try { const result = await api.saveSystemPrompt(token, prompt); setConfig(result); setPrompt(result.system_prompt); setNotice('Shared system prompt saved.'); } catch (error) { setNotice(error instanceof Error ? error.message : 'Could not save the system prompt.'); } finally { setBusy(false); } }

  return <main className="min-h-screen bg-cream px-5 py-9"><section className="mx-auto max-w-6xl"><p className="flex items-center gap-2 text-xs font-black uppercase tracking-[.16em] text-orange"><Network size={15} /> Workflow</p><h1 className="mt-2 text-4xl font-black">Your job workflow</h1><p className="mt-3 max-w-2xl text-[#805940]">Change the scoring model, the shared job-matching prompt, and every prompt the apply agent uses.</p><PipelineDiagram model={model} onPromptClick={key => document.getElementById(`prompt-${key}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })} /><section className="mt-7 rounded-[2rem] border border-[#EBCDAA] bg-[#FFF9F0] p-6 sm:p-8"><div className="flex items-center gap-2"><Settings2 className="text-orange" size={20} /><h2 className="text-xl font-black">Match scoring configuration</h2></div><label className="mt-5 block text-sm font-bold">Model<select value={model} onChange={event => setModel(event.target.value)} className="mt-2 h-11 w-full max-w-md rounded-xl border border-[#E7BD91] bg-white px-3 text-sm font-bold text-ink outline-none"><option value="gemini-3.5-flash-lite">Gemini 3.5 Flash Lite</option><option value="gemini-3.5-flash">Gemini 3.5 Flash</option><option value="gemini-3.5-pro">Gemini 3.5 Pro</option></select></label><Button className="mt-3" onClick={saveModel} disabled={busy}>{busy ? 'Saving…' : 'Save model'}</Button><label className="mt-7 block text-sm font-bold">Shared system prompt<textarea value={prompt} onChange={event => setPrompt(event.target.value)} disabled={!config?.can_edit_system_prompt} rows={10} className="mt-2 w-full resize-y rounded-xl border border-[#E7BD91] bg-white p-4 font-mono text-xs leading-relaxed text-ink outline-none disabled:cursor-not-allowed disabled:bg-[#FFF4E7]" /></label><Button className="mt-3" variant="secondary" onClick={savePrompt} disabled={busy || !config?.can_edit_system_prompt}>{busy ? 'Saving…' : 'Save system prompt'}</Button></section>{notice && <p className="mt-4 rounded-xl bg-[#E3F7D9] p-4 font-bold text-[#347233]">{notice}</p>}<AgentPromptsEditor token={token} /></section></main>;
}
