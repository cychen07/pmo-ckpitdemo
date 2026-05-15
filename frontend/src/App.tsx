import { useEffect, useMemo, useRef, useState } from 'react';
import {
  fetchAlertOutbox,
  fetchAlertOutboxClusters,
  fetchAlertSilences,
  fetchOpsTasks,
  cancelJob,
  escalateJobToTask,
  IdempotencyConflictError,
  humanTakeoverJob,
  fetchAudit,
  fetchArtifactContent,
  fetchEventHistory,
  fetchExecutors,
  fetchJobs,
  fetchMetrics,
  fetchRuntimeHealth,
  fetchRuntimeStatus,
  fetchTemplates,
  fetchTrace,
  fetchWorkflow,
  fetchWorkflows,
  fetchWorkflowOverview,
  fetchWhoAmI,
  getStoredToken,
  instantiateTemplate,
  openEventStream,
  replayJob,
  resendAlert,
  resendAlerts,
  silenceAlertCluster,
  unsilenceAlertCluster,
  unsilenceAlertClusters,
  recommendExecutors,
  resolveArtifactUrl,
  retryJob,
  runAgent,
  setStoredToken,
  updateAcceptance,
  workflowAction,
  workitemAction,
} from './api';
import { fallbackWorkflow } from './mock';
import { isStaleSync, renderSyncAge } from './opsTaskFreshness';
import type {
  AlertOutboxCluster,
  AlertOutboxRecord,
  AlertSilenceRecord,
  Artifact,
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
  Workitem,
  WorkitemAction,
} from './types';

type ViewKey = 'deck' | 'canvas' | 'detail' | 'resources' | 'observability';

interface Toast {
  id: number;
  tone: 'info' | 'success' | 'warn' | 'error';
  text: string;
}

interface ArtifactPreviewData {
  content: string;
  content_type: string;
}

export function App() {
  const [workflow, setWorkflow] = useState<Workflow>(fallbackWorkflow);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [templates, setTemplates] = useState<WorkflowTemplate[]>([]);
  const [executors, setExecutors] = useState<Executor[]>([]);
  const [recommendedExecutors, setRecommendedExecutors] = useState<Executor[]>([]);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);
  const [authToken, setAuthToken] = useState(getStoredToken());
  const [currentWorkflowId, setCurrentWorkflowId] = useState(fallbackWorkflow.id);
  const [view, setView] = useState<ViewKey>('deck');
  const [selectedId, setSelectedId] = useState(fallbackWorkflow.nodes[2].id);
  const [drawerOpen, setDrawerOpen] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [events, setEvents] = useState<DomainEvent[]>([]);
  const [audit, setAudit] = useState<Array<Record<string, unknown>>>([]);
  const [trace, setTrace] = useState<Trace | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [streamLive, setStreamLive] = useState(false);
  const [runningAgents, setRunningAgents] = useState<Set<string>>(new Set());
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null);
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [runtimeHealth, setRuntimeHealth] = useState<RuntimeHealth | null>(null);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [alertOutbox, setAlertOutbox] = useState<AlertOutboxRecord[]>([]);
  const [alertClusters, setAlertClusters] = useState<AlertOutboxCluster[]>([]);
  const [alertSilences, setAlertSilences] = useState<AlertSilenceRecord[]>([]);
  const [alertOpsTasks, setAlertOpsTasks] = useState<OpsTaskRecord[]>([]);
  const [refreshingAlertOpsTasks, setRefreshingAlertOpsTasks] = useState(false);
  const [overview, setOverview] = useState<WorkflowOverview | null>(null);
  const [previewArtifact, setPreviewArtifact] = useState<Artifact | null>(null);
  const [previewData, setPreviewData] = useState<ArtifactPreviewData | null>(null);
  const toastSeq = useRef(0);

  const refreshAlertOpsTasks = async (forceRefresh = false) => {
    if (forceRefresh) {
      setRefreshingAlertOpsTasks(true);
    }
    try {
      const items = await fetchOpsTasks(undefined, undefined, forceRefresh);
      setAlertOpsTasks(items.filter((task) => task.source_kind?.startsWith('alert.')));
    } catch {
      // ignore for dashboard polling
    } finally {
      if (forceRefresh) {
        setRefreshingAlertOpsTasks(false);
      }
    }
  };

  const refreshWorkflow = async (workflowId = currentWorkflowId) => {
    try {
      const data = await fetchWorkflow(workflowId);
      setWorkflow(data);
      setCurrentWorkflowId(data.id);
      setError(null);
      fetchWorkflowOverview(data.id).then(setOverview).catch(() => {});
      return data;
    } catch {
      setError('后端未启动，当前展示内置竞品分析演示数据。');
      return null;
    }
  };

  const refreshMetrics = () => {
    fetchMetrics().then(setMetrics).catch(() => {});
  };

  const refreshJobs = () => {
    fetchJobs().then(setJobs).catch(() => {});
    fetchRuntimeHealth().then(setRuntimeHealth).catch(() => {});
    fetchAlertOutbox().then(setAlertOutbox).catch(() => {});
    fetchAlertOutboxClusters().then(setAlertClusters).catch(() => {});
    fetchAlertSilences().then(setAlertSilences).catch(() => {});
    refreshAlertOpsTasks();
  };

  const refreshCatalogs = () => {
    fetchWorkflows().then(setWorkflows).catch(() => {});
    fetchTemplates().then(setTemplates).catch(() => {});
    fetchExecutors().then((items) => {
      setExecutors(items);
      setRecommendedExecutors(items);
    }).catch(() => {});
    fetchWhoAmI().then(setCurrentUser).catch(() => {});
    fetchRuntimeStatus().then(setRuntime).catch(() => {});
    fetchRuntimeHealth().then(setRuntimeHealth).catch(() => {});
    fetchJobs().then(setJobs).catch(() => {});
    fetchAudit(60).then(setAudit).catch(() => {});
  };

  const refreshTrace = (workitemId: string) => {
    fetchTrace(workitemId).then(setTrace).catch(() => setTrace(null));
  };

  const pushToast = (text: string, tone: Toast['tone'] = 'info') => {
    toastSeq.current += 1;
    const id = toastSeq.current;
    setToasts((cur) => [...cur, { id, tone, text }]);
    setTimeout(() => setToasts((cur) => cur.filter((t) => t.id !== id)), 3200);
  };

  useEffect(() => {
    refreshWorkflow(currentWorkflowId).then((data) => {
      if (data) {
        const decisionNode = data.nodes.find((n) => n.decision_gate) ?? data.nodes[0];
        if (decisionNode) setSelectedId(decisionNode.id);
      }
    });
    fetchEventHistory(15)
      .then((items) => setEvents(items))
      .catch(() => {});
    refreshCatalogs();
    refreshMetrics();
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    refreshTrace(selectedId);
  }, [selectedId]);

  useEffect(() => {
    let alive = true;
    const close = openEventStream(
      (event) => {
        if (!alive) return;
        setEvents((cur) => {
          if (cur.some((e) => e.seq === event.seq)) return cur;
          const next = [...cur, event];
          return next.length > 60 ? next.slice(next.length - 60) : next;
        });
        setStreamLive(true);
        if (event.name.startsWith('workitem.') || event.name.startsWith('agent.')) {
          setTimeout(() => alive && refreshWorkflow(currentWorkflowId), 80);
          if (event.workitem_id) setTimeout(() => alive && refreshTrace(event.workitem_id ?? selectedId), 120);
        }
        if (event.name.startsWith('workflow.')) {
          setTimeout(() => alive && refreshWorkflow(currentWorkflowId), 60);
        }
        if (
          event.name.startsWith('agent.') ||
          event.name.startsWith('workitem.') ||
          event.name.startsWith('workflow.') ||
          event.name.startsWith('budget.') ||
          event.name.startsWith('job.')
        ) {
          setTimeout(() => alive && refreshMetrics(), 200);
          setTimeout(() => alive && fetchAudit(60).then(setAudit).catch(() => {}), 220);
          setTimeout(() => alive && refreshCatalogs(), 260);
          setTimeout(() => alive && refreshJobs(), 280);
        }
        if (event.name.startsWith('budget.')) {
          const sev = event.name.endsWith('exhausted') ? 'error' : 'warn';
          const wi = event.workitem_id ?? '';
          pushToast(`预算告警 · ${wi} · ${event.name}`, sev);
        }
        if (event.name === 'decision.requested') {
          pushToast('Agent 触发了决策门，已等待你拍板', 'warn');
          if (event.workitem_id) {
            setSelectedId(event.workitem_id);
            setDrawerOpen(true);
          }
        }
        if (event.name === 'decision.resolved') {
          pushToast('决策已落库，下游恢复运行', 'success');
        }
        if (event.name.startsWith('agent.') && event.name.endsWith('.completed')) {
          pushToast(`Agent 完成：${event.name.replace('agent.', '').replace('.completed', '')}`, 'success');
        }
        if (event.name === 'job.failed') {
          pushToast('后台 Job 执行失败', 'error');
        }
        if (event.name === 'job.retry_scheduled') {
          pushToast('后台 Job 已进入退避重试', 'warn');
        }
        if (event.name === 'job.dead_lettered') {
          pushToast('后台 Job 已进入死信队列', 'error');
        }
        if (event.name === 'job.replayed') {
          pushToast('死信 Job 已重放为新任务', 'info');
        }
        if (event.name === 'job.succeeded') {
          pushToast('后台 Job 已完成', 'success');
        }
      },
      () => setStreamLive(false),
      15,
    );
    return () => {
      alive = false;
      close();
    };
  }, [authToken, currentWorkflowId, selectedId]);

  async function handleWorkflowAction(action: 'start' | 'pause' | 'resume' | 'complete' | 'cancel') {
    try {
      pushToast(`工作流 ${action} 中…`, 'info');
      await workflowAction(workflow.id, action);
      pushToast(`工作流已 ${action}`, 'success');
      refreshWorkflow(currentWorkflowId);
      refreshMetrics();
      refreshCatalogs();
    } catch (err) {
      const text = err instanceof Error ? err.message : '工作流操作失败';
      pushToast(text, 'error');
    }
  }

  const selected = workflow.nodes.find((n) => n.id === selectedId) ?? workflow.nodes[0];

  async function runAction(action: WorkitemAction) {
    if (!selected) return;
    try {
      const result = await workitemAction(selected.id, action);
      setWorkflow((cur) => ({
        ...cur,
        nodes: cur.nodes.map((n) => (n.id === selected.id ? result.workitem : n)),
      }));
      setError(null);
      pushToast(`执行 ${action} 成功`, 'success');
      refreshCatalogs();
      refreshTrace(selected.id);
    } catch (err) {
      const text = err instanceof Error ? err.message : '动作执行失败';
      setError(text);
      pushToast(text, 'error');
    }
  }

  async function handleRunAgent(workitemId?: string) {
    const id = workitemId ?? selected?.id;
    if (!id) return;
    // 前端层先挡一道：这个 workitem 已经在跑就直接 ignore，避免连点
    if (runningAgents.has(id)) return;
    setRunningAgents((cur) => {
      const next = new Set(cur);
      next.add(id);
      return next;
    });
    try {
      pushToast('Agent 已启动…', 'info');
      const result = await runAgent(id);
      if (result.mode === 'queued') {
        pushToast(`Agent 已入队，Job ${result.job.id}`, 'info');
        refreshJobs();
      } else {
        setWorkflow((cur) => ({
          ...cur,
          nodes: cur.nodes.map((n) => (n.id === id ? result.workitem : n)),
        }));
        pushToast(`Agent ${result.agent} 完成 → ${result.agent_result?.next_trigger}`, 'success');
        refreshTrace(id);
      }
    } catch (err) {
      if (err instanceof IdempotencyConflictError) {
        // 后端拒重复，静默忽略，仅给一条温和提示
        pushToast('Agent 已在运行，忽略重复触发', 'warn');
      } else {
        const text = err instanceof Error ? err.message : 'Agent 执行失败';
        pushToast(text, 'error');
      }
    } finally {
      setRunningAgents((cur) => {
        const next = new Set(cur);
        next.delete(id);
        return next;
      });
    }
  }

  async function handleDecide(payload: { decision: string; reasoning: string }) {
    if (!selected) return;
    try {
      pushToast(`提交决策：${payload.decision}`, 'info');
      const result = await workitemAction(selected.id, 'decide', payload);
      setWorkflow((cur) => ({
        ...cur,
        nodes: cur.nodes.map((n) => (n.id === selected.id ? result.workitem : n)),
      }));
      const followupTrigger = result.followup?.agent_result?.next_trigger;
      pushToast(
        followupTrigger
          ? `决策已提交，Agent 续跑 → ${followupTrigger}`
          : '决策已提交',
        'success',
      );
      setDrawerOpen(false);
      refreshTrace(selected.id);
    } catch (err) {
      const text = err instanceof Error ? err.message : '决策提交失败';
      pushToast(text, 'error');
    }
  }

  async function handleAcceptance(criterionId: string, checked: boolean) {
    if (!selected) return;
    try {
      const result = await updateAcceptance(selected.id, criterionId, checked);
      setWorkflow((cur) => ({
        ...cur,
        nodes: cur.nodes.map((n) => (n.id === selected.id ? result.workitem : n)),
      }));
      pushToast(`验收项已${checked ? '勾选' : '取消'}`, 'success');
      refreshCatalogs();
    } catch (err) {
      const text = err instanceof Error ? err.message : '验收更新失败';
      pushToast(text, 'error');
    }
  }

  async function handleRoleSwitch(token: string) {
    setStoredToken(token);
    setAuthToken(token);
    try {
      const user = await fetchWhoAmI();
      setCurrentUser(user);
      pushToast(`已切换到 ${user.role}`, 'success');
      refreshCatalogs();
      refreshWorkflow(currentWorkflowId);
    } catch (err) {
      const text = err instanceof Error ? err.message : '身份切换失败';
      pushToast(text, 'error');
    }
  }

  async function handleWorkflowSelect(nextWorkflowId: string) {
    const data = await refreshWorkflow(nextWorkflowId);
    if (!data) return;
    setCurrentWorkflowId(nextWorkflowId);
    setSelectedId(data.nodes[0]?.id ?? selectedId);
    setView('canvas');
  }

  async function handleInstantiateTemplate(templateId: string, title?: string) {
    try {
      const next = await instantiateTemplate(templateId, title);
      setWorkflow(next);
      setCurrentWorkflowId(next.id);
      setSelectedId(next.nodes[0]?.id ?? selectedId);
      setView('canvas');
      pushToast(`已实例化模板：${next.title}`, 'success');
      refreshCatalogs();
      refreshMetrics();
    } catch (err) {
      const text = err instanceof Error ? err.message : '模板实例化失败';
      pushToast(text, 'error');
    }
  }

  async function handleRecommend(capability: string) {
    try {
      const items = await recommendExecutors(capability);
      setRecommendedExecutors(items);
    } catch (err) {
      const text = err instanceof Error ? err.message : '推荐失败';
      pushToast(text, 'error');
    }
  }

  async function handlePreviewArtifact(artifact: Artifact) {
    try {
      setPreviewArtifact(artifact);
      const data = await fetchArtifactContent(artifact.id);
      setPreviewData({
        content: String(data.content ?? ''),
        content_type: String(data.content_type ?? 'text/plain'),
      });
    } catch (err) {
      setPreviewData(null);
      const text = err instanceof Error ? err.message : '产物预览失败';
      pushToast(text, 'error');
    }
  }

  return (
    <div className="mira-app">
      <TopNav
        view={view}
        onChange={setView}
        streamLive={streamLive}
        currentUser={currentUser}
        authToken={authToken}
        workflows={workflows}
        currentWorkflowId={currentWorkflowId}
        runtime={runtime}
        onWorkflowSelect={handleWorkflowSelect}
        onRoleSwitch={handleRoleSwitch}
      />
      {error && <div className="toast">{error}</div>}
      <div className="toast-stack">
        {toasts.map((t) => (
          <div key={t.id} className={`toast-item tone-${t.tone}`}>{t.text}</div>
        ))}
      </div>

      <main className="mira-shell">
        {view === 'deck' && (
          <CommandDeck
            workflow={workflow}
            workflows={workflows}
            templates={templates}
            metrics={metrics}
            events={events}
            audit={audit}
            runningAgents={runningAgents}
            onOpenItem={(id) => { setSelectedId(id); setView('detail'); }}
            onRunAgent={handleRunAgent}
            onOpenWorkflow={handleWorkflowSelect}
            onInstantiateTemplate={handleInstantiateTemplate}
          />
        )}
        {view === 'canvas' && (
          <WorkflowCanvas
            workflow={workflow}
            overview={overview}
            events={events}
            selectedId={selected.id}
            runningAgents={runningAgents}
            onSelect={setSelectedId}
            onOpenDetail={() => setView('detail')}
            onRunAgent={handleRunAgent}
            onWorkflowAction={handleWorkflowAction}
          />
        )}
        {view === 'detail' && selected && (
          <WorkitemDetail
            item={selected}
            events={events.filter((e) => e.workitem_id === selected.id)}
            trace={trace}
            onAction={runAction}
            onRunAgent={() => handleRunAgent(selected.id)}
            isRunning={runningAgents.has(selected.id)}
            onBack={() => setView('canvas')}
            onPreviewArtifact={handlePreviewArtifact}
            onAcceptanceChange={handleAcceptance}
          />
        )}
        {view === 'resources' && (
          <ResourcesPlaceholder
            executors={executors}
            recommendedExecutors={recommendedExecutors}
            onRecommend={handleRecommend}
          />
        )}
        {view === 'observability' && (
          <ObservabilityView
            metrics={metrics}
            alerts={metrics?.alerts ?? []}
            jobs={jobs}
            alertOutbox={alertOutbox}
            alertClusters={alertClusters}
            alertSilences={alertSilences}
            alertOpsTasks={alertOpsTasks}
            refreshingAlertOpsTasks={refreshingAlertOpsTasks}
            currentUser={currentUser}
            runtime={runtime}
            health={runtimeHealth}
            onRefresh={() => {
              refreshMetrics();
              refreshCatalogs();
              refreshJobs();
            }}
            onRetryJob={async (jobId) => {
              await retryJob(jobId);
              refreshJobs();
            }}
            onCancelJob={async (jobId) => {
              await cancelJob(jobId);
              refreshJobs();
            }}
            onReplayJob={async (jobId, payload) => {
              await replayJob(jobId, payload);
              refreshJobs();
            }}
            onResendAlert={async (alertId) => {
              await resendAlert(alertId);
              refreshJobs();
            }}
            onResendAlerts={async (status) => {
              await resendAlerts({ status });
              refreshJobs();
            }}
            onResendAlertCluster={async (clusterKey) => {
              await resendAlerts({ cluster_key: clusterKey });
              refreshJobs();
            }}
            onSilenceAlertCluster={async (clusterKey) => {
              await silenceAlertCluster(clusterKey, runtime?.alerting_stats?.silence_default_sec ?? 600);
              refreshJobs();
            }}
            onUnsilenceAlertCluster={async (clusterKey) => {
              await unsilenceAlertCluster(clusterKey);
              refreshJobs();
            }}
            onUnsilenceAlertClusters={async (clusterKeys) => {
              await unsilenceAlertClusters(clusterKeys);
              refreshJobs();
            }}
            onRefreshAlertOpsTasks={() => refreshAlertOpsTasks(true)}
            onHumanTakeover={async (job) => {
              await humanTakeoverJob(job.id, {
                new_owner: currentUser?.id,
                note: `Operator ${currentUser?.name ?? currentUser?.id ?? 'unknown'} took over dead-letter job ${job.id}`,
              });
              refreshCatalogs();
              refreshJobs();
            }}
            onEscalateTask={async (job) => {
              const result = await escalateJobToTask(job.id, {
                severity: job.last_failure_kind === 'timeout' ? 'critical' : 'warning',
              });
              refreshCatalogs();
              refreshJobs();
              return result.task;
            }}
            onOpenWorkflow={() => setView('canvas')}
          />
        )}
      </main>

      {previewArtifact && (
        <ArtifactPreview
          artifact={previewArtifact}
          previewData={previewData}
          allVersions={selected?.artifacts.filter((a) => a.title === previewArtifact.title) ?? []}
          onClose={() => {
            setPreviewArtifact(null);
            setPreviewData(null);
          }}
          onPick={handlePreviewArtifact}
        />
      )}

      {drawerOpen && selected?.decision_gate && (
        <DecisionDrawer
          item={selected}
          onClose={() => setDrawerOpen(false)}
          onDecide={handleDecide}
          onTakeover={() => runAction('takeover')}
        />
      )}
    </div>
  );
}

