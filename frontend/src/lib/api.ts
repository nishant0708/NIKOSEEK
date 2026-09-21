const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000/api/v1';

export type User = { id: number; username: string; created_at: string; is_admin: boolean };
export type AgentConfig = { match_scoring: { model: string }; system_prompt: string; can_edit_system_prompt: boolean };
export type AuthResponse = { access_token: string; token_type: string; user: User };
export type Job = { id?: number; source: string; external_url: string; title?: string; company?: string; location?: string; description?: string; date_posted?: string; ai_status: string; match_score?: number; ai_reason?: string; experience_match?: string };

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
  searchJobs: (token: string, payload: Record<string, unknown>) => request<{ run_id: number; accepted_jobs: Job[]; rejected_jobs: Job[]; fetched_count: number; saved_count: number }>('/jobs/scrape', { method: 'POST', body: JSON.stringify(payload) }, token),
  jobs: (token: string) => request<Job[]>('/jobs', {}, token),
  agentConfig: (token: string) => request<AgentConfig>('/agent-config', {}, token),
  saveAgentConfig: (token: string, model: string) => request<AgentConfig>('/agent-config', { method: 'PUT', body: JSON.stringify({ match_scoring: { model } }) }, token),
  saveSystemPrompt: (token: string, content: string) => request<AgentConfig>('/agent-config/system-prompt', { method: 'PUT', body: JSON.stringify({ content }) }, token),
};
