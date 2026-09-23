import { Fragment, useEffect, useState } from 'react';
import { ArrowRight, Eye, LoaderCircle, PencilLine } from 'lucide-react';
import { Button } from '../components/ui/button';
import { api } from '../lib/api';

function InlineMarkdown({ value }: { value: string }) {
  return (
    <>
      {value.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, index) => {
        if (part.startsWith('**') && part.endsWith('**')) {
          return <strong key={index}>{part.slice(2, -2)}</strong>;
        }

        if (part.startsWith('`') && part.endsWith('`')) {
          return (
            <code
              key={index}
              className="rounded bg-[#F5E9DA] px-1 py-0.5 font-mono text-[.9em]"
            >
              {part.slice(1, -1)}
            </code>
          );
        }

        return <Fragment key={index}>{part}</Fragment>;
      })}
    </>
  );
}

function MarkdownPreview({ markdown }: { markdown: string }) {
  return (
    <div className="h-[420px] min-h-[240px] resize-y overflow-y-auto rounded-2xl border border-[#E8C9A4] bg-[#FFFDF8] p-5 text-sm leading-relaxed text-[#4E2B19]">
      {markdown.split('\n').map((line, index) => {
        if (line.startsWith('### ')) {
          return (
            <h3 key={index} className="mt-5 text-base font-black first:mt-0">
              <InlineMarkdown value={line.slice(4)} />
            </h3>
          );
        }

        if (line.startsWith('## ')) {
          return (
            <h2 key={index} className="mt-6 text-xl font-black first:mt-0">
              <InlineMarkdown value={line.slice(3)} />
            </h2>
          );
        }

        if (line.startsWith('# ')) {
          return (
            <h1 key={index} className="text-2xl font-black first:mt-0">
              <InlineMarkdown value={line.slice(2)} />
            </h1>
          );
        }

        if (/^[-*] /.test(line)) {
          return (
            <div key={index} className="flex gap-2">
              <span className="text-orange">•</span>
              <span>
                <InlineMarkdown value={line.slice(2)} />
              </span>
            </div>
          );
        }

        if (!line.trim()) return <div key={index} className="h-3" />;

        return (
          <p key={index}>
            <InlineMarkdown value={line} />
          </p>
        );
      })}
    </div>
  );
}

export function ProfileReview({
  token,
  onDone,
}: {
  token: string;
  onDone: () => void;
}) {
  const [markdown, setMarkdown] = useState('');
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    api
      .profile(token)
      .then(result => setMarkdown(result.markdown ?? ''))
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

  return (
    <main className="min-h-screen bg-cream px-5 py-12">
      <section className="mx-auto max-w-6xl rounded-[2rem] border border-[#EAC79F] bg-[#FFF9F0] p-7 shadow-soft sm:p-10">
        <PencilLine className="text-orange" />

        <p className="mt-5 text-xs font-black uppercase tracking-[.16em] text-orange">
          Step 3 · Review profile
        </p>

        <h1 className="mt-2 text-4xl font-black">Make it sound like you.</h1>

        <p className="mt-3 text-[#805940]">
          Edit the Markdown on the left and verify the live formatting preview
          on the right before saving.
        </p>

        <div className="mt-7 grid gap-5 lg:grid-cols-2">
          <div>
            <div className="mb-2 flex items-center gap-2 text-xs font-black uppercase tracking-[.14em] text-[#895B3E]">
              <PencilLine size={14} />
              Markdown editor
            </div>

            <textarea
              value={markdown}
              onChange={e => setMarkdown(e.target.value)}
              disabled={busy}
              spellCheck={false}
              className="h-[420px] min-h-[240px] w-full resize-y overflow-y-auto rounded-2xl border border-[#E8C9A4] bg-[#FFFDF8] p-5 font-mono text-sm leading-relaxed outline-none focus:border-orange disabled:cursor-not-allowed"
            />
          </div>

          <div>
            <div className="mb-2 flex items-center gap-2 text-xs font-black uppercase tracking-[.14em] text-[#895B3E]">
              <Eye size={14} />
              Live preview
            </div>

            <MarkdownPreview markdown={markdown} />
          </div>
        </div>

        {error && (
          <p className="mt-4 rounded-xl bg-[#FFE1D6] p-3 font-bold text-[#AD3212]">
            {error}
          </p>
        )}

        <Button
          size="lg"
          className="mt-6 w-full"
          onClick={save}
          disabled={busy}
        >
          {busy ? (
            <LoaderCircle className="animate-spin" />
          ) : (
            <>
              Save profile & find jobs <ArrowRight size={18} />
            </>
          )}
        </Button>
      </section>
    </main>
  );
}