/* ---------- Top navigation ---------- */
function TopNav({
  view,
  onChange,
  streamLive,
  currentUser,
  authToken,
  workflows,
  currentWorkflowId,
  runtime,
  onWorkflowSelect,
  onRoleSwitch,
}: {
  view: ViewKey;
  onChange: (v: ViewKey) => void;
  streamLive: boolean;
  currentUser: CurrentUser | null;
  authToken: string;
  workflows: Workflow[];
  currentWorkflowId: string;
  runtime: RuntimeStatus | null;
  onWorkflowSelect: (id: string) => void;
  onRoleSwitch: (token: string) => void;
}) {
  const tabs: Array<{ key: ViewKey; label: string }> = [
    { key: 'deck', label: 'Command Deck 调度台' },
    { key: 'canvas', label: 'Workbench 工作台' },
    { key: 'detail', label: 'Projects 项目' },
    { key: 'observability', label: 'Observability 观测' },
    { key: 'resources', label: 'Resources 资源' },
  ];
  return (
    <header className="top-nav">
      <div className="brand">
        <span className="brand-mark" />
        <span className="brand-name">PMO 智能座舱</span>
      </div>
      <nav className="tab-pill">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            className={tab.key === view ? 'tab active' : 'tab'}
            onClick={() => onChange(tab.key)}
          >
            {tab.label}
          </button>
        ))}
      </nav>
      <div className="top-tools">
        <select
          className="top-select"
          value={currentWorkflowId}
          onChange={(e) => onWorkflowSelect(e.target.value)}
          title="切换当前工作流"
        >
          {workflows.map((wf) => (
            <option key={wf.id} value={wf.id}>{wf.title}</option>
          ))}
        </select>
        <select
          className="top-select"
          value={authToken}
          onChange={(e) => onRoleSwitch(e.target.value)}
          title="切换 demo 身份"
        >
          <option value="demo-operator">operator</option>
          <option value="demo-owner">owner</option>
          <option value="demo-admin">admin</option>
        </select>
        <span className={`live-dot ${streamLive ? 'on' : 'off'}`} title={streamLive ? '事件流已连接' : '事件流离线'}>
          <span /> {streamLive ? 'Live' : 'Offline'}
        </span>
        {runtime && (
          <span
            className="live-dot on"
            title={`delivery=${runtime.delivery_provider}, notify=${runtime.notification_provider}, exec=${runtime.execution_mode}, queued=${runtime.job_stats?.queued ?? 0}, running=${runtime.job_stats?.running ?? 0}`}
          >
            <span /> {runtime.delivery_provider}/{runtime.execution_mode}/{runtime.job_stats?.running ?? 0}
          </span>
        )}
        <button className="icon-btn" aria-label="通知">
          <span className="icon-dot" />
          <Bell />
        </button>
        <button className="icon-btn" aria-label="数据">
          <Stack />
        </button>
        <button className="icon-btn" aria-label="设置">
          <Gear />
        </button>
        <span className="avatar" title={currentUser ? `${currentUser.name} (${currentUser.role})` : 'unknown'}>
          {currentUser?.role?.slice(0, 2).toUpperCase() ?? 'NA'}
        </span>
      </div>
    </header>
  );
}

