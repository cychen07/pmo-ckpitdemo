import type {
  AlertOutboxCluster,
  AlertOutboxRecord,
  AlertSilenceRecord,
  CurrentUser,
  DomainEvent,
  Executor,
  JobRecord,
  MetricsResponse,
  OpsTaskRecord,
  RuntimeHealth,
  RuntimeStatus,
  Trace,
  Workflow,
  WorkflowOverview,
  WorkflowTemplate,
  WorkitemAction,
} from './types';

const API_BASE = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000';
const TOKEN_KEY = 'newera_demo_token';
const DEFAULT_TOKEN = 'demo-owner';

export function getStoredToken(): string {
  if (typeof window === 'undefined') return DEFAULT_TOKEN;
  return window.localStorage.getItem(TOKEN_KEY) ?? DEFAULT_TOKEN;
}

export function setStoredToken(token: string) {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function resolveArtifactUrl(uri: string): string {
  if (uri.startsWith('http://') || uri.startsWith('https://')) return uri;
  return `${API_BASE}${uri}`;
}

function headers(extra?: Record<string, string>): Record<string, string> {
  const token = getStoredToken();
  return {
    Authorization: `Bearer ${token}`,
    ...extra,
  };
}

async function parseOrThrow(response: Response, fallback: string) {
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const detail = (data as { detail?: unknown }).detail;
    throw new Error(typeof detail === 'string' ? detail : fallback);
  }
  return response.json();
}

export async function fetchWhoAmI(): Promise<CurrentUser> {
  const response = await fetch(`${API_BASE}/v1/whoami`, { headers: headers() });
  return parseOrThrow(response, `Whoami failed: ${response.status}`);
}

export async function fetchRuntimeStatus(): Promise<RuntimeStatus> {
  const response = await fetch(`${API_BASE}/v1/runtime`, { headers: headers() });
  return parseOrThrow(response, `Runtime status failed: ${response.status}`);
}

export async function fetchRuntimeHealth(): Promise<RuntimeHealth> {
  const response = await fetch(`${API_BASE}/v1/runtime/health`, { headers: headers() });
  return parseOrThrow(response, `Runtime health failed: ${response.status}`);
}

export async function fetchWorkflows(): Promise<Workflow[]> {
  const response = await fetch(`${API_BASE}/v1/workflows`, { headers: headers() });
  return parseOrThrow(response, `Workflows failed: ${response.status}`);
}

export async function fetchWorkflow(workflowId: string): Promise<Workflow> {
  const response = await fetch(`${API_BASE}/v1/workflows/${workflowId}`, { headers: headers() });
  return parseOrThrow(response, `Workflow request failed: ${response.status}`);
}

export async function fetchTemplates(): Promise<WorkflowTemplate[]> {
  const response = await fetch(`${API_BASE}/v1/templates`, { headers: headers() });
  return parseOrThrow(response, `Templates failed: ${response.status}`);
}

export async function instantiateTemplate(templateId: string, title?: string): Promise<Workflow> {
  const response = await fetch(`${API_BASE}/v1/templates/${templateId}/instantiate`, {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ title }),
  });
  return parseOrThrow(response, `Instantiate failed: ${response.status}`);
}

export async function fetchExecutors(): Promise<Executor[]> {
  const response = await fetch(`${API_BASE}/v1/executors`, { headers: headers() });
  return parseOrThrow(response, `Executors failed: ${response.status}`);
}

export async function recommendExecutors(capability?: string): Promise<Executor[]> {
  const response = await fetch(`${API_BASE}/v1/executors/recommend`, {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ capability: capability || null }),
  });
  return parseOrThrow(response, `Executor recommendation failed: ${response.status}`);
}

export async function fetchTrace(workitemId: string): Promise<Trace> {
  const response = await fetch(`${API_BASE}/v1/workitems/${workitemId}/trace`, { headers: headers() });
  return parseOrThrow(response, `Trace failed: ${response.status}`);
}

export async function fetchArtifactContent(artifactId: string) {
  const response = await fetch(`${API_BASE}/v1/artifacts/${artifactId}/content`, { headers: headers() });
  return parseOrThrow(response, `Artifact content failed: ${response.status}`);
}

export async function updateAcceptance(workitemId: string, criterionId: string, checked: boolean) {
  const response = await fetch(`${API_BASE}/v1/workitems/${workitemId}/acceptance`, {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ criterion_id: criterionId, checked }),
  });
  return parseOrThrow(response, `Acceptance update failed: ${response.status}`);
}

export async function workitemAction(
  workitemId: string,
  action: WorkitemAction,
  payload: Record<string, unknown> = {},
) {
  const finalPayload = action === 'reject'
    ? { reason: '验收项未完全通过', ...payload }
    : action === 'decide'
      ? { decision: 'continue', ...payload }
      : payload;
  const response = await fetch(`${API_BASE}/v1/workitems/${workitemId}/${action}`, {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ payload: finalPayload }),
  });
  return parseOrThrow(response, `Action failed: ${response.status}`);
}

