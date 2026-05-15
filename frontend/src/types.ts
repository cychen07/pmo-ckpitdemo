export type WorkitemState =
  | 'queued'
  | 'in_progress'
  | 'paused'
  | 'awaiting_decision'
  | 'submitted'
  | 'approved'
  | 'rejected'
  | 'escalated'
  | 'cancelled';

export type ExecutorType = 'human' | 'agent' | 'hybrid';

export interface Capability {
  tag: string;
  confidence: number;
}

export interface Executor {
  id: string;
  name: string;
  type: ExecutorType;
  capabilities: Capability[];
  current_load: number;
  unit_cost: number;
  success_rate: number;
  rework_rate: number;
  owner_user_id?: string;
  agent_spec?: { kind?: string; adapter?: string; model?: string };
}

export interface AcceptanceCriterion {
  id: string;
  label: string;
  checked: boolean;
  note?: string | null;
}

export interface DecisionGate {
  id: string;
  title: string;
  owner: string;
  sla_at: string;
  options: string[];
  selected_option?: string | null;
  reasoning?: string | null;
}

export interface Artifact {
  id: string;
  workitem_id: string;
  type: string;
  title: string;
  uri: string;
  confidence: number;
  version: number;
  external_refs?: Array<{ kind: string; label: string; url: string }>;
  created_at: string;
}

export type WorkitemAction =
  | 'assign'
  | 'start'
  | 'pause'
  | 'resume'
  | 'takeover'
  | 'request_decision'
  | 'decide'
  | 'submit'
  | 'approve'
  | 'reject'
  | 'escalate'
  | 'cancel';

export interface Budget {
  token_cap: number;
  cost_cap_usd: number;
  time_cap_min: number;
  tokens_used: number;
  cost_used_usd: number;
  time_used_sec: number;
}

export interface Workitem {
  id: string;
  title: string;
  goal: string;
  inputs: Array<Record<string, unknown>>;
  expected_outputs: Array<Record<string, unknown>>;
  acceptance_criteria: AcceptanceCriterion[];
  tool_whitelist: string[];
  budget: Budget;
  assignee: Executor;
  owner: string;
  state: WorkitemState;
  priority: 'P0' | 'P1' | 'P2' | 'P3';
  trace_id: string;
  parent_workflow_id?: string;
  decision_gate?: boolean;
  decision?: DecisionGate | null;
  artifacts: Artifact[];
  rejection_history: Array<Record<string, unknown>>;
  risk_score: number;
  sla_at?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  updated_at: string;
  allowed_actions?: WorkitemAction[];
}

export interface WorkflowEdge {
  from_id: string;
  to_id: string;
  condition?: string;
}

export type WorkflowState = 'draft' | 'running' | 'paused' | 'completed' | 'cancelled';

export interface Workflow {
  id: string;
  title: string;
  nodes: Workitem[];
  edges: WorkflowEdge[];
  template_id?: string;
  sla: string;
  rollback_policy: string;
  executors: Executor[];
  owner?: string;
  state?: WorkflowState;
}

export interface WorkflowTemplateNode {
  role: string;
  title: string;
  goal: string;
  decision_gate?: boolean;
  risk_score?: number;
  priority?: 'P0' | 'P1' | 'P2' | 'P3';
  inputs: Array<Record<string, unknown>>;
  expected_outputs: Array<Record<string, unknown>>;
  acceptance_criteria: Array<Record<string, unknown>>;
  tool_whitelist: string[];
  budget: Record<string, unknown>;
}

export interface WorkflowTemplate {
  id: string;
  title: string;
  description: string;
  nodes: WorkflowTemplateNode[];
  edges: Array<Record<string, string>>;
  sla: string;
  rollback_policy: string;
  owner: string;
  created_at: string;
  updated_at: string;
}

export interface TraceEntry {
  timestamp: string;
  actor: string;
  action: string;
  tool_used?: string | null;
  input_snapshot: Record<string, unknown>;
  output_snapshot: Record<string, unknown>;
  cost: number;
  duration: number;
  status: 'started' | 'succeeded' | 'failed' | 'blocked';
}

export interface Trace {
  id: string;
  workitem_id: string;
  entries: TraceEntry[];
}

export interface CurrentUser {
  id: string;
  name: string;
  role: 'operator' | 'owner' | 'admin';
  permissions: string[];
}

export interface RuntimeStatus {
  delivery_provider: string;
  notification_provider: string;
  ops_task_provider?: string;
  execution_mode: string;
  state_store_mode: string;
  object_store_mode: string;
  job_timeout_sec: number;
  job_retry_backoff_sec: number;
  dead_letter_enabled: boolean;
  snapshot_key?: string;
  postgres_dsn?: string | null;
  lark_base_url: string;
  lark_doc_folder_token?: string | null;
  lark_app_id?: string | null;
  lark_app_secret?: string | null;
  lark_bot_webhook_url?: string | null;
  lark_task_webhook_url?: string | null;
  lark_tasklist_guid?: string | null;
  lark_doc_enabled: boolean;
  lark_notify_enabled: boolean;
  lark_task_enabled?: boolean;
  lark_task_api_enabled?: boolean;
  alert_dedup_window_sec?: number;
  alert_silence_default_sec?: number;
  delivery_health?: {
    artifact_delivery: {
      provider: string;
      ok: boolean;
      mode?: string;
      error?: string;
    };
    notification: {
      provider: string;
      ok: boolean;
      mode?: string;
      error?: string;
    };
  };
  alerting_health?: {
    provider: string;
    ok: boolean;
    mode?: string;
    error?: string;
    dedup_window_sec?: number;
    silence_default_sec?: number;
  };
  alerting_stats?: {
    dedup_window_sec: number;
    silence_default_sec: number;
    suppressed_total: number;
    silenced_clusters: number;
  };
  tasking_health?: {
    provider: string;
    ok: boolean;
    mode?: string;
    error?: string;
    tasklist_guid_configured?: boolean;
  };
  job_stats?: {
    provider?: string;
    queued: number;
    running: number;
    succeeded: number;
    failed: number;
    dead_lettered?: number;
    cancel_requested?: number;
    cancelled?: number;
    total: number;
    worker_alive: boolean;
  };
}