/* ---------- Command Deck ---------- */
function CommandDeck({
  workflow,
  workflows,
  templates,
  metrics,
  events,
  audit,
  runningAgents,
  onOpenItem,
  onRunAgent,
  onOpenWorkflow,
  onInstantiateTemplate,
}: {
  workflow: Workflow;
  workflows: Workflow[];
  templates: WorkflowTemplate[];
  metrics: MetricsResponse | null;
  events: DomainEvent[];
  audit: Array<Record<string, unknown>>;
  runningAgents: Set<string>;
  onOpenItem: (id: string) => void;
  onRunAgent: (id: string) => void;
  onOpenWorkflow: (id: string) => void;
  onInstantiateTemplate: (templateId: string, title?: string) => void;
}) {
  const pending = useMemo(
    () =>
      workflow.nodes
        .filter((n) => n.decision_gate || n.state === 'awaiting_decision')
        .slice(0, 3),
    [workflow],
  );

  const risks = metrics?.alerts?.slice(0, 4).map((a) => ({
    tone: a.name.endsWith('exhausted') || a.name.includes('escalated') ? 'red' : 'amber',
    level: a.name.endsWith('exhausted') ? 'Critical' : 'Medium',
    label: `${a.name} · ${a.workitem_id ?? a.workflow_id ?? '-'}`,
    cta: 'Open',
  })) ?? [];
  const capacity = workflow.executors.length ? workflow.executors : [];
  const flows = templates.map((tpl, index) => ({
    id: tpl.id,
    name: tpl.title,
    count: workflows.filter((wf) => wf.template_id === tpl.id).length,
    color: ['var(--accent-blue)', '#9aa0a6', 'var(--accent-orange)', 'var(--accent-green)'][index % 4],
    pts: ['0 22 12 18 24 14 36 11 48 9 60 6', '0 14 12 16 24 13 36 12 48 14 60 11', '0 20 12 16 24 18 36 14 48 10 60 7', '0 16 12 14 24 11 36 13 48 9 60 7'][index % 4],
  }));
  const totalHours = metrics ? Math.round(metrics.budget.time_sec.used / 3600) : 0;
  const totalCost = metrics?.budget.cost_usd.used ?? 0;
  const humanCount = workflow.executors.filter((e) => e.type === 'human').length;
  const agentCount = workflow.executors.filter((e) => e.type === 'agent').length;

  return (
    <div className="deck-grid">
      <Panel area="A" title="Pending My Decisions 待我决策" badge={`${pending.length}`}>
        <ul className="decision-list">
          {pending.map((p, i) => (
            <li key={p.id}>
              <span className={`bullet color-${i % 4}`} />
              <div className="dl-main" onClick={() => onOpenItem(p.id)}>
                <div className="dl-title">{p.title}</div>
                <div className="dl-sub">Requested by {p.assignee.name} · 状态 {p.state}</div>
              </div>
              <span className="wait-pill">Waiting for {1 + i}d {2 * i}h</span>
              <button
                className="ghost-btn"
                onClick={(e) => { e.stopPropagation(); onRunAgent(p.id); }}
                title="让 Agent 跑一遍"
                disabled={
                  runningAgents.has(p.id) ||
                  p.state === 'approved' || p.state === 'rejected' || p.state === 'cancelled' ||
                  p.state === 'submitted' || p.state === 'awaiting_decision'
                }
              >
                {runningAgents.has(p.id) ? '⏳ Running' : '▶ Run'}
              </button>
            </li>
          ))}
        </ul>
        <button className="cta-btn" onClick={() => pending[0] && onOpenItem(pending[0].id)}>
          View &amp; Decide ↗
        </button>
      </Panel>

      <Panel area="B" title="Risk Radar 风险雷达">
        <ul className="risk-list">
          {risks.map((r) => (
            <li key={r.label}>
              <span className={`risk-dot tone-${r.tone}`} />
              <span className="risk-text">
                <strong>{r.level}:</strong> {r.label}
              </span>
              <button className="ghost-btn">{r.cta}</button>
            </li>
          ))}
          {risks.length === 0 && <li className="event-empty">暂无高优先风险</li>}
        </ul>
      </Panel>

      <Panel area="C" title="Capacity View 产能视图">
        <div className="cap-table">
          <div className="cap-head">
            <span>Executor</span>
            <span>Type</span>
            <span>Load</span>
            <span>Capabilities</span>
            <span>Cost/h</span>
            <span>Pass rate</span>
            <span>Status</span>
          </div>
          {capacity.map((e) => {
            const status = e.current_load >= 80 ? 'Busy' : e.current_load >= 50 ? 'Busy' : 'Available';
            const offline = e.current_load === 0;
            return (
              <div className="cap-row" key={e.id}>
                <span className="cap-name">
                  <span className={`exec-ico ${e.type}`}>{e.type === 'human' ? '🧑' : '🤖'}</span>
                  {e.name}
                </span>
                <span className={`type-pill ${e.type}`}>{e.type === 'human' ? 'Human' : 'AI Agent'}</span>
                <span className="cap-load">
                  <span className="load-bar">
                    <span style={{ width: `${e.current_load}%` }} />
                  </span>
                  {e.current_load}%
                </span>
                <span className="cap-tags">
                  {e.capabilities.slice(0, 2).map((c) => (
                    <span key={c.tag} className="tag">
                      {c.tag}
                    </span>
                  ))}
                </span>
                <span>¥{e.unit_cost}/h</span>
                <span>{(e.success_rate * 100).toFixed(e.success_rate >= 0.99 ? 1 : 0)}%</span>
                <span className={`status ${offline ? 'offline' : status === 'Busy' ? 'busy' : 'ok'}`}>
                  ● {offline ? 'Offline' : status}
                </span>
              </div>
            );
          })}
        </div>
      </Panel>

      <Panel area="D" title="Workflow Library 流程库">
        <div className="flow-grid">
          {flows.map((f) => (
            <div className="flow-card" key={f.id}>
              <div className="flow-meta">
                <strong>{f.name}</strong>
                <span>Used {f.count} times</span>
              </div>
              <svg viewBox="0 0 60 24" className="spark">
                <polyline fill="none" stroke={f.color} strokeWidth="1.4" points={f.pts} />
                <polyline
                  fill={f.color}
                  opacity="0.12"
                  points={`${f.pts} 60 24 0 24`}
                />
              </svg>
              <div className="flow-actions">
                <button className="ghost-btn" onClick={() => onInstantiateTemplate(f.id, `${f.name} 实例`)}>
                  Instantiate
                </button>
              </div>
            </div>
          ))}
          {flows.length === 0 && <div className="event-empty">暂无模板</div>}
        </div>
      </Panel>

      <Panel area="E" title="Weekly Project Pulse 本周项目脉搏">
        <div className="pulse">
          <div className="pulse-stat">
            <strong>{totalHours}h<small className="up">↑</small></strong>
            <span>Total Hours Logged</span>
          </div>
          <div className="pulse-stat">
            <strong>${totalCost.toFixed(2)}<small className="down">↓</small></strong>
            <span>Estimated Cost</span>
          </div>
          <div className="pulse-stat">
            <strong>{humanCount}:{agentCount}<small className="flat">→</small></strong>
            <span>Human/AI Ratio</span>
          </div>
        </div>
        <div className="event-log">
          <div className="event-log-head">
            <strong>Live Event Stream</strong>
            <span>{events.length} events</span>
          </div>
          <ul>
            {events.slice(-6).reverse().map((e) => (
              <li key={e.seq}>
                <code>{e.name}</code>
                <span className="ev-meta">{e.workitem_id ?? e.workflow_id ?? '-'}</span>
                <time>{new Date(e.timestamp).toLocaleTimeString()}</time>
              </li>
            ))}
            {events.length === 0 && <li className="event-empty">尚未收到事件，触发 Run Agent 试试</li>}
          </ul>
        </div>
        <div className="event-log" style={{ marginTop: 10 }}>
          <div className="event-log-head">
            <strong>Recent Audit</strong>
            <span>{audit.length} items</span>
          </div>
          <ul>
            {audit.slice(-4).reverse().map((entry, idx) => (
              <li key={`${String(entry.id ?? idx)}`}>
                <code>{String(entry.action ?? '-')}</code>
                <span className="ev-meta">{String(entry.actor ?? '-')}</span>
                <time>{new Date(String(entry.timestamp ?? Date.now())).toLocaleTimeString()}</time>
              </li>
            ))}
            {audit.length === 0 && <li className="event-empty">暂无审计事件</li>}
          </ul>
        </div>
        <div className="pulse-foot">
          <button className="ghost-btn" onClick={() => onOpenWorkflow(workflow.id)}>Open Current Workflow</button>
        </div>
      </Panel>
    </div>
  );
}

function Panel({
  title,
  badge,
  area,
  children,
}: {
  title: string;
  badge?: string;
  area: string;
  children: React.ReactNode;
}) {
  return (
    <section className="panel" data-area={area}>
      <div className="panel-marker">{area}</div>
      <div className="panel-head">
        <h3>
          {title}
          {badge && <span className="title-badge">({badge})</span>}
        </h3>
      </div>
      <div className="panel-body">{children}</div>
    </section>
  );
}

/* ---------- Workflow Canvas ---------- */
function WorkflowCanvas({
  workflow,
  overview,
  events,
  selectedId,
  runningAgents,
  onSelect,
  onOpenDetail,
  onRunAgent,
  onWorkflowAction,
}: {
  workflow: Workflow;
  overview: WorkflowOverview | null;
  events: DomainEvent[];
  selectedId: string;
  runningAgents: Set<string>;
  onSelect: (id: string) => void;
  onOpenDetail: () => void;
  onRunAgent: (id: string) => void;
  onWorkflowAction: (action: 'start' | 'pause' | 'resume' | 'complete' | 'cancel') => void;
}) {
  const tabs = ['概览', '执行链', '看板', '甘特', '报表'];
  const focused = workflow.nodes.find((n) => n.id === selectedId) ?? workflow.nodes[0];
  const tail = focused
    ? `${focused.assignee.name} · ${focused.state}`
    : '待启动';
  const allowed = new Set(overview?.allowed_actions ?? []);
  const progress = overview?.progress_pct ?? 0;
  const costUsed = overview?.budget.cost_used_usd ?? 0;
  const costCap = overview?.budget.cost_cap_usd ?? 0;
  const costPct = overview?.budget.cost_pct ?? 0;

  return (
    <section className="canvas-view">
      {/* WF-CTL: 工作流总控条 */}
      <div className="wf-ctl">
        <div className="wf-ctl-left">
          <span className={`wf-state wf-${overview?.state ?? 'running'}`}>
            ● {overview?.state ?? 'running'}
          </span>
          <strong>{workflow.title}</strong>
          <span className="wf-meta">
            SLA {overview?.sla ?? workflow.sla} · 回滚 {overview?.rollback_policy ?? workflow.rollback_policy}
          </span>
        </div>
        <div className="wf-ctl-center">
          <div className="wf-progress" title={`完成 ${overview?.completed_nodes ?? 0}/${overview?.total_nodes ?? workflow.nodes.length}`}>
            <span style={{ width: `${progress}%` }} />
            <em>{progress}%</em>
          </div>
          <div className="wf-chips">
            <span className="chip-xs">✔ {overview?.completed_nodes ?? 0} 完成</span>
            <span className="chip-xs warn">⧖ {overview?.decision_pending ?? 0} 待决策</span>
            <span className="chip-xs danger">⊘ {overview?.blocked_nodes ?? 0} 阻塞</span>
          </div>
        </div>
        <div className="wf-ctl-right">
          <button
            className="primary"
            onClick={() => onWorkflowAction('start')}
            disabled={!allowed.has('start')}
            title={!allowed.has('start') ? '当前状态不允许启动' : ''}
          >
            ▶ 启动
          </button>
          <button
            onClick={() => onWorkflowAction('pause')}
            disabled={!allowed.has('pause')}
          >
            ⏸ 暂停
          </button>
          <button
            onClick={() => onWorkflowAction('resume')}
            disabled={!allowed.has('resume')}
          >
            ⏵ 恢复
          </button>
          <button
            onClick={() => onWorkflowAction('complete')}
            disabled={!allowed.has('complete')}
          >
            ✓ 完结
          </button>
          <button
            className="danger"
            onClick={() => onWorkflowAction('cancel')}
            disabled={!allowed.has('cancel')}
          >
            ⏹ 取消
          </button>
        </div>
      </div>

      <div className="canvas-bar">
        <button className="link-btn">← Workflow Canvas 执行链视图 · {workflow.title}</button>
        <div className="budget">
          <span>{`$${costUsed.toFixed(2)} / $${costCap.toFixed(2)}`}</span>
          <span className="budget-bar">
            <span style={{ width: `${Math.min(100, costPct)}%` }} />
          </span>
          <span className="budget-pct">{costPct}%</span>
        </div>
      </div>
      <div className="canvas-tabs">
        {tabs.map((t, i) => (
          <button key={t} className={i === 1 ? 'tab active' : 'tab'}>
            {t}
          </button>
        ))}
      </div>

      <div className="canvas-board">
        <div className="chain">
          {workflow.nodes.map((node, idx) => {
            const status = node.state === 'approved' || node.state === 'submitted'
              ? 'done'
              : node.state === 'in_progress'
                ? 'active'
                : node.state === 'awaiting_decision'
                  ? 'ok'
                  : 'idle';
            const kind = node.assignee.type === 'human' ? 'human' : 'agent';
            return (
              <div className="chain-cell" key={node.id}>
                <ChainNode
                  kind={node.decision_gate ? 'diamond' : kind}
                  name={node.title}
                  tail={`${node.assignee.name} · ${node.state}`}
                  status={status}
                  highlighted={node.id === selectedId}
                  onClick={() => onSelect(node.id)}
                />
                <button
                  className="run-mini"
                  onClick={(e) => { e.stopPropagation(); onRunAgent(node.id); }}
                  disabled={
                    runningAgents.has(node.id) ||
                    node.state === 'approved' || node.state === 'rejected' || node.state === 'cancelled' ||
                    node.state === 'submitted' || node.state === 'awaiting_decision'
                  }
                >
                  {runningAgents.has(node.id) ? '⏳ Running' : '▶ Run Agent'}
                </button>
                {idx < workflow.nodes.length - 1 && <Connector />}
              </div>
            );
          })}
        </div>
      </div>

      <div className="live-stream-card">
        <div className="ls-head">
          <strong>Live Execution Stream 实时执行流</strong>
          <span className="ls-sub">{focused?.title} · {tail}</span>
        </div>
        <ul className="stream">
          {events.slice(-8).reverse().map((e) => (
            <li key={e.seq} className={e.name.startsWith('agent.') ? 'active' : ''}>
              <time>{new Date(e.timestamp).toLocaleTimeString()}</time>
              <code>{e.name}</code>
              <span>{(e.payload as { summary?: string })?.summary ?? e.workitem_id ?? ''}</span>
            </li>
          ))}
          {events.length === 0 && <li>尚无事件，可点 Run Agent 触发</li>}
        </ul>
        <div className="ls-foot">
          <span>共 {events.length} 条事件</span>
          <div className="ls-actions">
            <button
              onClick={() => focused && onRunAgent(focused.id)}
              disabled={!focused || runningAgents.has(focused.id)}
            >
              {focused && runningAgents.has(focused.id) ? '⏳ Running' : '▶ Run Agent'}
            </button>
            <button onClick={onOpenDetail}>↪ 进入详情</button>
          </div>
        </div>
      </div>
    </section>
  );
}