export class IdempotencyConflictError extends Error {
  readonly code = 'idempotency_conflict';
  readonly workitemId: string;
  readonly op: string;
  constructor(workitemId: string, op: string, message: string) {
    super(message);
    this.workitemId = workitemId;
    this.op = op;
  }
}

export async function runAgent(workitemId: string) {
  const response = await fetch(`${API_BASE}/v1/workitems/${workitemId}/run-agent`, {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ payload: {} }),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const detail = (data as { detail?: unknown }).detail;
    if (
      response.status === 409 &&
      detail &&
      typeof detail === 'object' &&
      (detail as { code?: string }).code === 'idempotency_conflict'
    ) {
      const d = detail as { op: string; workitem_id: string; message: string };
      throw new IdempotencyConflictError(d.workitem_id, d.op, d.message);
    }
    const msg = typeof detail === 'string' ? detail : `Run agent failed: ${response.status}`;
    throw new Error(msg);
  }
  return response.json();
}

export async function fetchJobs(): Promise<JobRecord[]> {
  const response = await fetch(`${API_BASE}/v1/jobs`, { headers: headers() });
  return parseOrThrow(response, `Jobs failed: ${response.status}`);
}

export async function fetchDeadLetters(): Promise<JobRecord[]> {
  const response = await fetch(`${API_BASE}/v1/jobs/dead-letters`, { headers: headers() });
  return parseOrThrow(response, `Dead letters failed: ${response.status}`);
}

export async function retryJob(jobId: string): Promise<JobRecord> {
  const response = await fetch(`${API_BASE}/v1/jobs/${jobId}/retry`, {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
  });
  return parseOrThrow(response, `Retry job failed: ${response.status}`);
}

export async function replayJob(jobId: string, payload: Record<string, unknown>): Promise<JobRecord> {
  const response = await fetch(`${API_BASE}/v1/jobs/${jobId}/replay`, {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ payload }),
  });
  return parseOrThrow(response, `Replay job failed: ${response.status}`);
}

export async function cancelJob(jobId: string): Promise<JobRecord> {
  const response = await fetch(`${API_BASE}/v1/jobs/${jobId}/cancel`, {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
  });
  return parseOrThrow(response, `Cancel job failed: ${response.status}`);
}

export async function fetchEventHistory(limit = 30): Promise<DomainEvent[]> {
  const response = await fetch(`${API_BASE}/v1/events/history?limit=${limit}`, { headers: headers() });
  return parseOrThrow(response, `Event history failed: ${response.status}`);
}

export async function fetchAudit(limit = 50) {
  const response = await fetch(`${API_BASE}/v1/audit?limit=${limit}`, { headers: headers() });
  return parseOrThrow(response, `Audit failed: ${response.status}`);
}

export async function fetchAlertOutbox(status?: string): Promise<AlertOutboxRecord[]> {
  const suffix = status ? `?status=${encodeURIComponent(status)}` : '';
  const response = await fetch(`${API_BASE}/v1/alerts/outbox${suffix}`, { headers: headers() });
  return parseOrThrow(response, `Alert outbox failed: ${response.status}`);
}

export async function fetchAlertOutboxClusters(status?: string): Promise<AlertOutboxCluster[]> {
  const suffix = status ? `?status=${encodeURIComponent(status)}` : '';
  const response = await fetch(`${API_BASE}/v1/alerts/outbox/clusters${suffix}`, { headers: headers() });
  return parseOrThrow(response, `Alert outbox clusters failed: ${response.status}`);
}

export async function fetchAlertSilences(): Promise<AlertSilenceRecord[]> {
  const response = await fetch(`${API_BASE}/v1/alerts/outbox/silences`, { headers: headers() });
  return parseOrThrow(response, `Alert silences failed: ${response.status}`);
}

export async function silenceAlertCluster(
  cluster_key: string,
  duration_sec?: number,
): Promise<{ cluster_key: string; silenced_until: string; duration_sec: number }> {
  const response = await fetch(`${API_BASE}/v1/alerts/outbox/silence`, {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ cluster_key, duration_sec }),
  });
  return parseOrThrow(response, `Silence alert cluster failed: ${response.status}`);
}

export async function unsilenceAlertCluster(
  cluster_key: string,
): Promise<{ cluster_key: string; cleared: boolean; silenced_until: null }> {
  const response = await fetch(`${API_BASE}/v1/alerts/outbox/unsilence`, {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ cluster_key }),
  });
  return parseOrThrow(response, `Unsilence alert cluster failed: ${response.status}`);
}

export async function unsilenceAlertClusters(
  cluster_keys: string[],
): Promise<Array<{ cluster_key: string; cleared: boolean; silenced_until: null }>> {
  const response = await fetch(`${API_BASE}/v1/alerts/outbox/unsilence-many`, {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ cluster_keys }),
  });
  return parseOrThrow(response, `Unsilence alert clusters failed: ${response.status}`);
}

