import { useState } from 'react';
import { Ban, CheckCircle2, LoaderCircle, RefreshCw, XCircle } from 'lucide-react';
import type { Application, ApplicationFormData, ManualStatus } from '../../lib/api';

type Action = { id: ManualStatus | 'RETRY'; label: string; icon: typeof Ban; tone: string; confirm?: boolean };

const RETRY: Action = { id: 'RETRY', label: 'Retry', icon: RefreshCw, tone: 'bg-[#FFE4C8] text-[#82512D] hover:bg-[#FFD7AE]' };
const FAIL: Action = { id: 'FAILED', label: 'Move to failed', icon: XCircle, tone: 'bg-[#FFE1D6] text-[#AD3212] hover:bg-[#FFD0C0]', confirm: true };
const DONE: Action = { id: 'COMPLETED', label: 'Completed', icon: CheckCircle2, tone: 'bg-[#DDF4D5] text-[#347233] hover:bg-[#CBEFC0]' };
const REJECT: Action = { id: 'REJECTED', label: 'Rejected', icon: Ban, tone: 'bg-[#EFE7E1] text-[#6B4329] hover:bg-[#E5D9CF]', confirm: true };

export function actionsFor(application: Application): Action[] {
  const sessionClosed = (application.form_data as ApplicationFormData | undefined)?.session_open === false;
  switch (application.status) {
    case 'PENDING':
    case 'IN_PROGRESS':
      return [FAIL];
    case 'HUMAN_REVIEW_REQUIRED':
      return sessionClosed ? [RETRY, DONE, REJECT, FAIL] : [DONE, REJECT, FAIL];
    case 'FAILED':
      return [RETRY, DONE, REJECT];
    case 'SUBMITTED':
      return [DONE, REJECT];
    case 'COMPLETED':
      return [REJECT];
    case 'REJECTED':
      return [RETRY];
    default:
      return [];
  }
}

/** Manual status controls: stop a stuck job, or close out a job as completed / rejected. */
export function StatusActions({ application, onRetry, onStatus, size = 'sm' }: {
  application: Application;
  onRetry: (application: Application) => Promise<void> | void;
  onStatus: (application: Application, status: ManualStatus) => Promise<void>;
  size?: 'sm' | 'xs';
}) {
  const [pending, setPending] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const actions = actionsFor(application);
  if (!actions.length) return null;
  const pad = size === 'xs' ? 'px-2 py-1 text-[10px]' : 'px-3 py-1.5 text-xs';

  async function run(action: Action) {
    if (action.confirm && confirming !== action.id) { setConfirming(action.id); return; }
    setPending(action.id); setConfirming(null);
    try {
      if (action.id === 'RETRY') await onRetry(application);
      else await onStatus(application, action.id);
    } finally { setPending(null); }
  }

  return (
    <div className="flex flex-wrap gap-1.5" onClick={event => event.stopPropagation()}>
      {actions.map(action => {
        const Icon = action.icon;
        return (
          <button key={action.id} type="button" disabled={pending !== null} onClick={() => run(action)}
            className={`inline-flex items-center gap-1 rounded-full font-bold transition disabled:opacity-60 ${pad} ${action.tone} ${confirming === action.id ? 'ring-2 ring-offset-1 ring-[#AD3212]' : ''}`}>
            {pending === action.id ? <LoaderCircle size={12} className="animate-spin" /> : <Icon size={12} />}
            {confirming === action.id ? `Confirm: ${action.label.toLowerCase()}?` : action.label}
          </button>
        );
      })}
    </div>
  );
}