function ChainNode({
  kind,
  name,
  tail,
  status,
  highlighted,
  onClick,
}: {
  kind: string;
  name: string;
  tail: string;
  status: 'done' | 'active' | 'idle' | 'ok';
  highlighted?: boolean;
  onClick?: () => void;
}) {
  const isDiamond = kind.startsWith('diamond');
  return (
    <button
      className={`chain-node ${kind} status-${status} ${highlighted ? 'glow' : ''}`}
      onClick={onClick}
    >
      {isDiamond ? (
        <div className="diamond">
          <div className="diamond-inner">
            <strong>{name}</strong>
            {tail && <small>{tail}</small>}
          </div>
        </div>
      ) : (
        <>
          <div className="cn-head">
            {kind === 'agent' ? <span className="ico">🤖</span> : <span className="ico">🧑</span>}
            <strong>{name}</strong>
            {status === 'done' && <span className="check">✓</span>}
          </div>
          <small>{tail}</small>
        </>
      )}
    </button>
  );
}

function Connector() {
  return <span className="connector" aria-hidden />;
}

/* ---------- Workitem Detail ---------- */
function WorkitemDetail({
  item,
  events,
  trace,
  onAction,
  onRunAgent,
  isRunning,
  onBack,
  onPreviewArtifact,
  onAcceptanceChange,
}: {
  item: Workitem;
  events: DomainEvent[];
  trace: Trace | null;
  onAction: (a: WorkitemAction) => void;
  onRunAgent: () => void;
  isRunning: boolean;
  onBack: () => void;
  onPreviewArtifact: (a: Artifact) => void;
  onAcceptanceChange: (criterionId: string, checked: boolean) => void;
}) {
  const checked = item.acceptance_criteria.filter((c) => c.checked).length;
  const total = item.acceptance_criteria.length;
  const progress = total ? Math.round((checked / total) * 100) : 0;

  const terminal = ['approved', 'rejected', 'cancelled', 'submitted'];
  const canRun = !terminal.includes(item.state) && item.state !== 'awaiting_decision';

  const budget = item.budget;
  const costPct = budget.cost_cap_usd ? Math.min(100, Math.round((budget.cost_used_usd / budget.cost_cap_usd) * 100)) : 0;
  const timeCapSec = (budget.time_cap_min ?? 0) * 60;
  const timePct = timeCapSec ? Math.min(100, Math.round((budget.time_used_sec / timeCapSec) * 100)) : 0;
  const tokenPct = budget.token_cap ? Math.min(100, Math.round((budget.tokens_used / budget.token_cap) * 100)) : 0;

  const ringPct = item.state === 'approved' || item.state === 'submitted' ? 100 : Math.max(progress, timePct);

  return (
    <section className="detail-view">
      <button className="link-btn" onClick={onBack}>
        ← 返回执行链 · #{item.id}
      </button>
      <h1 className="detail-title">Workitem #{item.id} · {item.title}</h1>

      <div className="detail-grid">
        <article className="card contract">
          <h3>Contract 契约</h3>
          <h4>目标</h4>
          <p>{item.goal}</p>
          <h4>输入</h4>
          <div className="chip-row">
            {item.inputs.length === 0 && <span className="chip ghost">（无指定输入）</span>}
            {item.inputs.map((raw, idx) => {
              const i = raw as { name?: string; version?: string };
              return (
                <span className="chip" key={`${i.name ?? 'in'}-${idx}`}>
                  📄 {i.name ?? '(unnamed)'}{i.version ? ` ${i.version}` : ''}
                </span>
              );
            })}
          </div>
          <h4>期望产出</h4>
          <ul className="bullet-list">
            {item.expected_outputs.map((raw, idx) => {
              const o = raw as { name?: string; format?: string };
              return (
                <li key={`${o.name ?? 'out'}-${idx}`}>
                  {o.name ?? '(unnamed)'}{o.format ? ` (${o.format})` : ''}
                </li>
              );
            })}
          </ul>
          <h4>验收标准</h4>
          <ul className="check-list">
            {item.acceptance_criteria.map((c) => (
              <li key={c.id}>
                <label className="check-editable">
                  <input
                    type="checkbox"
                    checked={c.checked}
                    onChange={(e) => onAcceptanceChange(c.id, e.target.checked)}
                  /> {c.label}
                </label>
              </li>
            ))}
          </ul>
          <h4>预算</h4>
          <BudgetBar label="cost" value={costPct} text={`$${budget.cost_used_usd}/$${budget.cost_cap_usd} ${costPct}%`} />
          <BudgetBar label="time" value={timePct} text={`${budget.time_used_sec}s/${budget.time_cap_min}min`} />
          <BudgetBar label="tokens" value={tokenPct} text={`token ${budget.tokens_used}/${budget.token_cap}`} />
        </article>

        <div className="right-stack">
          <article className="card cockpit">
            <div className="cockpit-head">
              <h3>Live Cockpit 执行舱</h3>
              <div className="cockpit-meta">
                <span>Executor: {item.assignee.type === 'agent' ? '🤖' : '🧑'} {item.assignee.name}</span>
                <span>State: {item.state}</span>
              </div>
            </div>
            <div className="cockpit-body">
              <div className="ring">
                <svg viewBox="0 0 100 100">
                  <circle cx="50" cy="50" r="42" fill="none" stroke="#e9eaee" strokeWidth="8" />
                  <circle
                    cx="50"
                    cy="50"
                    r="42"
                    fill="none"
                    stroke="var(--accent-blue)"
                    strokeWidth="8"
                    strokeDasharray={`${(ringPct * 2.64).toFixed(1)} 999`}
                    strokeLinecap="round"
                    transform="rotate(-90 50 50)"
                  />
                </svg>
                <div className="ring-text">
                  <strong>{ringPct}%</strong>
                  <small>{item.state}</small>
                </div>
              </div>
              <div className="trace">
                <div className="trace-head">
                  <strong>Real-time Trace</strong>
                  <span className="badge-blue">{trace?.entries.length ?? 0} Trace</span>
                </div>
                <ol>
                  {(trace?.entries ?? []).slice(-10).map((entry, idx) => (
                    <li key={`${entry.timestamp}-${idx}`} className={entry.status === 'succeeded' ? 'active' : ''}>
                      [{new Date(entry.timestamp).toLocaleTimeString()}] {entry.action}
                      {entry.tool_used ? ` · ${entry.tool_used}` : ''}
                      {entry.duration ? ` · ${entry.duration.toFixed(1)}s` : ''}
                    </li>
                  ))}
                  {(!trace || trace.entries.length === 0) && <li className="muted">暂无 Trace，点击 ▶ Run Agent 触发执行</li>}
                </ol>
              </div>
            </div>
            <div className="cockpit-actions">
              <button
                className="primary"
                onClick={onRunAgent}
                disabled={!canRun || isRunning}
              >
                {isRunning ? '⏳ Running...' : '▶ Run Agent'}
              </button>
              <button onClick={() => onAction('start')} disabled={item.state !== 'queued'}>启动</button>
              <button onClick={() => onAction('pause')} disabled={item.state !== 'in_progress'}>暂停</button>
              <button onClick={() => onAction('takeover')}>接管</button>
              <button onClick={() => onAction('submit')} disabled={item.state !== 'in_progress'}>提交</button>
              <button onClick={() => onAction('approve')} disabled={item.state !== 'submitted'}>通过</button>
              <button onClick={() => onAction('reject')}>终止</button>
            </div>
          </article>

          <article className="card artifacts">
            <h3>Artifacts 产物 ({item.artifacts.length})</h3>
            {item.artifacts.length === 0 && (
              <div className="artifact ghost">
                <span className="art-ico ghost-ico">…</span>
                <div>暂无产物，等待 Agent 产出</div>
              </div>
            )}
            {item.artifacts.map((a) => (
              <div className="artifact" key={a.id}>
                <span className="art-ico">{a.type.slice(0, 1).toUpperCase()}</span>
                <div>
                  <strong>{a.title}</strong>
                  <small>{a.type} · v{a.version} · 置信度 {Math.round(a.confidence * 100)}%</small>
                  {!!a.external_refs?.length && (
                    <div className="artifact-links">
                      {a.external_refs.map((ref) => (
                        <a key={`${a.id}-${ref.kind}-${ref.url}`} href={resolveArtifactUrl(ref.url)} target="_blank" rel="noreferrer">
                          {ref.label}
                        </a>
                      ))}
                    </div>
                  )}
                </div>
                <div className="art-actions">
                  <button title="下载" onClick={() => window.open(resolveArtifactUrl(a.uri), '_blank')}>⬇</button>
                  <button title="预览" onClick={() => onPreviewArtifact(a)}>👁</button>
                </div>
              </div>
            ))}
          </article>

          <article className="card audit">
            <AuditTabs item={item} events={events} />
          </article>
        </div>
      </div>
    </section>
  );
}

function BudgetBar({ label, value, text }: { label: string; value: number; text: string }) {
  return (
    <div className="budget-row">
      <span className="bb-label">{label}</span>
      <span className="bb-bar">
        <span style={{ width: `${value}%` }} />
      </span>
      <span className="bb-text">{text}</span>
    </div>
  );
}

function AuditTabs({ item, events }: { item: Workitem; events: DomainEvent[] }) {
  const [tab, setTab] = useState<'events' | 'rejections'>('events');
  return (
    <>
      <div className="seg">
        <button className={tab === 'events' ? 'seg-tab active' : 'seg-tab'} onClick={() => setTab('events')}>
          事件流 ({events.length})
        </button>
        <button className={tab === 'rejections' ? 'seg-tab active' : 'seg-tab'} onClick={() => setTab('rejections')}>
          驳回历史 ({item.rejection_history?.length ?? 0})
        </button>
      </div>
      <ul className="mini-audit">
        {tab === 'events' && events.slice(-6).reverse().map((e) => (
          <li key={e.seq}>
            <code>{e.name}</code>
            <time>{new Date(e.timestamp).toLocaleTimeString()}</time>
          </li>
        ))}
        {tab === 'rejections' && (item.rejection_history ?? []).slice(-6).reverse().map((entry, idx) => (
          <li key={`${String(entry.timestamp ?? idx)}`}>
            <code>{String(entry.reason ?? 'rejected')}</code>
            <time>{new Date(String(entry.timestamp ?? Date.now())).toLocaleTimeString()}</time>
          </li>
        ))}
        {tab === 'events' && events.length === 0 && <li className="muted">暂无事件</li>}
        {tab === 'rejections' && (item.rejection_history?.length ?? 0) === 0 && <li className="muted">暂无驳回历史</li>}
      </ul>
    </>
  );
}

