const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000/api/v1';

export type User = { id: number; username: string; created_at: string; is_admin: boolean };
export type AgentConfig = { match_scoring: { model: string }; system_prompt: string; can_edit_system_prompt: boolean };
export type AuthResponse = { access_token: string; token_type: string; user: User };
export type Job = { id?: number; source: string; external_url: string; title?: string; company?: string; location?: string; description?: string; date_posted?: string; ai_status: string; match_score?: number; ai_reason?: string; experience_match?: string; resume_category?: string | null };
export type ApplicationJob = { id: number; source: string; external_url: string; title?: string; company?: string; location?: string; ai_status: string; match_score?: number };
export type Application = { id: number; job_id: number; resume_key: string; resume_role?: string | null; resume_agent?: ResumeAgentDecision; status: string; stage: string; queue_position: number; application_url?: string | null; form_data?: Record<string, unknown>; error?: string | null; created_at: string; updated_at: string; started_at?: string | null; applied_at?: string | null; completed_at?: string | null; llm_input_tokens?: number; llm_output_tokens?: number; llm_total_tokens?: number; llm_calls?: number; pending_question?: AgentQuestion | null; job: ApplicationJob };
export type AgentQuestion = { id: string; question: string; reason?: string; options: { id: number; label: string; idx?: number }[] };
export type BrowserFrame = { application_id: number; image: string | null; url?: string; title?: string; width?: number; height?: number; reason?: string };
export type BrowserInputEvent = { kind: 'click' | 'type' | 'key' | 'scroll' | 'back' | 'reload' | 'front'; x?: number; y?: number; text?: string; key?: string; dy?: number };
export type ApplicationQueueResponse = { message: string; applications: Application[] };
export type LinkedInCredentials = { email: string; password_set: boolean };
// One login the agent uses to sign up / sign in on company portals; accounts = { workday: { hp: true } }.
export type PortalCredentials = { email: string; password_set: boolean; accounts: Record<string, Record<string, boolean>> };
export type LinkedInSessionStatus = 'NEEDS_LOGIN' | 'LOGIN_IN_PROGRESS' | 'READY';
export type LinkedInSession = { source: string; status: LinkedInSessionStatus; connected: boolean; last_login_at?: string | null; last_checked_at?: string | null; error?: string | null; credentials: LinkedInCredentials };
export const LINKEDIN_NOT_CONNECTED = 'LINKEDIN_NOT_CONNECTED';
export type IndeedCredentials = { email: string; password_set: boolean };
export type IndeedSession = { source: string; status: LinkedInSessionStatus; connected: boolean; last_login_at?: string | null; last_checked_at?: string | null; error?: string | null; credentials: IndeedCredentials };
export const INDEED_NOT_CONNECTED = 'INDEED_NOT_CONNECTED';
export type AgentStageKey = 'resume' | 'open' | 'login' | 'find' | 'fields' | 'review' | 'failed';
export type AgentEventKind = 'stage' | 'thinking' | 'action' | 'info' | 'warning' | 'error' | 'question';
export type AgentEvent = { id: number | null; application_id: number; attempt: number; stage_key: AgentStageKey; kind: AgentEventKind; message: string; data: Record<string, unknown>; created_at: string | null };
export type FormFieldKind = 'text' | 'textarea' | 'select' | 'radio' | 'checkbox' | 'combobox' | 'listbox';
export type FormField = { key: string; kind: FormFieldKind; label: string; name?: string | null; required: boolean; options?: string[] | null; value: string; error?: string; source?: 'ai' | 'user' | 'prefilled' | null; group?: string | null; hint?: string };
export type ApplicationFormData = { mode?: 'external' | 'easy_apply' | 'indeed_apply' | 'ats'; session_open?: boolean; url?: string | null; step?: number; fields?: FormField[]; missing?: string[]; missing_required?: string[]; has_next?: boolean; can_submit?: boolean; submit_available?: boolean; resume_uploaded?: string | null; mapping?: string; answers?: FormAnswer[] };
export type FormAnswer = { step: number; label: string; value: string; source?: 'ai' | 'user' | 'prefilled' | null; kind: FormFieldKind };
export type ManualStatus = 'FAILED' | 'COMPLETED' | 'REJECTED';
export type AgentPrompt = { key: string; title: string; description: string; variables: string[]; output: string; default: string; content: string; customized: boolean };
export type AgentEventsResponse = { application_id: number; attempt: number; events: AgentEvent[] };
// Resume agent: your resumes (uploaded + generated), its settings, and the per-application decision.
export type ResumeInfo = { id: number; storage_key: string; role: string; category: string; file_name: string; label: string; origin: 'uploaded' | 'generated'; upload_name: string; parent_id?: number | null; source_job_id?: number | null; created_at?: string | null; portals: Record<string, string> };
export type ResumeAgentSettings = { enabled: boolean; reuse_threshold: number; tailor_min: number; max_rounds: number; model: string };
export type ResumeCandidate = { resume_key: string; label: string; origin: string; score: number; matched: string[]; missing: string[]; reason: string };
export type ResumeAgentStatus = 'reused' | 'generated' | 'low_fit' | 'tailor_no_gain';
export type ResumeAgentDecision = { status?: ResumeAgentStatus; category?: string; candidates?: ResumeCandidate[]; jd_keywords?: string[]; base_key?: string; base_score?: number; chosen_key?: string; chosen_label?: string; chosen_score?: number; tailored_score?: number | null; keywords_added?: string[]; pinned?: boolean; thresholds?: { reuse: number; tailor_min: number } };
export type ResumeSwitchResult = 'attached' | 'not_on_this_step' | 'queued' | 'retry';