export interface RuntimeHealth {
  queue: {
    provider: string;
    ok: boolean;
    worker_alive?: boolean;
    error?: string;
  };
  state_store: {
    provider: string;
    ok: boolean;
    error?: string;
  };
  delivery: {
    artifact_delivery: {
      provider: string;
      ok: boolean;
      mode?: string;
      error?: string;
    };
    notification: {
      provider: string;
      ok: boolean;
      mode?: string;
      error?: string;
    };
  };
  tasking?: {
    provider: string;
    ok: boolean;
    mode?: string;
    error?: string;
  };
  execution_mode: string;
  ready?: boolean;
}

export interface JobRecord {
  id: string;
  kind: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'dead_lettered' | 'cancel_requested' | 'cancelled';
  workitem_id: string;
  actor: string;
  payload: Record<string, unknown>;
  result?: Record<string, unknown> | null;
  error?: string | null;
  last_failure_kind?: 'timeout' | 'error' | null;
  attempts: number;
  max_attempts: number;
  timeout_sec?: number;
  retry_backoff_sec?: number;
  source_job_id?: string | null;
  cancel_requested: boolean;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  next_retry_at?: string | null;
}

export interface AlertOutboxRecord {
  id: string;
  event_name: string;
  severity: 'warning' | 'critical';
  summary: string;
  workitem_id?: string | null;
  workflow_id?: string | null;
  payload: Record<string, unknown>;
  timestamp: string;
  status: 'buffered' | 'delivered' | 'failed' | 'pending';
  provider: string;
  attempts: number;
  delivered_at?: string | null;
  error?: string | null;
}

export interface AlertOutboxCluster {
  cluster_key: string;
  status: 'buffered' | 'delivered' | 'failed' | 'pending';
  event_name: string;
  provider: string;
  reason: string;
  severity: 'warning' | 'critical';
  count: number;
  alert_ids: string[];
  last_timestamp: string;
  latest_summary?: string | null;
  latest_error?: string | null;
  suppressed_count?: number;
  silenced_until?: string | null;
}

export interface AlertSilenceRecord {
  cluster_key: string;
  event_name: string;
  reason: string;
  provider: string;
  silenced_until: string;
  suppressed_count?: number;
  last_emitted_at?: string | null;
}

export interface OpsTaskRecord {
  id: string;
  workitem_id: string;
  workflow_id?: string | null;
  title: string;
  summary: string;
  severity: string;
  provider: string;
  status: string;
  owner: string;
  created_by: string;
  source_job_id?: string | null;
  source_kind?: string | null;
  source_ref?: string | null;
  metadata?: Record<string, unknown>;
  created_at: string;
  delivery_status?: string;
  external_status?: string;
  last_synced_at?: string | null;
  completed_at?: string | null;
  sync_error?: string | null;
  delivery_error?: string | null;
  external_ref?: { kind: string; label: string; url: string } | null;
}

export interface DomainEvent {
  name: string;
  workitem_id?: string | null;
  workflow_id?: string | null;
  payload: Record<string, unknown>;
  seq: number;
  timestamp: string;
}

/* OBS-01 / WF-CTL 类型 */
export interface MetricsBudgetSlot {
  used: number;
  cap: number;
  pct: number;
}

export interface AgentCostRow {
  executor_id: string;
  name: string;
  tokens_used: number;
  cost_used_usd: number;
  workitems: number;
  artifacts: number;
}

export interface WorkflowOverview {
  id: string;
  title: string;
  state: WorkflowState;
  owner?: string;
  sla: string;
  rollback_policy: string;
  total_nodes: number;
  completed_nodes: number;
  blocked_nodes: number;
  decision_pending: number;
  progress_pct: number;
  state_distribution: Record<string, number>;
  budget: {
    cost_used_usd: number;
    cost_cap_usd: number;
    cost_pct: number;
    time_used_sec: number;
    time_cap_sec: number;
    time_pct: number;
    tokens_used: number;
    tokens_cap: number;
    tokens_pct: number;
  };
  allowed_actions: string[];
}

export interface MetricsResponse {
  summary: {
    workitems_total: number;
    in_progress: number;
    awaiting_decision: number;
    approved: number;
    escalated: number;
    over_budget: number;
    artifact_count: number;
    executor_count: number;
    workflow_count: number;
    template_count: number;
    event_count: number;
    alert_count: number;
  };
  state_distribution: Record<string, number>;
  budget: {
    tokens: MetricsBudgetSlot;
    cost_usd: MetricsBudgetSlot;
    time_sec: MetricsBudgetSlot;
  };
  agent_costs: AgentCostRow[];
  workflows: WorkflowOverview[];
  alerts: DomainEvent[];
  event_kind_count: Record<string, number>;
}