/* ---------- Decision Drawer ---------- */
function DecisionDrawer({
  item,
  onClose,
  onDecide,
  onTakeover,
}: {
  item: Workitem;
  onClose: () => void;
  onDecide: (payload: { decision: string; reasoning: string }) => void;
  onTakeover: () => void;
}) {
  const fallbackOptions = ['继续推进', '微调后再产出', '终止重做'];
  const options = item.decision?.options?.length ? item.decision.options : fallbackOptions;
  const [selected, setSelected] = useState(options[0]);
  const [reasoning, setReasoning] = useState('');

  // 当 item 切换 / decision 更新时，重置选中项
  const optionsKey = options.join('|');
  useEffect(() => {
    setSelected(options[0]);
    setReasoning('');
  }, [item.id, optionsKey]);

  const reasoningHint = item.decision?.reasoning ?? null;
  const decided = !!item.decision?.selected_option;
  const submit = () => {
    if (decided) return;
    onDecide({ decision: selected, reasoning });
  };

  const checked = item.acceptance_criteria.filter((c) => c.checked).length;
  const total = item.acceptance_criteria.length;
  const pending = total - checked;

  return (
    <aside className="decision-drawer">
      <header>
        <span className="dd-icon">✦</span>
        <strong>{item.decision?.title ?? `${item.title} · 决策门`}</strong>
        <button className="dd-close" onClick={onClose} aria-label="关闭">×</button>
      </header>
      <div className="dd-meta">
        {item.title} <span className="dd-wait">{decided ? `已选: ${item.decision?.selected_option}` : '等待你拍板'}</span>
      </div>

      <h4>Upstream Output Summary 上游产物摘要</h4>
      <div className="upstream">
        <div><strong>{item.artifacts.length}</strong><span>产物</span></div>
        <div><strong>{checked}/{total}</strong><span>验收</span></div>
        <div><strong>{pending}</strong><span>待补</span></div>
        <div className="upstream-bars">
          <span style={{ height: `${Math.max(20, item.risk_score * 100)}%`, background: 'var(--accent-orange)' }} />
          <span style={{ height: `${total ? (checked / total) * 100 : 20}%`, background: 'var(--accent-green)' }} />
          <span style={{ height: '60%', background: '#cfe2ff' }} />
        </div>
      </div>

      {reasoningHint && (
        <>
          <h4>Agent Reasoning · Agent 推理</h4>
          <pre className="dd-reasoning">{reasoningHint}</pre>
        </>
      )}

      <h4>Decision Question 待决策问题</h4>
      <p className="dd-q">{item.goal}</p>

      <h4>Options 备选方案 ({options.length})</h4>
      <div className="dd-options">
        {options.map((opt) => (
          <label
            key={opt}
            className={`dd-opt ${selected === opt ? 'active highlight' : ''}`}
          >
            <input
              type="radio"
              checked={selected === opt}
              onChange={() => setSelected(opt)}
              disabled={decided}
            />
            <div>
              <strong>{opt}</strong>
              <div className="opt-tags">
                {opt.includes('终止') && <span className="t-red">⚠ 终态</span>}
                {opt.includes('继续') && <span className="t-green">🟢 推进</span>}
                {opt.includes('微调') && <span className="t-amber">+ 1 轮</span>}
              </div>
            </div>
          </label>
        ))}
      </div>

      <h4>Reasoning Note (可选)</h4>
      <textarea
        placeholder="请输入您的思考过程（可选）"
        value={reasoning}
        onChange={(e) => setReasoning(e.target.value)}
        disabled={decided}
      />

      <footer>
        <button className="primary" onClick={submit} disabled={decided}>
          {decided ? '已决策' : `通过 · ${selected}`}
        </button>
      </footer>
      <div className="dd-foot">
        <button onClick={onClose}>稍后再说</button>
        <button onClick={onTakeover}>转人工接管</button>
      </div>
    </aside>
  );
}

function ResourcesPlaceholder({
  executors,
  recommendedExecutors,
  onRecommend,
}: {
  executors: Executor[];
  recommendedExecutors: Executor[];
  onRecommend: (capability: string) => void;
}) {
  const [capability, setCapability] = useState('竞品调研');
  return (
    <section className="resources">
      <h2>Resources 资源</h2>
      <div className="resource-toolbar">
        <p>共 {executors.length} 个执行者已注册，支持按能力推荐。</p>
        <div className="resource-search">
          <input value={capability} onChange={(e) => setCapability(e.target.value)} placeholder="输入能力关键词，如 竞品调研 / 报告起草" />
          <button onClick={() => onRecommend(capability)}>推荐</button>
        </div>
      </div>
      <div className="resource-grid">
        <article className="card obs-card">
          <h3>All Executors</h3>
          <div className="cap-table">
            <div className="cap-head">
              <span>Executor</span>
              <span>Type</span>
              <span>Load</span>
              <span>Capabilities</span>
              <span>Score</span>
              <span>Status</span>
            </div>
            {executors.map((e) => (
              <div className="cap-row" key={e.id}>
                <span className="cap-name"><span className={`exec-ico ${e.type}`}>{e.type === 'human' ? '🧑' : '🤖'}</span>{e.name}</span>
                <span className={`type-pill ${e.type}`}>{e.type}</span>
                <span className="cap-load"><span className="load-bar"><span style={{ width: `${e.current_load}%` }} /></span>{e.current_load}%</span>
                <span className="cap-tags">{e.capabilities.slice(0, 3).map((c) => <span key={c.tag} className="tag">{c.tag}</span>)}</span>
                <span>{((e as Executor & { composite_score?: number }).composite_score ?? 0).toFixed(3)}</span>
                <span className={`status ${e.current_load >= 80 ? 'busy' : 'ok'}`}>● {e.current_load >= 80 ? 'Busy' : 'Available'}</span>
              </div>
            ))}
          </div>
        </article>
        <article className="card obs-card">
          <h3>Recommended For "{capability}"</h3>
          <ul className="risk-list">
            {recommendedExecutors.map((e) => {
              const ex = e as Executor & { recommend_reasons?: string[]; composite_score?: number; match_score?: number };
              return (
                <li key={`rec-${e.id}`}>
                  <span className="risk-dot tone-blue" />
                  <span className="risk-text">
                    <strong>{e.name}</strong> · 匹配 {ex.match_score?.toFixed(2) ?? '0.00'} · 综合 {ex.composite_score?.toFixed(2) ?? '0.00'}
                    <small className="muted"> {ex.recommend_reasons?.join(' / ')}</small>
                  </span>
                </li>
              );
            })}
            {recommendedExecutors.length === 0 && <li className="event-empty">暂无推荐结果</li>}
          </ul>
        </article>
      </div>
    </section>
  );
}