async function request<T>(path: string, init: RequestInit = {}, token?: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { ...(init.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }), ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init.headers },
  });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(data?.detail ?? 'Something went wrong. Please try again.');
  }
  return response.json() as Promise<T>;
}

async function requestBlob(path: string, token: string): Promise<Blob> {
  const response = await fetch(`${API_URL}${path}`, { headers: { Authorization: `Bearer ${token}` } });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(data?.detail ?? 'Could not load the file.');
  }
  return response.blob();
}

function applicationSocketUrl(token: string) {
  const url = new URL(API_URL);
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
  url.pathname = `${url.pathname.replace(/\/$/, '')}/applications/ws`;
  url.searchParams.set('token', token);
  return url.toString();
}

export const api = {
  signup: (username: string, password: string) => request<AuthResponse>('/auth/signup', { method: 'POST', body: JSON.stringify({ username, password }) }),
  login: (username: string, password: string) => request<AuthResponse>('/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
  me: (token: string) => request<User>('/auth/me', {}, token),
  resumeStatus: (token: string) => request<{ has_resume: boolean }>('/resumes/status', {}, token),
  uploadResume: (token: string, role: string, file: File) => {
    const data = new FormData(); data.append('role', role); data.append('file', file);
    return request<{ message: string; storage_key: string }>('/resumes', { method: 'POST', body: data }, token);
  },
  generateProfile: (token: string, payload: { resume_key: string; projects: string; achievements: string }) => request<{ details_markdown: string; profile_markdown: string }>('/profile/generate', { method: 'POST', body: JSON.stringify(payload) }, token),
  profile: (token: string) => request<{ markdown: string | null }>('/profile', {}, token),
  saveProfile: (token: string, markdown: string) => request<{ message: string }>('/profile', { method: 'PUT', body: JSON.stringify({ markdown }) }, token),
  linkedinCredentials: (token: string) => request<LinkedInCredentials>('/profile/linkedin', {}, token),
  saveLinkedinCredentials: (token: string, payload: { email: string; password?: string | null }) => request<LinkedInCredentials>('/profile/linkedin', { method: 'PUT', body: JSON.stringify(payload) }, token),
  linkedinSession: (token: string) => request<LinkedInSession>('/linkedin/session', {}, token),
  connectLinkedin: (token: string) => request<LinkedInSession>('/linkedin/session/connect', { method: 'POST' }, token),
  disconnectLinkedin: (token: string) => request<LinkedInSession>('/linkedin/session/disconnect', { method: 'POST' }, token),
  indeedCredentials: (token: string) => request<IndeedCredentials>('/profile/indeed', {}, token),
  saveIndeedCredentials: (token: string, payload: { email: string }) => request<IndeedCredentials>('/profile/indeed', { method: 'PUT', body: JSON.stringify(payload) }, token),
  portalCredentials: (token: string) => request<PortalCredentials>('/profile/ats', {}, token),
  savePortalCredentials: (token: string, payload: { email: string; password?: string | null }) => request<PortalCredentials>('/profile/ats', { method: 'PUT', body: JSON.stringify(payload) }, token),
  forgetPortalAccount: (token: string, vendor: string, tenant: string) => request<PortalCredentials>(`/profile/ats/accounts/${encodeURIComponent(vendor)}/${encodeURIComponent(tenant)}`, { method: 'DELETE' }, token),
  indeedSession: (token: string) => request<IndeedSession>('/indeed/session', {}, token),
  connectIndeed: (token: string) => request<IndeedSession>('/indeed/session/connect', { method: 'POST' }, token),
  disconnectIndeed: (token: string) => request<IndeedSession>('/indeed/session/disconnect', { method: 'POST' }, token),
  searchJobs: (token: string, payload: Record<string, unknown>) => request<{ run_id: number; accepted_jobs: Job[]; rejected_jobs: Job[]; fetched_count: number; saved_count: number }>('/jobs/scrape', { method: 'POST', body: JSON.stringify(payload) }, token),
  jobs: (token: string) => request<Job[]>('/jobs', {}, token),
  applyJobs: (token: string, payload: { job_ids: number[]; resume_key?: string | null }) => request<ApplicationQueueResponse>('/applications/apply', { method: 'POST', body: JSON.stringify(payload) }, token),
  applications: (token: string) => request<Application[]>('/applications', {}, token),
  fillApplicationFields: (token: string, applicationId: number, values: Record<string, string>, remember: boolean) => request<Application>(`/applications/${applicationId}/fields`, { method: 'POST', body: JSON.stringify({ values, remember }) }, token),
  continueApplication: (token: string, applicationId: number) => request<Application>(`/applications/${applicationId}/continue`, { method: 'POST' }, token),
  answerQuestion: (token: string, applicationId: number, payload: { question_id: string; option?: number; instruction?: string; done?: boolean }) => request<Application>(`/applications/${applicationId}/answer`, { method: 'POST', body: JSON.stringify(payload) }, token),
  recheckApplication: (token: string, applicationId: number) => request<Application>(`/applications/${applicationId}/recheck`, { method: 'POST' }, token),
  instructApplication: (token: string, applicationId: number, instruction: string) => request<Application>(`/applications/${applicationId}/instruct`, { method: 'POST', body: JSON.stringify({ instruction }) }, token),
  browserInput: (token: string, applicationId: number, event: BrowserInputEvent) => request<{ url: string }>(`/applications/${applicationId}/browser/input`, { method: 'POST', body: JSON.stringify(event) }, token),
  setApplicationStatus: (token: string, applicationId: number, status: ManualStatus, reason?: string) => request<Application>(`/applications/${applicationId}/status`, { method: 'POST', body: JSON.stringify({ status, reason }) }, token),
  submitApplication: (token: string, applicationId: number) => request<Application>(`/applications/${applicationId}/submit`, { method: 'POST' }, token),
  resumes: (token: string) => request<ResumeInfo[]>('/resumes', {}, token),
  resumeFile: (token: string, key: string) => requestBlob(`/resumes/file?key=${encodeURIComponent(key)}`, token),
  switchResume: (token: string, applicationId: number, resumeKey: string) => request<{ application: Application; result: ResumeSwitchResult }>(`/applications/${applicationId}/resume`, { method: 'POST', body: JSON.stringify({ resume_key: resumeKey }) }, token),
  resumeAgent: (token: string) => request<ResumeAgentSettings>('/agent-config/resume-agent', {}, token),
  saveResumeAgent: (token: string, settings: ResumeAgentSettings) => request<ResumeAgentSettings>('/agent-config/resume-agent', { method: 'PUT', body: JSON.stringify(settings) }, token),
  agentPrompts: (token: string) => request<AgentPrompt[]>('/agent-config/prompts', {}, token),
  saveAgentPrompt: (token: string, key: string, content: string) => request<AgentPrompt[]>(`/agent-config/prompts/${key}`, { method: 'PUT', body: JSON.stringify({ content }) }, token),
  resetAgentPrompt: (token: string, key: string) => request<AgentPrompt[]>(`/agent-config/prompts/${key}`, { method: 'DELETE' }, token),
  applicationEvents: (token: string, applicationId: number) => request<AgentEventsResponse>(`/applications/${applicationId}/events`, {}, token),
  applicationSocketUrl,
  agentConfig: (token: string) => request<AgentConfig>('/agent-config', {}, token),
  saveAgentConfig: (token: string, model: string) => request<AgentConfig>('/agent-config', { method: 'PUT', body: JSON.stringify({ match_scoring: { model } }) }, token),
  saveSystemPrompt: (token: string, content: string) => request<AgentConfig>('/agent-config/system-prompt', { method: 'PUT', body: JSON.stringify({ content }) }, token),
};