export async function resendAlert(alertId: string): Promise<AlertOutboxRecord> {
  const response = await fetch(`${API_BASE}/v1/alerts/outbox/${alertId}/resend`, {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
  });
  return parseOrThrow(response, `Resend alert failed: ${response.status}`);
}

export async function resendAlerts(body: {
  alert_ids?: string[];
  cluster_key?: string;
  status?: 'failed' | 'buffered' | 'delivered' | 'pending';
}): Promise<AlertOutboxRecord[]> {
  const response = await fetch(`${API_BASE}/v1/alerts/outbox/resend`, {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  });
  return parseOrThrow(response, `Resend alerts failed: ${response.status}`);
}

export async function fetchOpsTasks(
  workitemId?: string,
  sourceKind?: string,
  forceRefresh = false,
): Promise<OpsTaskRecord[]> {
  const params = new URLSearchParams();
  if (workitemId) params.set('workitem_id', workitemId);
  if (sourceKind) params.set('source_kind', sourceKind);
  if (forceRefresh) params.set('force_refresh', 'true');
  const suffix = params.size > 0 ? `?${params.toString()}` : '';
  const response = await fetch(`${API_BASE}/v1/ops/tasks${suffix}`, { headers: headers() });
  return parseOrThrow(response, `Ops tasks failed: ${response.status}`);
}

export async function humanTakeoverJob(jobId: string, payload: Record<string, unknown>) {
  const response = await fetch(`${API_BASE}/v1/jobs/${jobId}/human-takeover`, {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ payload }),
  });
  return parseOrThrow(response, `Human takeover failed: ${response.status}`);
}

export async function escalateJobToTask(
  jobId: string,
  body: { title?: string; summary?: string; severity?: string } = {},
): Promise<{ task: OpsTaskRecord }> {
  const response = await fetch(`${API_BASE}/v1/jobs/${jobId}/escalate-task`, {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  });
  return parseOrThrow(response, `Escalate to task failed: ${response.status}`);
}

/* OBS-01 / WF-CTL */
export async function fetchMetrics(): Promise<MetricsResponse> {
  const response = await fetch(`${API_BASE}/v1/metrics`, { headers: headers() });
  return parseOrThrow(response, `Metrics failed: ${response.status}`);
}

export async function fetchWorkflowOverview(workflowId: string): Promise<WorkflowOverview> {
  const response = await fetch(`${API_BASE}/v1/workflows/${workflowId}/overview`, { headers: headers() });
  return parseOrThrow(response, `Workflow overview failed: ${response.status}`);
}

export async function workflowAction(workflowId: string, action: 'start' | 'pause' | 'resume' | 'complete' | 'cancel') {
  const response = await fetch(`${API_BASE}/v1/workflows/${workflowId}/${action}`, {
    method: 'POST',
    headers: headers({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ payload: {} }),
  });
  return parseOrThrow(response, `Workflow action failed: ${response.status}`);
}

export function openEventStream(
  onEvent: (event: DomainEvent) => void,
  onError?: (err: Event) => void,
  replay = 10,
): () => void {
  const token = encodeURIComponent(getStoredToken());
  const url = `${API_BASE}/v1/events?replay=${replay}&access_token=${token}`;
  const source = new EventSource(url);
  source.onmessage = (msg) => {
    try {
      onEvent(JSON.parse(msg.data));
    } catch {
      /* ignore malformed */
    }
  };
  const handler = (e: MessageEvent) => {
    try {
      onEvent(JSON.parse(e.data));
    } catch {
      /* ignore */
    }
  };
  const knownEvents = [
    'workitem.assign',
    'workitem.start',
    'workitem.started',
    'workitem.submit',
    'workitem.submitted',
    'workitem.approve',
    'workitem.approved',
    'workitem.reject',
    'workitem.rejected',
    'workitem.pause',
    'workitem.paused',
    'workitem.resume',
    'workitem.resumed',
    'workitem.takeover',
    'workitem.cancel',
    'workitem.cancelled',
    'workitem.escalate',
    'workitem.escalated',
    'workitem.request_decision',
    'workitem.decide',
    'decision.requested',
    'decision.resolved',
    'agent.research.completed',
    'agent.drafting.completed',
    'agent.analysis.completed',
    'workflow.start',
    'workflow.pause',
    'workflow.resume',
    'workflow.complete',
    'workflow.cancel',
    'budget.warning',
    'budget.exhausted',
    'job.queued',
    'job.started',
    'job.succeeded',
    'job.failed',
    'job.retry_scheduled',
    'job.dead_lettered',
    'job.replayed',
    'job.retried',
    'job.cancel_requested',
    'job.cancelled',
  ];
  knownEvents.forEach((name) => source.addEventListener(name, handler as EventListener));
  source.onerror = (err) => {
    if (onError) onError(err);
  };
  return () => {
    knownEvents.forEach((name) => source.removeEventListener(name, handler as EventListener));
    source.close();
  };
}