/* ---------- OBS-01: Observability view ---------- */
function ObservabilityView({
  metrics,
  alerts,
  jobs,
  alertOutbox,
  alertClusters,
  alertSilences,
  alertOpsTasks,
  refreshingAlertOpsTasks,
  currentUser,
  runtime,
  health,
  onRefresh,
  onRetryJob,
  onCancelJob,
  onReplayJob,
  onResendAlert,
  onResendAlerts,
  onResendAlertCluster,
  onSilenceAlertCluster,
  onUnsilenceAlertCluster,
  onUnsilenceAlertClusters,
  onRefreshAlertOpsTasks,
  onHumanTakeover,
  onEscalateTask,
  onOpenWorkflow,
}: {
  metrics: MetricsResponse | null;
  alerts: DomainEvent[];
  jobs: JobRecord[];
  alertOutbox: AlertOutboxRecord[];
  alertClusters: AlertOutboxCluster[];
  alertSilences: AlertSilenceRecord[];
  alertOpsTasks: OpsTaskRecord[];
  refreshingAlertOpsTasks: boolean;
  currentUser: CurrentUser | null;
  runtime: RuntimeStatus | null;
  health: RuntimeHealth | null;
  onRefresh: () => void;
  onRetryJob: (jobId: string) => Promise<void>;
  onCancelJob: (jobId: string) => Promise<void>;
  onReplayJob: (jobId: string, payload: Record<string, unknown>) => Promise<void>;
  onResendAlert: (alertId: string) => Promise<void>;
  onResendAlerts: (status: 'failed' | 'buffered') => Promise<void>;
  onResendAlertCluster: (clusterKey: string) => Promise<void>;
  onSilenceAlertCluster: (clusterKey: string) => Promise<void>;
  onUnsilenceAlertCluster: (clusterKey: string) => Promise<void>;
  onUnsilenceAlertClusters: (clusterKeys: string[]) => Promise<void>;
  onRefreshAlertOpsTasks: () => Promise<void>;
  onHumanTakeover: (job: JobRecord) => Promise<void>;
  onEscalateTask: (job: JobRecord) => Promise<OpsTaskRecord>;
  onOpenWorkflow: () => void;
}) {
  const [jobFilter, setJobFilter] = useState<'all' | 'dead_lettered'>('all');
  const [outboxFilter, setOutboxFilter] = useState<'all' | 'failed' | 'buffered' | 'delivered'>('all');
  const [silenceEventFilter, setSilenceEventFilter] = useState<'all' | string>('all');
  const [silenceSort, setSilenceSort] = useState<'eta_asc' | 'eta_desc' | 'suppressed_desc'>('eta_asc');
  const [selectedSilenceKeys, setSelectedSilenceKeys] = useState<string[]>([]);
  const [focusedTaskId, setFocusedTaskId] = useState<string | null>(null);
  const [focusedClusterKeys, setFocusedClusterKeys] = useState<string[]>([]);
  const [focusedOutboxIds, setFocusedOutboxIds] = useState<string[]>([]);
  const [selectedJob, setSelectedJob] = useState<JobRecord | null>(null);
  const silenceSectionRef = useRef<HTMLElement | null>(null);
  const clusterSectionRef = useRef<HTMLElement | null>(null);
  const outboxSectionRef = useRef<HTMLElement | null>(null);
  const taskSectionRef = useRef<HTMLElement | null>(null);

  if (!metrics) {
    return (
      <section className="obs-view">
        <div className="obs-empty">
          <h2>Observability 观测</h2>
          <p>加载指标中…若后端未启动，请先启动 uvicorn。</p>
          <button className="primary" onClick={onRefresh}>重试</button>
        </div>
      </section>
    );
  }

  const s = metrics.summary;
  const b = metrics.budget;
  const deadLetters = runtime?.job_stats?.dead_lettered ?? jobs.filter((job) => job.status === 'dead_lettered').length;
  const filteredJobs = jobFilter === 'dead_lettered'
    ? jobs.filter((job) => job.status === 'dead_lettered')
    : jobs;
  const filteredOutbox = outboxFilter === 'all'
    ? alertOutbox
    : alertOutbox.filter((item) => item.status === outboxFilter);
  const silenceEventOptions = Array.from(new Set(alertSilences.map((item) => item.event_name))).sort();
  const filteredSilences = alertSilences
    .filter((item) => silenceEventFilter === 'all' || item.event_name === silenceEventFilter)
    .sort((a, b) => {
      if (silenceSort === 'suppressed_desc') {
        return (b.suppressed_count ?? 0) - (a.suppressed_count ?? 0);
      }
      const delta = new Date(a.silenced_until).getTime() - new Date(b.silenced_until).getTime();
      return silenceSort === 'eta_asc' ? delta : -delta;
    });
  const allFilteredSilenceKeys = filteredSilences.map((item) => item.cluster_key);
  const selectedSilenceCount = selectedSilenceKeys.filter((key) => allFilteredSilenceKeys.includes(key)).length;
  const actionableOutboxCount = filteredOutbox.filter((item) => item.status === 'failed' || item.status === 'buffered').length;
  const batchResendStatus: 'failed' | 'buffered' = outboxFilter === 'buffered'
    ? 'buffered'
    : filteredOutbox.some((item) => item.status === 'failed')
      ? 'failed'
      : 'buffered';
  const kpiCards = [
    { label: '工作项', value: s.workitems_total, tone: '' },
    { label: '在跑', value: s.in_progress, tone: 'blue' },
    { label: '待决策', value: s.awaiting_decision, tone: 'amber' },
    { label: '超预算', value: s.over_budget, tone: 'red' },
    { label: '已完成', value: s.approved, tone: 'green' },
    { label: '升级', value: s.escalated, tone: 'orange' },
    { label: '产物', value: s.artifact_count, tone: '' },
    { label: '死信', value: deadLetters, tone: deadLetters > 0 ? 'red' : '' },
  ];

  useEffect(() => {
    setSelectedSilenceKeys((current) => current.filter((key) => alertSilences.some((item) => item.cluster_key === key)));
  }, [alertSilences]);

  useEffect(() => {
    setFocusedClusterKeys((current) => current.filter((key) => (
      alertSilences.some((item) => item.cluster_key === key)
      || alertClusters.some((item) => item.cluster_key === key)
    )));
    setFocusedOutboxIds((current) => current.filter((id) => alertOutbox.some((item) => item.id === id)));
  }, [alertSilences, alertClusters, alertOutbox]);

  const getTaskClusterKeys = (task: OpsTaskRecord): string[] => {
    const metadata = task.metadata ?? {};
    const keys = new Set<string>();
    const clusterKey = metadata.cluster_key;
    if (typeof clusterKey === 'string' && clusterKey) keys.add(clusterKey);
    const clusterKeys = metadata.cluster_keys;
    if (Array.isArray(clusterKeys)) {
      clusterKeys.forEach((item) => {
        if (typeof item === 'string' && item) keys.add(item);
      });
    }
    if (
      typeof task.source_ref === 'string'
      && task.source_ref
      && (
        task.source_kind === 'alert.silence'
        || task.source_kind === 'alert.unsilence'
      )
    ) {
      keys.add(task.source_ref);
    }
    return [...keys];
  };

  const getTaskAlertIds = (task: OpsTaskRecord): string[] => {
    const metadata = task.metadata ?? {};
    const ids = new Set<string>();
    const alertIds = metadata.alert_ids;
    if (Array.isArray(alertIds)) {
      alertIds.forEach((item) => {
        if (typeof item === 'string' && item) ids.add(item);
      });
    }
    if (task.source_kind === 'alert.resend' && typeof task.source_ref === 'string' && task.source_ref) {
      ids.add(task.source_ref);
    }
    return [...ids];
  };

  const scrollToSection = (section: 'silence' | 'cluster' | 'outbox') => {
    const ref = section === 'silence'
      ? silenceSectionRef
      : section === 'cluster'
        ? clusterSectionRef
        : outboxSectionRef;
    window.requestAnimationFrame(() => {
      ref.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  };

  const scrollToTaskSection = () => {
    window.requestAnimationFrame(() => {
      taskSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  };

  const focusTaskLinks = (task: OpsTaskRecord, section: 'silence' | 'cluster' | 'outbox') => {
    const clusterKeys = getTaskClusterKeys(task);
    const alertIds = getTaskAlertIds(task);
    setFocusedTaskId(task.id);
    setFocusedClusterKeys(clusterKeys);
    setFocusedOutboxIds(alertIds);
    if (section === 'outbox') {
      setOutboxFilter('all');
    }
    if (section === 'silence') {
      setSilenceEventFilter('all');
    }
    scrollToSection(section);
  };

  const clearTaskFocus = () => {
    setFocusedTaskId(null);
    setFocusedClusterKeys([]);
    setFocusedOutboxIds([]);
  };

  const focusOpsTask = (task: OpsTaskRecord) => {
    setFocusedTaskId(task.id);
    setFocusedClusterKeys(getTaskClusterKeys(task));
    setFocusedOutboxIds(getTaskAlertIds(task));
    scrollToTaskSection();
  };

  const findLatestTaskByClusterKey = (clusterKey: string): OpsTaskRecord | undefined => (
    alertOpsTasks
      .filter((task) => getTaskClusterKeys(task).includes(clusterKey))
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())[0]
  );

  const findLatestTaskByAlertId = (alertId: string): OpsTaskRecord | undefined => (
    alertOpsTasks
      .filter((task) => getTaskAlertIds(task).includes(alertId))
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())[0]
  );

  const latestTaskSyncAt = alertOpsTasks
    .map((task) => task.last_synced_at)
    .filter((value): value is string => Boolean(value))
    .sort((a, b) => new Date(b).getTime() - new Date(a).getTime())[0];

  const renderLinkedTaskSummary = (task: OpsTaskRecord | undefined) => {
    if (!task) {
      return null;
    }
    const remoteStatus = task.external_status ?? task.delivery_status ?? task.status;
    return (
      <div className="linked-task-box">
        <div className="linked-task-head">
          <strong>最近 Alert Ops Task</strong>
          <span className={`outbox-status tone-${task.severity === 'critical' ? 'critical' : 'warning'}`}>
            {remoteStatus}
          </span>
        </div>
        <div className="linked-task-title">{task.title}</div>
        <div className="linked-task-meta">
          <span>{task.source_kind ?? 'alert.operation'}</span>
          <span>{new Date(task.created_at).toLocaleTimeString()}</span>
          {task.last_synced_at && <span>sync {new Date(task.last_synced_at).toLocaleTimeString()}</span>}
          {task.completed_at && <span>done {new Date(task.completed_at).toLocaleTimeString()}</span>}
          {task.external_ref?.url && (
            <a href={task.external_ref.url} target="_blank" rel="noreferrer">
              {task.external_ref.label ?? '打开任务'}
            </a>
          )}
        </div>
        {task.sync_error && <div className="outbox-error">sync failed: {task.sync_error}</div>}
        <div className="cluster-actions">
          <button onClick={() => focusOpsTask(task)}>定位任务</button>
        </div>
      </div>
    );
  };

  return (
    <section className="obs-view">
      <div className="obs-head">
        <h2>Observability 观测</h2>
        <div className="obs-actions">
          <span className="obs-meta">
            事件总数 {s.event_count} · 模板 {s.template_count}
            {runtime && ` · queue=${runtime.job_stats?.provider ?? 'memory'} · exec=${runtime.execution_mode}`}
          </span>
          <button onClick={onRefresh}>🔄 刷新</button>
        </div>
      </div>

      <div className="obs-kpis">
        {kpiCards.map((k) => (
          <div className={`kpi ${k.tone}`} key={k.label}>
            <strong>{k.value}</strong>
            <span>{k.label}</span>
          </div>
        ))}
      </div>

      <div className="obs-grid">
        <article className="card obs-card">
          <h3>预算消耗 Budget Burn</h3>
          <ObsBudgetRow label="Tokens" used={b.tokens.used} cap={b.tokens.cap} pct={b.tokens.pct} unit="tokens" />
          <ObsBudgetRow label="Cost" used={b.cost_usd.used} cap={b.cost_usd.cap} pct={b.cost_usd.pct} unit="USD" decimals={2} />
          <ObsBudgetRow label="Time" used={b.time_sec.used} cap={b.time_sec.cap} pct={b.time_sec.pct} unit="sec" />
        </article>

        <article className="card obs-card">
          <h3>状态分布 State Distribution</h3>
          <div className="state-dist">
            {Object.entries(metrics.state_distribution).filter(([, v]) => v > 0).map(([k, v]) => (
              <div className="state-row" key={k}>
                <span className={`state-tag state-${k}`}>{k}</span>
                <span className="state-bar">
                  <span style={{ width: `${(v / Math.max(1, s.workitems_total)) * 100}%` }} />
                </span>
                <span className="state-count">{v}</span>
              </div>
            ))}
          </div>
        </article>

        <article className="card obs-card">
          <h3>Agent 成本排行 Cost by Agent</h3>
          {metrics.agent_costs.length === 0 && <p className="muted">暂未产生 Agent 成本</p>}
          <table className="obs-table">
            <thead>
              <tr><th>Agent</th><th>Tokens</th><th>Cost (USD)</th><th>Workitems</th><th>Artifacts</th></tr>
            </thead>
            <tbody>
              {metrics.agent_costs.map((row) => (
                <tr key={row.executor_id}>
                  <td>🤖 {row.name}</td>
                  <td>{row.tokens_used.toLocaleString()}</td>
                  <td>${row.cost_used_usd.toFixed(3)}</td>
                  <td>{row.workitems}</td>
                  <td>{row.artifacts}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </article>

        <article className="card obs-card">
          <h3>工作流进度 Workflows</h3>
          {metrics.workflows.map((wf) => (
            <div className="wf-row" key={wf.id} onClick={onOpenWorkflow}>
              <div className="wf-row-head">
                <strong>{wf.title}</strong>
                <span className={`wf-state wf-${wf.state}`}>● {wf.state}</span>
              </div>
              <div className="wf-row-bar">
                <span style={{ width: `${wf.progress_pct}%` }} />
                <em>{wf.progress_pct}%</em>
              </div>
              <div className="wf-row-meta">
                <span>✔ {wf.completed_nodes}/{wf.total_nodes}</span>
                <span>⧖ {wf.decision_pending} 待决策</span>
                <span>⊘ {wf.blocked_nodes} 阻塞</span>
                <span>Cost {wf.budget.cost_pct}%</span>
              </div>
            </div>
          ))}
        </article>

        <article className="card obs-card obs-alerts">
          <h3>近期告警 Alerts ({alerts.length})</h3>
          {alerts.length === 0 && <p className="muted">暂无告警，系统运行良好。</p>}
          <ul className="alert-list">
            {alerts.map((a) => (
              <li key={a.seq} className={a.name.endsWith('exhausted') || a.name.includes('escalated') ? 'critical' : 'warn'}>
                <code>{a.name}</code>
                <span className="alert-wi">{a.workitem_id ?? a.workflow_id ?? '-'}</span>
                <time>{new Date(a.timestamp).toLocaleTimeString()}</time>
              </li>
            ))}
          </ul>
        </article>

        <article className="card obs-card" ref={silenceSectionRef}>
          <h3>Silence Windows</h3>
          <div className="silence-toolbar">
            <div className="seg job-seg">
              <select
                className="top-select"
                value={silenceEventFilter}
                onChange={(e) => setSilenceEventFilter(e.target.value)}
              >
                <option value="all">全部事件</option>
                {silenceEventOptions.map((eventName) => (
                  <option key={eventName} value={eventName}>{eventName}</option>
                ))}
              </select>
              <select
                className="top-select"
                value={silenceSort}
                onChange={(e) => setSilenceSort(e.target.value as 'eta_asc' | 'eta_desc' | 'suppressed_desc')}
              >
                <option value="eta_asc">最早到期</option>
                <option value="eta_desc">最晚到期</option>
                <option value="suppressed_desc">按抑制次数</option>
              </select>
            </div>
            <div className="cluster-actions">
              {selectedSilenceCount > 0 && (
                <button onClick={() => onUnsilenceAlertClusters(selectedSilenceKeys)}>
                  解除选中 ({selectedSilenceCount})
                </button>
              )}
              {filteredSilences.length > 1 && (
                <button onClick={() => onUnsilenceAlertClusters(allFilteredSilenceKeys)}>
                  解除当前筛选 ({filteredSilences.length})
                </button>
              )}
            </div>
          </div>
          {filteredSilences.length === 0 && <p className="muted">当前没有生效中的静默窗口。</p>}
          <ul className="silence-list">
            {filteredSilences.slice(0, 8).map((silence) => (
              <li
                key={silence.cluster_key}
                className={focusedClusterKeys.includes(silence.cluster_key) ? 'silence-item is-linked' : 'silence-item'}
              >
                <label className="silence-check">
                  <input
                    type="checkbox"
                    checked={selectedSilenceKeys.includes(silence.cluster_key)}
                    onChange={(e) => {
                      setSelectedSilenceKeys((current) => (
                        e.target.checked
                          ? [...current, silence.cluster_key]
                          : current.filter((key) => key !== silence.cluster_key)
                      ));
                    }}
                  />
                  <span>选择</span>
                </label>
                <div className="cluster-head">
                  <code>{silence.event_name}</code>
                  <span>到期 {renderSilenceEta(silence.silenced_until)}</span>
                </div>
                <div className="cluster-reason">{silence.reason}</div>
                <div className="cluster-meta">
                  <span>{silence.provider}</span>
                  <span>suppressed {silence.suppressed_count ?? 0}</span>
                  <span>until {new Date(silence.silenced_until).toLocaleTimeString()}</span>
                  {silence.last_emitted_at && <span>last emit {new Date(silence.last_emitted_at).toLocaleTimeString()}</span>}
                </div>
                {renderLinkedTaskSummary(findLatestTaskByClusterKey(silence.cluster_key))}
                <div className="cluster-actions">
                  <button onClick={() => onUnsilenceAlertCluster(silence.cluster_key)}>解除静默</button>
                </div>
              </li>
            ))}
          </ul>
        </article>

        <article className="card obs-card" ref={clusterSectionRef}>
          <h3>Failure Clusters</h3>
          {alertClusters.length === 0 && <p className="muted">暂无失败聚类。</p>}
          <ul className="cluster-list">
            {alertClusters.slice(0, 6).map((cluster) => (
              <li
                key={cluster.cluster_key}
                className={
                  `${focusedClusterKeys.includes(cluster.cluster_key) ? 'cluster-item is-linked' : 'cluster-item'} tone-${cluster.severity}`
                }
              >
                <div className="cluster-head">
                  <code>{cluster.event_name}</code>
                  <span>{cluster.count} alerts</span>
                </div>
                <div className="cluster-reason">{cluster.reason}</div>
                <div className="cluster-meta">
                  <span>{cluster.status}</span>
                  <span>{cluster.provider}</span>
                  <span>{new Date(cluster.last_timestamp).toLocaleTimeString()}</span>
                  <span>suppressed {cluster.suppressed_count ?? 0}</span>
                  {cluster.silenced_until && <span>静默中 {renderSilenceEta(cluster.silenced_until)}</span>}
                </div>
                {renderLinkedTaskSummary(findLatestTaskByClusterKey(cluster.cluster_key))}
                <div className="cluster-actions">
                  {(cluster.status === 'failed' || cluster.status === 'buffered') && (
                    <button onClick={() => onResendAlertCluster(cluster.cluster_key)}>重发这一组</button>
                  )}
                  {cluster.silenced_until
                    ? <button onClick={() => onUnsilenceAlertCluster(cluster.cluster_key)}>解除静默</button>
                    : <button onClick={() => onSilenceAlertCluster(cluster.cluster_key)}>静默 10m</button>}
                </div>
              </li>
            ))}
          </ul>
        </article>

        <article className="card obs-card" ref={outboxSectionRef}>
          <h3>Alert Outbox</h3>
          <div className="job-toolbar">
            <div className="seg job-seg">
              {(['all', 'failed', 'buffered', 'delivered'] as const).map((status) => (
                <button
                  key={status}
                  className={outboxFilter === status ? 'seg-tab active' : 'seg-tab'}
                  onClick={() => setOutboxFilter(status)}
                >
                  {renderOutboxFilterLabel(status, alertOutbox)}
                </button>
              ))}
            </div>
            {actionableOutboxCount > 0 && (
              <button onClick={() => onResendAlerts(batchResendStatus)}>
                批量重发 {batchResendStatus === 'failed' ? '失败项' : '缓冲项'}
              </button>
            )}
          </div>
          {filteredOutbox.length === 0 && <p className="muted">暂无告警出站记录。</p>}
          <ul className="outbox-list">
            {filteredOutbox.slice(0, 8).map((item) => (
              <li
                key={item.id}
                className={`${focusedOutboxIds.includes(item.id) ? 'outbox-item is-linked' : 'outbox-item'} status-${item.status}`}
              >
                <div className="outbox-main">
                  <div className="outbox-head">
                    <code>{item.event_name}</code>
                    <span className={`outbox-status tone-${item.severity}`}>{item.status}</span>
                  </div>
                  <div className="outbox-summary">{item.summary}</div>
                  <div className="outbox-meta">
                    <span>{item.provider}</span>
                    <span>attempts {item.attempts}</span>
                    <span>{new Date(item.timestamp).toLocaleTimeString()}</span>
                    <span>{item.workitem_id ?? item.workflow_id ?? '-'}</span>
                  </div>
                  {renderLinkedTaskSummary(findLatestTaskByAlertId(item.id))}
                  {item.error && <div className="outbox-error">{item.error}</div>}
                </div>
                {(item.status === 'failed' || item.status === 'buffered') && (
                  <button onClick={() => onResendAlert(item.id)}>重发</button>
                )}
              </li>
            ))}
          </ul>
        </article>

        <article className="card obs-card" ref={taskSectionRef}>
          <div className="ops-task-toolbar">
            <h3>Alert Ops Tasks</h3>
            <div className="ops-task-toolbar-actions">
              <span className={isStaleSync(latestTaskSyncAt) ? 'freshness-badge tone-warning' : 'freshness-badge'}>
                已同步 {renderSyncAge(latestTaskSyncAt)}
              </span>
              {isStaleSync(latestTaskSyncAt) && (
                <span className="stale-indicator" aria-label="stale status">
                  <span className="stale-dot" />
                  <span>stale</span>
                </span>
              )}
              <button
                className={isStaleSync(latestTaskSyncAt) ? 'warning-button' : undefined}
                onClick={() => void onRefreshAlertOpsTasks()}
                disabled={refreshingAlertOpsTasks}
              >
                {refreshingAlertOpsTasks ? 'Refreshing...' : 'Refresh Status'}
              </button>
            </div>
          </div>
          {alertOpsTasks.length === 0 && <p className="muted">当前还没有映射到飞书的告警运营任务。</p>}
          <ul className="outbox-list">
            {alertOpsTasks.slice(0, 8).map((task) => (
              <li
                key={task.id}
                className={focusedTaskId === task.id ? 'outbox-item status-buffered is-linked' : 'outbox-item status-buffered'}
              >
                <div className="outbox-main">
                  <div className="outbox-head">
                    <code>{task.source_kind ?? 'alert.operation'}</code>
                    <span className={`outbox-status tone-${task.severity === 'critical' ? 'critical' : 'warning'}`}>
                      {task.external_status ?? task.delivery_status ?? task.status}
                    </span>
                  </div>
                  <div className="outbox-summary">{task.title}</div>
                  <div className="outbox-meta">
                    <span>{task.provider}</span>
                    <span>{task.workitem_id}</span>
                    <span>{new Date(task.created_at).toLocaleTimeString()}</span>
                    {task.last_synced_at && <span>sync {new Date(task.last_synced_at).toLocaleTimeString()}</span>}
                    {task.completed_at && <span>done {new Date(task.completed_at).toLocaleTimeString()}</span>}
                    {task.source_ref && <span>{task.source_ref}</span>}
                    {getTaskClusterKeys(task).length > 0 && <span>cluster {getTaskClusterKeys(task).length}</span>}
                    {getTaskAlertIds(task).length > 0 && <span>alert {getTaskAlertIds(task).length}</span>}
                  </div>
                  <div className="cluster-reason">{task.summary}</div>
                  {task.sync_error && <div className="outbox-error">sync failed: {task.sync_error}</div>}
                  {task.delivery_error && <div className="outbox-error">{task.delivery_error}</div>}
                  <div className="cluster-actions">
                    {getTaskClusterKeys(task).some((key) => alertSilences.some((item) => item.cluster_key === key)) && (
                      <button onClick={() => focusTaskLinks(task, 'silence')}>定位 Silence</button>
                    )}
                    {getTaskClusterKeys(task).some((key) => alertClusters.some((item) => item.cluster_key === key)) && (
                      <button onClick={() => focusTaskLinks(task, 'cluster')}>定位 Cluster</button>
                    )}
                    {getTaskAlertIds(task).some((id) => alertOutbox.some((item) => item.id === id)) && (
                      <button onClick={() => focusTaskLinks(task, 'outbox')}>定位 Outbox</button>
                    )}
                    {focusedTaskId === task.id && (
                      <button onClick={clearTaskFocus}>清除联动</button>
                    )}
                  </div>
                </div>
                {task.external_ref?.url && (
                  <a href={task.external_ref.url} target="_blank" rel="noreferrer">
                    {task.external_ref.label ?? '打开任务'}
                  </a>
                )}
              </li>
            ))}
          </ul>
        </article>

        <article className="card obs-card">
          <h3>事件类型计数 Event Kinds</h3>
          <div className="kind-chips">
            {Object.entries(metrics.event_kind_count).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
              <span className="kind-chip" key={k}>{k}<em>{v}</em></span>
            ))}
          </div>
        </article>

        <article className="card obs-card">
          <h3>Runtime Health</h3>
          <div className="risk-list">
            <div className="risk-item">
              <span className={`risk-dot ${health?.queue.ok ? 'tone-green' : 'tone-red'}`} />
              <span className="risk-text">
                <strong>Queue</strong> · {health?.queue.provider ?? runtime?.job_stats?.provider ?? 'memory'}
                <small className="muted"> worker={health?.queue.worker_alive ? 'alive' : 'down'} {health?.queue.error ?? ''}</small>
              </span>
            </div>
            <div className="risk-item">
              <span className={`risk-dot ${health?.state_store.ok ? 'tone-green' : 'tone-red'}`} />
              <span className="risk-text">
                <strong>State Store</strong> · {health?.state_store.provider ?? runtime?.state_store_mode ?? 'json_snapshot'}
                <small className="muted"> {health?.state_store.error ?? 'healthy'}</small>
              </span>
            </div>
            <div className="risk-item">
              <span className={`risk-dot ${health?.delivery.artifact_delivery.ok ? 'tone-green' : 'tone-red'}`} />
              <span className="risk-text">
                <strong>Delivery</strong> · {health?.delivery.artifact_delivery.provider ?? runtime?.delivery_provider ?? 'local'}
                <small className="muted"> {health?.delivery.artifact_delivery.error ?? health?.delivery.artifact_delivery.mode ?? 'healthy'}</small>
              </span>
            </div>
            <div className="risk-item">
              <span className={`risk-dot ${health?.delivery.notification.ok ? 'tone-green' : 'tone-red'}`} />
              <span className="risk-text">
                <strong>Notify</strong> · {health?.delivery.notification.provider ?? runtime?.notification_provider ?? 'local'}
                <small className="muted"> {health?.delivery.notification.error ?? health?.delivery.notification.mode ?? 'healthy'}</small>
              </span>
            </div>
            <div className="risk-item">
              <span className={`risk-dot ${runtime?.alerting_health?.ok ? 'tone-green' : 'tone-red'}`} />
              <span className="risk-text">
                <strong>Alert Router</strong> · {runtime?.alerting_health?.provider ?? 'local'}
                <small className="muted"> {runtime?.alerting_health?.error ?? runtime?.alerting_health?.mode ?? 'buffered'}</small>
              </span>
            </div>
            <div className="risk-item">
              <span className={`risk-dot ${runtime?.tasking_health?.ok ? 'tone-green' : 'tone-red'}`} />
              <span className="risk-text">
                <strong>Ops Tasks</strong> · {runtime?.tasking_health?.provider ?? runtime?.ops_task_provider ?? 'local'}
                <small className="muted">
                  {runtime?.tasking_health?.error ?? runtime?.tasking_health?.mode ?? 'buffered'}
                  {runtime?.alerting_stats ? ` · dedup ${runtime.alerting_stats.dedup_window_sec}s · suppressed ${runtime.alerting_stats.suppressed_total}` : ''}
                </small>
              </span>
            </div>
          </div>
        </article>

        <article className="card obs-card">
          <h3>Job Center</h3>
          <div className="job-toolbar">
            <div className="seg job-seg">
              <button className={jobFilter === 'all' ? 'seg-tab active' : 'seg-tab'} onClick={() => setJobFilter('all')}>
                全部 ({jobs.length})
              </button>
              <button
                className={jobFilter === 'dead_lettered' ? 'seg-tab active' : 'seg-tab'}
                onClick={() => setJobFilter('dead_lettered')}
              >
                死信 ({deadLetters})
              </button>
            </div>
            <span className="muted">点击 Job 行可查看 payload 并重放</span>
          </div>
          {jobs.length === 0 && <p className="muted">暂无后台作业。</p>}
          <table className="obs-table">
            <thead>
              <tr><th>Job</th><th>状态</th><th>尝试</th><th>失败原因</th><th>重试</th><th>Workitem</th><th>操作</th></tr>
            </thead>
            <tbody>
              {filteredJobs.slice(0, 12).map((job) => (
                <tr key={job.id} className="job-row-clickable" onClick={() => setSelectedJob(job)}>
                  <td>
                    <code>{job.id}</code>
                    <div className="muted">{new Date(job.created_at).toLocaleTimeString()}</div>
                    {job.source_job_id && <div className="muted">from {job.source_job_id}</div>}
                  </td>
                  <td>{renderJobStatus(job.status)}</td>
                  <td>{job.attempts}/{job.max_attempts}</td>
                  <td>{job.last_failure_kind === 'timeout' ? `timeout ${job.timeout_sec ?? '-' }s` : job.last_failure_kind ?? '-'}</td>
                  <td>{job.next_retry_at ? `at ${new Date(job.next_retry_at).toLocaleTimeString()}` : `${job.retry_backoff_sec ?? 0}s`}</td>
                  <td>{job.workitem_id}</td>
                  <td>
                    {(job.status === 'failed' || job.status === 'cancelled' || job.status === 'dead_lettered') && (
                      <button onClick={(e) => { e.stopPropagation(); onRetryJob(job.id); }}>重试</button>
                    )}
                    {(job.status === 'queued' || job.status === 'running') && (
                      <button onClick={(e) => { e.stopPropagation(); onCancelJob(job.id); }}>取消</button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {selectedJob && (
            <JobReplayDrawer
              job={selectedJob}
              currentUser={currentUser}
              onClose={() => setSelectedJob(null)}
              onReplay={async (payload) => {
                await onReplayJob(selectedJob.id, payload);
                setSelectedJob(null);
              }}
              onTakeover={async () => {
                await onHumanTakeover(selectedJob);
                setSelectedJob(null);
              }}
              onCreateTask={async () => onEscalateTask(selectedJob)}
            />
          )}
        </article>
      </div>
    </section>
  );
}

function renderJobStatus(status: JobRecord['status']) {
  if (status === 'dead_lettered') return 'dead-lettered';
  if (status === 'cancel_requested') return 'cancel-requested';
  return status;
}

function renderOutboxFilterLabel(
  status: 'all' | 'failed' | 'buffered' | 'delivered',
  items: AlertOutboxRecord[],
) {
  const count = status === 'all' ? items.length : items.filter((item) => item.status === status).length;
  const labelMap = {
    all: '全部',
    failed: '失败',
    buffered: '缓冲',
    delivered: '已投递',
  } as const;
  return `${labelMap[status]} (${count})`;
}

function renderSilenceEta(silencedUntil: string) {
  const remainingMs = new Date(silencedUntil).getTime() - Date.now();
  if (remainingMs <= 0) {
    return '即将结束';
  }
  const remainingMin = Math.ceil(remainingMs / 60000);
  return `${remainingMin}m`;
}

function JobReplayDrawer({
  job,
  currentUser,
  onClose,
  onReplay,
  onTakeover,
  onCreateTask,
}: {
  job: JobRecord;
  currentUser: CurrentUser | null;
  onClose: () => void;
  onReplay: (payload: Record<string, unknown>) => Promise<void>;
  onTakeover: () => Promise<void>;
  onCreateTask: () => Promise<OpsTaskRecord>;
}) {
  const [draft, setDraft] = useState(JSON.stringify(job.payload, null, 2));
  const [parseError, setParseError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [creatingTask, setCreatingTask] = useState(false);
  const [lastTask, setLastTask] = useState<OpsTaskRecord | null>(null);
  const [existingTasks, setExistingTasks] = useState<OpsTaskRecord[]>([]);
  const recommendation = recommendRepairTemplate(job);

  function applyRepairTemplate(kind: 'more_time' | 'safer_retry' | 'fast_fail') {
    try {
      const current = JSON.parse(draft) as Record<string, unknown>;
      if (kind === 'more_time') {
        current.__job_timeout_sec = Math.max(Number(current.__job_timeout_sec ?? job.timeout_sec ?? 8), 20);
        current.__job_max_attempts = Math.max(Number(current.__job_max_attempts ?? job.max_attempts ?? 2), 2);
      }
      if (kind === 'safer_retry') {
        current.__job_timeout_sec = Math.max(Number(current.__job_timeout_sec ?? job.timeout_sec ?? 8), 12);
        current.__job_max_attempts = Math.max(Number(current.__job_max_attempts ?? job.max_attempts ?? 2), 4);
        current.__job_retry_backoff_sec = Math.max(Number(current.__job_retry_backoff_sec ?? job.retry_backoff_sec ?? 0.25), 1);
      }
      if (kind === 'fast_fail') {
        current.__job_timeout_sec = Math.min(Number(current.__job_timeout_sec ?? job.timeout_sec ?? 8), 6);
        current.__job_max_attempts = 1;
        current.__job_retry_backoff_sec = 0;
      }
      setDraft(JSON.stringify(current, null, 2));
      setParseError(null);
    } catch (err) {
      setParseError(err instanceof Error ? err.message : '模板应用失败');
    }
  }

  useEffect(() => {
    setDraft(JSON.stringify(job.payload, null, 2));
    setParseError(null);
    fetchOpsTasks(job.workitem_id).then(setExistingTasks).catch(() => setExistingTasks([]));
  }, [job.id, job.payload]);

  return (
    <aside className="job-drawer">
      <header>
        <strong>Dead-letter Remediation</strong>
        <button className="dd-close" onClick={onClose}>×</button>
      </header>
      <div className="job-drawer-meta">
        <span><code>{job.id}</code></span>
        <span>{renderJobStatus(job.status)}</span>
        <span>{job.last_failure_kind ?? 'unknown'}</span>
      </div>
      <div className="job-drawer-grid">
        <div>
          <h4>执行上下文</h4>
          <ul className="drawer-facts">
            <li>workitem: {job.workitem_id}</li>
            <li>attempts: {job.attempts}/{job.max_attempts}</li>
            <li>timeout: {job.timeout_sec ?? 0}s</li>
            <li>backoff: {job.retry_backoff_sec ?? 0}s</li>
            <li>source: {job.source_job_id ?? '-'}</li>
          </ul>
        </div>
        <div>
          <h4>最近错误</h4>
          <pre className="job-error-box">{job.error ?? 'No error message'}</pre>
        </div>
      </div>
      <div className="repair-recommendation">
        <strong>推荐动作</strong>
        <span>{recommendation.reason}</span>
        <span>当前接管人建议：{currentUser?.name ?? currentUser?.id ?? 'operator'}</span>
      </div>
      <div className="job-escalation-actions">
        <button onClick={() => { void onTakeover(); }}>人工接管</button>
        <button
          className="primary"
          disabled={creatingTask}
          onClick={async () => {
            setCreatingTask(true);
            try {
              const task = await onCreateTask();
              setLastTask(task);
              const next = await fetchOpsTasks(job.workitem_id);
              setExistingTasks(next);
            } finally {
              setCreatingTask(false);
            }
          }}
        >
          {creatingTask ? '升级中…' : '升级并建任务'}
        </button>
      </div>
      {(lastTask || existingTasks.length > 0) && (
        <div className="job-task-box">
          <h4>Ops Tasks</h4>
          <ul className="drawer-facts">
            {(lastTask ? [lastTask, ...existingTasks.filter((task) => task.id !== lastTask.id)] : existingTasks)
              .slice(0, 3)
              .map((task) => (
                <li key={task.id}>
                  {task.title} · {task.status} · {task.severity}
                  {task.delivery_status ? ` · ${task.delivery_status}` : ''}
                  {task.external_ref?.url && (
                    <>
                      {' · '}
                      <a href={task.external_ref.url} target="_blank" rel="noreferrer">
                        {task.external_ref.label ?? '打开任务'}
                      </a>
                    </>
                  )}
                  {task.delivery_error ? ` · ${task.delivery_error}` : ''}
                </li>
              ))}
          </ul>
        </div>
      )}
      <h4>修复模板</h4>
      <div className="repair-templates">
        <button
          className={recommendation.kind === 'more_time' ? 'recommended' : ''}
          onClick={() => applyRepairTemplate('more_time')}
        >
          放宽超时
        </button>
        <button
          className={recommendation.kind === 'safer_retry' ? 'recommended' : ''}
          onClick={() => applyRepairTemplate('safer_retry')}
        >
          保守重试
        </button>
        <button
          className={recommendation.kind === 'fast_fail' ? 'recommended' : ''}
          onClick={() => applyRepairTemplate('fast_fail')}
        >
          快速失败
        </button>
      </div>
      <h4>Replay Payload</h4>
      <textarea
        className="job-payload-editor"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
      />
      {parseError && <div className="job-parse-error">{parseError}</div>}
      <footer className="job-drawer-actions">
        <button onClick={onClose}>关闭</button>
        <button
          className="primary"
          disabled={submitting}
          onClick={async () => {
            try {
              setSubmitting(true);
              const payload = JSON.parse(draft) as Record<string, unknown>;
              setParseError(null);
              await onReplay(payload);
            } catch (err) {
              setParseError(err instanceof Error ? err.message : 'JSON 解析失败');
            } finally {
              setSubmitting(false);
            }
          }}
        >
          {submitting ? '重放中…' : '重放为新 Job'}
        </button>
      </footer>
    </aside>
  );
}

function recommendRepairTemplate(job: JobRecord): {
  kind: 'more_time' | 'safer_retry' | 'fast_fail';
  reason: string;
} {
  if (job.last_failure_kind === 'timeout') {
    return {
      kind: 'more_time',
      reason: '检测到 timeout，优先放宽超时并保留最小重试次数，避免任务还没跑完就被杀掉。',
    };
  }
  if ((job.attempts ?? 0) >= Math.max(3, job.max_attempts ?? 0)) {
    return {
      kind: 'fast_fail',
      reason: '连续失败次数已经偏高，建议先快速失败缩小爆炸半径，再切人工定位根因。',
    };
  }
  return {
    kind: 'safer_retry',
    reason: '当前更像瞬时错误或外部依赖抖动，优先增加 backoff 和重试次数更稳妥。',
  };
}

function ObsBudgetRow({
  label,
  used,
  cap,
  pct,
  unit,
  decimals = 0,
}: {
  label: string;
  used: number;
  cap: number;
  pct: number;
  unit: string;
  decimals?: number;
}) {
  const tone = pct >= 100 ? 'danger' : pct >= 80 ? 'warn' : 'ok';
  return (
    <div className="budget-row">
      <span className="bb-label">{label}</span>
      <span className={`bb-bar tone-${tone}`}>
        <span style={{ width: `${Math.min(100, pct)}%` }} />
      </span>
      <span className="bb-text">
        {used.toFixed(decimals)} / {cap.toFixed(decimals)} {unit} · {pct}%
      </span>
    </div>
  );
}

/* ---------- ART-VIEW: Artifact preview drawer ---------- */
function ArtifactPreview({
  artifact,
  previewData,
  allVersions,
  onClose,
  onPick,
}: {
  artifact: Artifact;
  previewData: ArtifactPreviewData | null;
  allVersions: Artifact[];
  onClose: () => void;
  onPick: (a: Artifact) => void;
}) {
  const sortedVersions = [...allVersions].sort((a, b) => b.version - a.version);
  return (
    <aside className="artifact-preview">
      <header>
        <span className="ap-type">{artifact.type.toUpperCase()}</span>
        <strong>{artifact.title}</strong>
        <button className="dd-close" onClick={onClose}>×</button>
      </header>
      <div className="ap-meta">
        <span>v{artifact.version}</span>
        <span>置信度 {Math.round(artifact.confidence * 100)}%</span>
        <span>{new Date(artifact.created_at).toLocaleString()}</span>
        <a href={resolveArtifactUrl(artifact.uri)} target="_blank" rel="noreferrer">🔗 打开源</a>
      </div>

      {sortedVersions.length > 1 && (
        <div className="ap-versions">
          <span>历史版本:</span>
          {sortedVersions.map((v) => (
            <button
              key={v.id}
              className={v.id === artifact.id ? 'ap-v active' : 'ap-v'}
              onClick={() => onPick(v)}
            >
              v{v.version}
            </button>
          ))}
        </div>
      )}

      <div className="ap-body">
        <pre>{previewData?.content ?? '加载中...'}</pre>
      </div>

      <footer className="ap-foot">
        <span className="muted">content-type: {previewData?.content_type ?? 'loading'}</span>
      </footer>
    </aside>
  );
}

/* ---------- Inline icons ---------- */
function Bell() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M6 8a6 6 0 0112 0c0 7 3 9 3 9H3s3-2 3-9z" />
      <path d="M10 21a2 2 0 004 0" />
    </svg>
  );
}
function Stack() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M12 3l9 5-9 5-9-5 9-5z" />
      <path d="M3 13l9 5 9-5" />
    </svg>
  );
}
function Gear() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1-1.5 1.7 1.7 0 00-1.8.3l-.1.1A2 2 0 114.4 17l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1 1.7 1.7 0 00-.3-1.8l-.1-.1A2 2 0 117 4.4l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1A2 2 0 1119.6 7l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z" />
    </svg>
  );
}
