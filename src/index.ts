// ECHO GUARDIAN — Autonomous AI DevOps Agent
// Deployed as both echo-guardian-alpha and echo-guardian-beta
//
// CAPABILITIES:
// 1. Monitor ALL 273+ workers 24/7 with /health pings
// 2. Mutual Watchdog — if partner guardian goes down, resurrect it via CF API
// 3. Enhance/Optimize/Harden/Upgrade existing workers via AI code analysis
// 4. Add new features to existing workers
// 5. Create entirely new workers when gaps are identified
//
// CRONS (alpha):  every-5m health, every-30m enhance, every-6h deep audit, 8am daily report
// CRONS (beta):   offset-5m health, offset-30m enhance, offset-6h deep audit, 9am daily report
// Offset schedules prevent duplicate work.

interface Env {
  DB: D1Database;
  CACHE: KVNamespace;
  GUARDIAN_ID: string;
  PARTNER_URL: string;
  PARTNER_NAME: string;
  CLOUDFLARE_API_TOKEN: string;
  CLOUDFLARE_ACCOUNT_ID: string;
  GITHUB_TOKEN: string;
  ECHO_API_KEY: string;
  SVC_SHARED_BRAIN: Fetcher;
  SVC_ENGINE_RUNTIME: Fetcher;
  SVC_BUILDER: Fetcher;
  SVC_DAEMON: Fetcher;
  SVC_PARTNER: Fetcher;
  GITHUB_ORG: string;
}

// ═══════════════════════════════════════════════════════════════
// STRUCTURED LOGGING
// ═══════════════════════════════════════════════════════════════
function slog(level: string, msg: string, data: Record<string, unknown> = {}) {
  const entry = { ts: new Date().toISOString(), level, msg, guardian: 'echo-guardian', ...data };
  console.log(JSON.stringify(entry));
}

// ═══════════════════════════════════════════════════════════════
// WORKER REGISTRY — ALL KNOWN WORKERS
// Split into tiers for prioritized monitoring
// ═══════════════════════════════════════════════════════════════
const TIER1_CRITICAL = [
  'echo-engine-runtime', 'echo-shared-brain', 'echo-doctrine-forge', 'echo-knowledge-forge',
  'echo-chat', 'echo-autonomous-daemon', 'echo-autonomous-builder', 'echo-diagnostics-agent',
  'echo-speak-cloud', 'echo-sdk-gateway', 'echo-arcanum', 'echo-vault-api',
  'echo-build-orchestrator', 'omniscient-sync', 'echo-ai-orchestrator', 'echo-analytics-engine',
  'echo-report-generator', 'echo-swarm-brain', 'echo-email-sender',
];

const TIER2_SAAS = [
  'echo-crm', 'echo-helpdesk', 'echo-booking', 'echo-invoice', 'echo-forms', 'echo-hr',
  'echo-contracts', 'echo-lms', 'echo-email-marketing', 'echo-surveys', 'echo-knowledge-base',
  'echo-workflow-automation', 'echo-social-media', 'echo-document-manager', 'echo-live-chat',
  'echo-link-shortener', 'echo-feedback-board', 'echo-newsletter', 'echo-web-analytics',
  'echo-waitlist', 'echo-reviews', 'echo-signatures', 'echo-affiliate', 'echo-proposals',
  'echo-gamer-companion', 'echo-qr-menu', 'echo-podcast', 'echo-calendar', 'echo-payroll',
  'echo-recruiting', 'echo-compliance', 'echo-timesheet', 'echo-expense', 'echo-okr',
  'echo-finance-ai', 'echo-home-ai', 'echo-shepherd-ai', 'echo-call-center', 'echo-intel-hub',
  'echo-inventory', 'echo-project-manager', 'echo-documents', 'echo-workflows', 'echo-finance',
  'echo-invoicing', 'echo-appointments', 'echo-hr-management', 'echo-project-management',
];

const TIER3_BOTS = [
  'echo-x-bot', 'echo-telegram', 'echo-linkedin', 'echo-reddit-bot', 'echo-instagram',
  'echo-slack', 'echo-whatsapp', 'echo-messenger', 'echo-messaging-gateway', 'echo-bot-auditor',
];

const TIER4_INFRA = [
  'echo-config-manager', 'echo-alert-router', 'echo-log-aggregator', 'echo-rate-limiter',
  'echo-cron-orchestrator', 'echo-api-gateway', 'echo-notification-hub', 'echo-distributed-tracing',
  'echo-service-registry', 'echo-health-dashboard', 'echo-circuit-breaker',
  'echo-deployment-coordinator', 'echo-incident-manager', 'echo-cost-optimizer',
  'echo-status-page', 'echo-feature-flags', 'echo-mcp-server', 'echo-mcp-preload',
];

const TIER5_SECURITY = [
  'echo-prometheus-ai', 'echo-prometheus-cloud', 'echo-prometheus-surveillance',
  'echo-gs343', 'echo-343-scanner', 'echo-phoenix-cloud',
];

const TIER6_SERVICES = [
  'echo-paypal', 'echo-crypto-trader', 'echo-price-alerts', 'echo-tax-return',
  'echo-coin-rewards', 'echo-model-host', 'echo-drive-intelligence', 'echo-county-records',
  'echo-speak-cloud', 'echo-beta-portal', 'ept-api', 'echo-memory-prime',
  'echo-graph-rag', 'echo-immortality-vault', 'echo-a2a-protocol', 'echo-agent-coordinator',
  'echo-agentic-engine', 'echo-customer-success', 'echo-data-room', 'echo-asset-manager',
  'echo-document-delivery', 'echo-ab-testing-engine', 'echo-vendor-manager',
];

const TIER7_CLIENT = [
  'billymc-api', 'billymc-voice', 'profinish-api', 'bgat-chat-api', 'bree-chat',
  'cleanbrees-api', 'cleanbrees-convoai', 'cleanbrees-messenger', 'bgat-api-gateway',
  'barking-lot-facebook', 'rah-api',
];

const TIER8_DATA = [
  'shadowglass-v8-warpspeed', 'echo-landman-pipeline', 'echo-darkweb-intelligence',
  'echo-knowledge-harvester', 'echo-knowledge-scout', 'echo-news-scraper',
  'data-acquisition-pipeline', 'echo-domain-harvester', 'echo-sec-scraper',
];

const ALL_TIERS = [
  { name: 'CRITICAL', workers: TIER1_CRITICAL, priority: 1 },
  { name: 'SAAS', workers: TIER2_SAAS, priority: 2 },
  { name: 'BOTS', workers: TIER3_BOTS, priority: 3 },
  { name: 'INFRA', workers: TIER4_INFRA, priority: 4 },
  { name: 'SECURITY', workers: TIER5_SECURITY, priority: 5 },
  { name: 'SERVICES', workers: TIER6_SERVICES, priority: 6 },
  { name: 'CLIENT', workers: TIER7_CLIENT, priority: 7 },
  { name: 'DATA', workers: TIER8_DATA, priority: 8 },
];

function getAllWorkers(): string[] {
  return ALL_TIERS.flatMap(t => t.workers);
}

function workerUrl(name: string): string {
  return `https://${name}.bmcii1976.workers.dev`;
}

// ═══════════════════════════════════════════════════════════════
// D1 SCHEMA INITIALIZATION
// ═══════════════════════════════════════════════════════════════
const SCHEMA = `
CREATE TABLE IF NOT EXISTS health_checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  worker_name TEXT NOT NULL,
  status TEXT NOT NULL,
  status_code INTEGER,
  latency_ms INTEGER,
  version TEXT,
  error TEXT,
  checked_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_hc_worker ON health_checks(worker_name, checked_at);
CREATE INDEX IF NOT EXISTS idx_hc_status ON health_checks(status, checked_at);

CREATE TABLE IF NOT EXISTS incidents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  worker_name TEXT NOT NULL,
  type TEXT NOT NULL,
  severity TEXT NOT NULL DEFAULT 'warning',
  description TEXT,
  auto_resolved INTEGER DEFAULT 0,
  resolved_at TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_inc_worker ON incidents(worker_name, created_at);
CREATE INDEX IF NOT EXISTS idx_inc_open ON incidents(resolved_at, severity);

CREATE TABLE IF NOT EXISTS enhancements (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  worker_name TEXT NOT NULL,
  type TEXT NOT NULL,
  description TEXT,
  files_changed TEXT,
  code_before TEXT,
  code_after TEXT,
  ai_reasoning TEXT,
  github_url TEXT,
  deployed INTEGER DEFAULT 0,
  reverted INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_enh_worker ON enhancements(worker_name, created_at);

CREATE TABLE IF NOT EXISTS creations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  worker_name TEXT NOT NULL,
  reason TEXT,
  description TEXT,
  lines_of_code INTEGER,
  github_repo TEXT,
  deployed INTEGER DEFAULT 0,
  created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS guardian_state (
  key TEXT PRIMARY KEY,
  value TEXT,
  updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS enhancement_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  worker_name TEXT NOT NULL,
  priority INTEGER DEFAULT 5,
  type TEXT NOT NULL,
  analysis TEXT,
  status TEXT DEFAULT 'pending',
  claimed_by TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_eq_status ON enhancement_queue(status, priority);

CREATE TABLE IF NOT EXISTS partner_health (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  partner_name TEXT NOT NULL,
  status TEXT NOT NULL,
  latency_ms INTEGER,
  consecutive_failures INTEGER DEFAULT 0,
  last_success TEXT,
  resurrection_attempted INTEGER DEFAULT 0,
  checked_at TEXT DEFAULT (datetime('now'))
);
`;

async function ensureSchema(db: D1Database) {
  const statements = SCHEMA.split(';').filter(s => s.trim());
  for (const stmt of statements) {
    try { await db.prepare(stmt).run(); } catch { /* table exists */ }
  }
}

// ═══════════════════════════════════════════════════════════════
// HEALTH CHECK ENGINE
// Uses Cloudflare API (Workers on same account can't fetch each
// other via public URLs — returns 404 from CF routing layer).
// Service bindings used for live /health checks on critical workers.
// ═══════════════════════════════════════════════════════════════
interface HealthResult {
  worker: string;
  status: 'healthy' | 'degraded' | 'down' | 'timeout';
  statusCode: number;
  latencyMs: number;
  version: string;
  error: string;
}

// Map service binding env keys to worker names for live /health checks
const SERVICE_BINDING_MAP: Record<string, string> = {
  'echo-shared-brain': 'SVC_SHARED_BRAIN',
  'echo-engine-runtime': 'SVC_ENGINE_RUNTIME',
  'echo-autonomous-builder': 'SVC_BUILDER',
  'echo-autonomous-daemon': 'SVC_DAEMON',
};

async function checkWorkerViaBinding(env: Env, name: string, bindingKey: string): Promise<HealthResult> {
  const start = Date.now();
  try {
    const binding = (env as unknown as Record<string, Fetcher>)[bindingKey];
    if (!binding) return { worker: name, status: 'degraded', statusCode: 0, latencyMs: 0, version: '', error: 'No binding' };

    const res = await binding.fetch(new Request('https://worker/health'));
    const latency = Date.now() - start;
    let version = '';
    try {
      const body = await res.json() as Record<string, unknown>;
      version = (body.version as string) || '';
    } catch { /* non-json */ }

    if (res.status === 200) {
      return { worker: name, status: latency > 5000 ? 'degraded' : 'healthy', statusCode: 200, latencyMs: latency, version, error: '' };
    }
    return { worker: name, status: 'degraded', statusCode: res.status, latencyMs: latency, version, error: `HTTP ${res.status}` };
  } catch (e: unknown) {
    return { worker: name, status: 'down', statusCode: 0, latencyMs: Date.now() - start, version: '', error: e instanceof Error ? e.message : String(e) };
  }
}

interface CfScript {
  id: string;
  modified_on: string;
  created_on: string;
  has_assets?: boolean;
}

async function fetchDeployedScripts(env: Env): Promise<Map<string, CfScript>> {
  const scripts = new Map<string, CfScript>();
  try {
    const res = await fetch(
      `https://api.cloudflare.com/client/v4/accounts/${env.CLOUDFLARE_ACCOUNT_ID}/workers/scripts`,
      { headers: { 'Authorization': `Bearer ${env.CLOUDFLARE_API_TOKEN}` } }
    );
    if (!res.ok) {
      slog('error', 'CF API failed to list scripts', { status: res.status });
      return scripts;
    }
    const data = await res.json() as { result?: CfScript[] };
    for (const s of data.result || []) {
      scripts.set(s.id, s);
    }
  } catch (e: unknown) {
    slog('error', 'CF API error', { error: e instanceof Error ? e.message : String(e) });
  }
  return scripts;
}

async function healthSweep(env: Env): Promise<{ results: HealthResult[]; down: HealthResult[]; degraded: HealthResult[] }> {
  const allWorkers = getAllWorkers();
  const results: HealthResult[] = [];

  // Step 1: Bulk-fetch all deployed scripts via CF API (1 call)
  const deployedScripts = await fetchDeployedScripts(env);
  const apiCheckTime = Date.now();

  // Step 2: Live /health checks on service-bound workers (parallel)
  const bindingChecks = Object.entries(SERVICE_BINDING_MAP).map(([workerName, bindingKey]) =>
    checkWorkerViaBinding(env, workerName, bindingKey)
  );
  // Also check partner
  bindingChecks.push(checkWorkerViaBinding(env, env.PARTNER_NAME, 'SVC_PARTNER'));
  const liveResults = await Promise.allSettled(bindingChecks);
  const liveMap = new Map<string, HealthResult>();
  for (const r of liveResults) {
    if (r.status === 'fulfilled') liveMap.set(r.value.worker, r.value);
  }

  // Step 3: For each registered worker, determine status
  for (const name of allWorkers) {
    // If we have a live /health check result (via service binding), use that
    const liveResult = liveMap.get(name);
    if (liveResult) {
      results.push(liveResult);
      continue;
    }

    // Otherwise, check if it's deployed via CF API
    const script = deployedScripts.get(name);
    if (script) {
      // Worker is deployed — check freshness
      const modifiedMs = new Date(script.modified_on).getTime();
      const ageHours = (apiCheckTime - modifiedMs) / (1000 * 60 * 60);
      // If last deployed within 30 days, consider healthy
      // If stale (>30 days no deploy), mark degraded with note
      const status = ageHours < 720 ? 'healthy' : 'degraded';
      const error = ageHours >= 720 ? `Stale: last deployed ${Math.round(ageHours / 24)}d ago` : '';
      results.push({
        worker: name, status, statusCode: 200, latencyMs: 0,
        version: script.modified_on.split('T')[0], error,
      });
    } else {
      // Worker NOT found in CF account — it's down/undeployed
      results.push({
        worker: name, status: 'down', statusCode: 0, latencyMs: 0,
        version: '', error: 'Not deployed — not found in Cloudflare account',
      });
    }
  }

  // Store results in D1
  const stmt = env.DB.prepare('INSERT INTO health_checks (worker_name, status, status_code, latency_ms, version, error) VALUES (?, ?, ?, ?, ?, ?)');
  const batches = [];
  for (let i = 0; i < results.length; i += 25) {
    const chunk = results.slice(i, i + 25);
    batches.push(env.DB.batch(chunk.map(r => stmt.bind(r.worker, r.status, r.statusCode, r.latencyMs, r.version, r.error))));
  }
  await Promise.allSettled(batches);

  const down = results.filter(r => r.status === 'down' || r.status === 'timeout');
  const degraded = results.filter(r => r.status === 'degraded');

  // Create incidents for down workers
  for (const d of down) {
    const existing = await env.DB.prepare('SELECT id FROM incidents WHERE worker_name = ? AND resolved_at IS NULL AND type = ?').bind(d.worker, 'down').first();
    if (!existing) {
      await env.DB.prepare('INSERT INTO incidents (worker_name, type, severity, description) VALUES (?, ?, ?, ?)').bind(d.worker, 'down', 'critical', `Worker ${d.error || 'unreachable'}`).run();
    }
  }

  // Auto-resolve incidents for workers that are back up
  const healthy = results.filter(r => r.status === 'healthy');
  for (const h of healthy) {
    await env.DB.prepare("UPDATE incidents SET resolved_at = datetime('now'), auto_resolved = 1 WHERE worker_name = ? AND resolved_at IS NULL").bind(h.worker).run();
  }

  // Cache summary
  const summary = { total: results.length, healthy: healthy.length, degraded: degraded.length, down: down.length, checkedAt: new Date().toISOString() };
  await env.CACHE.put('last_health_sweep', JSON.stringify(summary), { expirationTtl: 600 });
  await env.CACHE.put('last_health_details', JSON.stringify(results), { expirationTtl: 600 });

  return { results, down, degraded };
}

// ═══════════════════════════════════════════════════════════════
// MUTUAL WATCHDOG — PARTNER GUARDIAN CHECK + RESURRECTION
// ═══════════════════════════════════════════════════════════════
async function checkPartner(env: Env): Promise<{ alive: boolean; latency: number; consecutiveFailures: number }> {
  const start = Date.now();
  let alive = false;
  let error = '';

  try {
    // Use service binding (Workers can't fetch each other via public URL on same account)
    const res = await env.SVC_PARTNER.fetch(new Request('https://partner/health'));
    alive = res.status === 200;
    if (!alive) error = `HTTP ${res.status}`;
  } catch (e: unknown) {
    error = e instanceof Error ? e.message : String(e);
  }

  const latency = Date.now() - start;

  // Get consecutive failure count
  const last = await env.DB.prepare('SELECT consecutive_failures FROM partner_health ORDER BY id DESC LIMIT 1').first<{ consecutive_failures: number }>();
  const consecutiveFailures = alive ? 0 : (last?.consecutive_failures || 0) + 1;

  await env.DB.prepare('INSERT INTO partner_health (partner_name, status, latency_ms, consecutive_failures, last_success) VALUES (?, ?, ?, ?, ?)').bind(
    env.PARTNER_NAME, alive ? 'healthy' : 'down', latency, consecutiveFailures, alive ? new Date().toISOString() : null
  ).run();

  slog(alive ? 'info' : 'warn', `Partner ${env.PARTNER_NAME} check`, { alive, latency, consecutiveFailures, error });

  return { alive, latency, consecutiveFailures };
}

async function resurrectPartner(env: Env): Promise<boolean> {
  slog('warn', `RESURRECTING partner ${env.PARTNER_NAME}`, { guardian: env.GUARDIAN_ID });

  try {
    // Step 1: Check if the worker script exists via CF API
    const cfRes = await fetch(
      `https://api.cloudflare.com/client/v4/accounts/${env.CLOUDFLARE_ACCOUNT_ID}/workers/scripts/${env.PARTNER_NAME}`,
      { headers: { 'Authorization': `Bearer ${env.CLOUDFLARE_API_TOKEN}` } }
    );

    if (!cfRes.ok) {
      slog('error', 'Partner script not found in CF account', { status: cfRes.status });
      await reportToBrain(env, `GUARDIAN ${env.GUARDIAN_ID}: Partner ${env.PARTNER_NAME} script not found in Cloudflare (${cfRes.status}). Manual intervention needed.`, 10);
      return false;
    }

    // Step 2: Try to get the script content and re-deploy it (triggers a fresh deployment)
    const redeployRes = await fetch(
      `https://api.cloudflare.com/client/v4/accounts/${env.CLOUDFLARE_ACCOUNT_ID}/workers/scripts/${env.PARTNER_NAME}/deployments`,
      {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${env.CLOUDFLARE_API_TOKEN}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ strategy: 'percentage', versions: [{ percentage: 100 }] }),
      }
    );

    if (!redeployRes.ok) {
      // Fallback: just do a no-op settings update to trigger restart
      const settingsRes = await fetch(
        `https://api.cloudflare.com/client/v4/accounts/${env.CLOUDFLARE_ACCOUNT_ID}/workers/scripts/${env.PARTNER_NAME}/settings`,
        {
          method: 'PATCH',
          headers: {
            'Authorization': `Bearer ${env.CLOUDFLARE_API_TOKEN}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ logpush: false }),
        }
      );
      slog('info', 'Fallback settings patch', { status: settingsRes.status });
    }

    // Step 3: Wait 15s and re-check
    await new Promise(r => setTimeout(r, 15000));
    const verifyRes = await fetch(`${env.PARTNER_URL}/health`, { signal: AbortSignal.timeout(10000) }).catch(() => null);
    const resurrected = verifyRes?.ok || false;

    await env.DB.prepare("UPDATE partner_health SET resurrection_attempted = 1 WHERE partner_name = ? ORDER BY id DESC LIMIT 1").bind(env.PARTNER_NAME).run();

    await reportToBrain(env, `GUARDIAN ${env.GUARDIAN_ID}: ${resurrected ? 'SUCCESSFULLY' : 'FAILED TO'} resurrect partner ${env.PARTNER_NAME}. Used CF API deployment trigger.`, resurrected ? 8 : 10);

    slog(resurrected ? 'info' : 'error', `Partner resurrection ${resurrected ? 'SUCCESS' : 'FAILED'}`, { partner: env.PARTNER_NAME });
    return resurrected;
  } catch (e: unknown) {
    slog('error', 'Resurrection error', { error: e instanceof Error ? e.message : String(e) });
    return false;
  }
}

// ═══════════════════════════════════════════════════════════════
// GITHUB API — READ / WRITE / CREATE REPOS
// ═══════════════════════════════════════════════════════════════
async function githubGet(env: Env, path: string): Promise<unknown> {
  const res = await fetch(`https://api.github.com${path}`, {
    headers: { 'Authorization': `token ${env.GITHUB_TOKEN}`, 'Accept': 'application/vnd.github.v3+json', 'User-Agent': 'EchoGuardian/1.0' },
  });
  if (!res.ok) return null;
  return res.json();
}

async function githubPut(env: Env, path: string, body: Record<string, unknown>): Promise<{ ok: boolean; status: number }> {
  const res = await fetch(`https://api.github.com${path}`, {
    method: 'PUT',
    headers: { 'Authorization': `token ${env.GITHUB_TOKEN}`, 'Accept': 'application/vnd.github.v3+json', 'User-Agent': 'EchoGuardian/1.0', 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return { ok: res.ok, status: res.status };
}

async function githubPost(env: Env, path: string, body: Record<string, unknown>): Promise<{ ok: boolean; data: unknown }> {
  const res = await fetch(`https://api.github.com${path}`, {
    method: 'POST',
    headers: { 'Authorization': `token ${env.GITHUB_TOKEN}`, 'Accept': 'application/vnd.github.v3+json', 'User-Agent': 'EchoGuardian/1.0', 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => null);
  return { ok: res.ok, data };
}

async function readWorkerSource(env: Env, workerName: string): Promise<string | null> {
  const data = await githubGet(env, `/repos/${env.GITHUB_ORG}/${workerName}/contents/src/index.ts`) as { content?: string; encoding?: string } | null;
  if (!data?.content) return null;
  return atob(data.content.replace(/\n/g, ''));
}

async function pushWorkerSource(env: Env, workerName: string, filePath: string, content: string, message: string): Promise<boolean> {
  // Get current file SHA
  const existing = await githubGet(env, `/repos/${env.GITHUB_ORG}/${workerName}/contents/${filePath}`) as { sha?: string } | null;
  const body: Record<string, unknown> = {
    message,
    content: btoa(unescape(encodeURIComponent(content))),
    committer: { name: `Echo Guardian ${env.GUARDIAN_ID}`, email: 'guardian@echo-op.com' },
  };
  if (existing?.sha) body.sha = existing.sha;

  const result = await githubPut(env, `/repos/${env.GITHUB_ORG}/${workerName}/contents/${filePath}`, body);
  return result.ok;
}

async function createGithubRepo(env: Env, name: string, description: string): Promise<boolean> {
  const result = await githubPost(env, `/orgs/${env.GITHUB_ORG}/repos`, {
    name, description, private: false, auto_init: false,
  });
  return result.ok;
}

// ═══════════════════════════════════════════════════════════════
// CLOUDFLARE API — DEPLOY WORKERS
// ═══════════════════════════════════════════════════════════════
async function deployWorkerScript(env: Env, workerName: string, script: string, bindings?: Record<string, unknown>): Promise<boolean> {
  try {
    // Simple script upload (no bindings change)
    const formData = new FormData();
    formData.append('worker.js', new Blob([script], { type: 'application/javascript+module' }), 'worker.js');

    const metadata: Record<string, unknown> = {
      main_module: 'worker.js',
      compatibility_date: '2024-12-01',
    };
    if (bindings) metadata.bindings = bindings;
    formData.append('metadata', new Blob([JSON.stringify(metadata)], { type: 'application/json' }));

    const res = await fetch(
      `https://api.cloudflare.com/client/v4/accounts/${env.CLOUDFLARE_ACCOUNT_ID}/workers/scripts/${workerName}`,
      {
        method: 'PUT',
        headers: { 'Authorization': `Bearer ${env.CLOUDFLARE_API_TOKEN}` },
        body: formData,
      }
    );

    slog(res.ok ? 'info' : 'error', `Deploy ${workerName}`, { status: res.status });
    return res.ok;
  } catch (e: unknown) {
    slog('error', `Deploy failed for ${workerName}`, { error: e instanceof Error ? e.message : String(e) });
    return false;
  }
}

// ═══════════════════════════════════════════════════════════════
// AI ENHANCEMENT ENGINE — Analyze + Improve Workers
// ═══════════════════════════════════════════════════════════════
interface EnhancementPlan {
  type: 'security' | 'performance' | 'feature' | 'hardening' | 'optimization' | 'upgrade';
  description: string;
  priority: number;
  codeChanges: { file: string; before: string; after: string }[];
}

async function analyzeWorkerWithAI(env: Env, workerName: string, sourceCode: string): Promise<EnhancementPlan[]> {
  try {
    // Use Engine Runtime for code analysis
    const prompt = `Analyze this Cloudflare Worker source code for "${workerName}" and identify specific improvements.

RULES:
- Only suggest changes that are SAFE and won't break existing functionality
- Focus on: missing /health endpoint, missing structured logging, missing CORS headers, missing auth on write endpoints, SQL injection risks, missing error handling, performance improvements
- Return a JSON array of enhancement plans
- Each plan must have: type (security|performance|feature|hardening|optimization|upgrade), description, priority (1-10), and specific code changes
- Do NOT suggest changes to business logic — only infrastructure/quality improvements
- Maximum 3 enhancements per analysis
- Be VERY specific about what code to add/change

SOURCE CODE:
\`\`\`typescript
${sourceCode.substring(0, 15000)}
\`\`\`

Return ONLY a JSON array, no markdown fences:
[{"type":"...", "description":"...", "priority": N, "codeChanges": [{"file":"src/index.ts", "before":"exact code to find", "after":"replacement code"}]}]`;

    const res = await env.SVC_ENGINE_RUNTIME.fetch('https://engine/query/reason', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: prompt, domain: 'PROG', max_doctrines: 3 }),
    });

    if (!res.ok) {
      slog('warn', 'Engine Runtime analysis failed', { worker: workerName, status: res.status });
      return [];
    }

    const result = await res.json() as { conclusion?: string; reasoning?: string };
    const text = result.conclusion || result.reasoning || '';

    // Extract JSON from response
    const jsonMatch = text.match(/\[[\s\S]*\]/);
    if (!jsonMatch) return [];

    const plans = JSON.parse(jsonMatch[0]) as EnhancementPlan[];
    return plans.filter(p => p.type && p.description && p.codeChanges?.length > 0).slice(0, 3);
  } catch (e: unknown) {
    slog('error', 'AI analysis error', { worker: workerName, error: e instanceof Error ? e.message : String(e) });
    return [];
  }
}

async function applyEnhancement(env: Env, workerName: string, plan: EnhancementPlan, sourceCode: string): Promise<boolean> {
  let modifiedCode = sourceCode;

  for (const change of plan.codeChanges) {
    if (!change.before || !change.after) continue;
    if (!modifiedCode.includes(change.before)) {
      slog('warn', 'Code pattern not found, skipping change', { worker: workerName, pattern: change.before.substring(0, 100) });
      continue;
    }
    modifiedCode = modifiedCode.replace(change.before, change.after);
  }

  if (modifiedCode === sourceCode) {
    slog('info', 'No code changes applied', { worker: workerName });
    return false;
  }

  // Push to GitHub
  const commitMsg = `guardian(${env.GUARDIAN_ID}): ${plan.type} — ${plan.description}`;
  const pushed = await pushWorkerSource(env, workerName, 'src/index.ts', modifiedCode, commitMsg);

  if (!pushed) {
    slog('error', 'Failed to push enhancement to GitHub', { worker: workerName });
    return false;
  }

  // Log the enhancement
  await env.DB.prepare('INSERT INTO enhancements (worker_name, type, description, files_changed, code_before, code_after, ai_reasoning, github_url, deployed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)').bind(
    workerName, plan.type, plan.description, 'src/index.ts',
    plan.codeChanges.map(c => c.before).join('\n---\n').substring(0, 5000),
    plan.codeChanges.map(c => c.after).join('\n---\n').substring(0, 5000),
    plan.description, `https://github.com/${env.GITHUB_ORG}/${workerName}`, 0
  ).run();

  slog('info', 'Enhancement applied', { worker: workerName, type: plan.type, description: plan.description });
  return true;
}

async function enhancementScan(env: Env): Promise<{ scanned: number; enhanced: number }> {
  // Check rate limit — max 5 enhancements per day per guardian
  const todayCount = await env.DB.prepare("SELECT COUNT(*) as cnt FROM enhancements WHERE created_at > datetime('now', '-1 day')").first<{ cnt: number }>();
  if ((todayCount?.cnt || 0) >= 5) {
    slog('info', 'Daily enhancement limit reached', { count: todayCount?.cnt });
    return { scanned: 0, enhanced: 0 };
  }

  // Coordinate with partner — claim workers to avoid duplicates
  const lockKey = `enhance_lock_${new Date().toISOString().split('T')[0]}`;
  const existingLock = await env.CACHE.get(lockKey);
  const claimed = existingLock ? JSON.parse(existingLock) as string[] : [];

  // Pick workers to analyze — rotate through tiers
  const tierIndex = Math.floor(Date.now() / 1800000) % ALL_TIERS.length;
  const tier = ALL_TIERS[tierIndex];
  const candidates = tier.workers.filter(w => !claimed.includes(w));
  const toScan = candidates.slice(0, 3);

  if (toScan.length === 0) return { scanned: 0, enhanced: 0 };

  // Claim these workers
  claimed.push(...toScan);
  await env.CACHE.put(lockKey, JSON.stringify(claimed), { expirationTtl: 3600 });

  let scanned = 0;
  let enhanced = 0;

  for (const workerName of toScan) {
    scanned++;

    // Read source from GitHub
    const source = await readWorkerSource(env, workerName);
    if (!source) {
      slog('info', 'No source found on GitHub', { worker: workerName });
      continue;
    }

    // Analyze with AI
    const plans = await analyzeWorkerWithAI(env, workerName, source);
    if (plans.length === 0) {
      slog('info', 'No enhancements needed', { worker: workerName });
      continue;
    }

    // Apply highest priority enhancement
    const topPlan = plans.sort((a, b) => (b.priority || 0) - (a.priority || 0))[0];
    const applied = await applyEnhancement(env, workerName, topPlan, source);
    if (applied) {
      enhanced++;
      await reportToBrain(env, `GUARDIAN ${env.GUARDIAN_ID}: Enhanced ${workerName} — ${topPlan.type}: ${topPlan.description}`, 7);
    }
  }

  return { scanned, enhanced };
}

// ═══════════════════════════════════════════════════════════════
// DEEP AUDIT — Security Hardening + Feature Analysis
// ═══════════════════════════════════════════════════════════════
async function deepAudit(env: Env): Promise<{ audited: number; findings: number; fixed: number }> {
  const todayEnhancements = await env.DB.prepare("SELECT COUNT(*) as cnt FROM enhancements WHERE created_at > datetime('now', '-1 day')").first<{ cnt: number }>();
  if ((todayEnhancements?.cnt || 0) >= 5) return { audited: 0, findings: 0, fixed: 0 };

  // Rotate through workers — audit 5 per cycle
  const offset = await env.CACHE.get('audit_offset');
  const currentOffset = offset ? parseInt(offset) : 0;
  const allWorkers = getAllWorkers();
  const toAudit = allWorkers.slice(currentOffset, currentOffset + 5);
  await env.CACHE.put('audit_offset', String((currentOffset + 5) % allWorkers.length), { expirationTtl: 86400 * 7 });

  let audited = 0;
  let findings = 0;
  let fixed = 0;

  for (const workerName of toAudit) {
    audited++;
    const source = await readWorkerSource(env, workerName);
    if (!source) continue;

    // Quick static analysis checks
    const issues: string[] = [];

    if (!source.includes('/health')) issues.push('MISSING: /health endpoint');
    if (source.includes('console.log(') && !source.includes('JSON.stringify')) issues.push('HARDENING: Use structured JSON logging instead of console.log');
    if (source.includes("'+") || source.includes("\" +")) {
      if (source.includes('.prepare(') && (source.includes("'+") || source.includes('`${'))) {
        issues.push('SECURITY: Possible SQL string concatenation — use parameterized queries');
      }
    }
    if (!source.includes('Access-Control-Allow-Origin') && !source.includes('cors')) issues.push('HARDENING: Missing CORS headers');
    if ((source.includes('POST') || source.includes('PUT') || source.includes('DELETE')) && !source.includes('API_KEY') && !source.includes('auth') && !source.includes('Auth')) {
      issues.push('SECURITY: Write endpoints may lack authentication');
    }
    if (!source.includes('try') || !source.includes('catch')) issues.push('HARDENING: Missing error handling');

    findings += issues.length;

    if (issues.length > 0) {
      // Queue for enhancement
      for (const issue of issues) {
        const type = issue.startsWith('SECURITY') ? 'security' : issue.startsWith('HARDENING') ? 'hardening' : 'optimization';
        await env.DB.prepare('INSERT INTO enhancement_queue (worker_name, priority, type, analysis, status) VALUES (?, ?, ?, ?, ?)').bind(
          workerName, type === 'security' ? 9 : 5, type, issue, 'pending'
        ).run();
      }

      // Try to auto-fix the highest priority issue via AI
      const plans = await analyzeWorkerWithAI(env, workerName, source);
      if (plans.length > 0) {
        const applied = await applyEnhancement(env, workerName, plans[0], source);
        if (applied) fixed++;
      }
    }
  }

  slog('info', 'Deep audit complete', { audited, findings, fixed });
  return { audited, findings, fixed };
}

// ═══════════════════════════════════════════════════════════════
// NEW WORKER CREATION ENGINE
// ═══════════════════════════════════════════════════════════════
async function evaluateNewWorkerNeeds(env: Env): Promise<{ proposed: number; created: number }> {
  // Rate limit: max 1 new worker per week
  const recentCreations = await env.DB.prepare("SELECT COUNT(*) as cnt FROM creations WHERE created_at > datetime('now', '-7 days')").first<{ cnt: number }>();
  if ((recentCreations?.cnt || 0) >= 1) return { proposed: 0, created: 0 };

  // Analyze ecosystem gaps via Engine Runtime
  const allWorkerNames = getAllWorkers().join(', ');
  const prompt = `You are analyzing the ECHO OMEGA PRIME SaaS ecosystem. We currently have these Cloudflare Workers deployed:
${allWorkerNames}

These cover: CRM, helpdesk, booking, invoicing, HR, LMS, email marketing, surveys, forms, contracts,
inventory, project management, live chat, link shortener, feedback board, newsletter, web analytics,
waitlist, reviews, signatures, affiliate, proposals, gamer companion, QR menu, podcast, calendar,
payroll, recruiting, compliance, timesheet, expense, OKR, finance, documents, workflows,
social media management, knowledge base, workflow automation, document manager, and more.

Identify ONE SaaS product that is commonly needed by small-to-medium businesses but MISSING from our ecosystem.
Requirements:
- Must be a product that would generate revenue
- Must NOT duplicate any existing worker
- Must be buildable as a single Cloudflare Worker with D1 + KV
- Must serve a clear market need

Return ONLY a JSON object (no markdown fences):
{"name": "echo-PRODUCT-NAME", "description": "What it does", "reason": "Why businesses need it", "endpoints": ["list of 5-10 key endpoints"], "d1_tables": ["list of table names"], "estimated_lines": N, "revenue_potential": "low|medium|high"}

If the ecosystem is comprehensive and no clear gap exists, return: {"name": "none", "reason": "Ecosystem is comprehensive"}`;

  try {
    const res = await env.SVC_ENGINE_RUNTIME.fetch('https://engine/query/reason', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: prompt, domain: 'SAAS', max_doctrines: 3 }),
    });

    if (!res.ok) return { proposed: 0, created: 0 };

    const result = await res.json() as { conclusion?: string };
    const text = result.conclusion || '';
    const jsonMatch = text.match(/\{[\s\S]*\}/);
    if (!jsonMatch) return { proposed: 0, created: 0 };

    const proposal = JSON.parse(jsonMatch[0]) as { name: string; description?: string; reason?: string; endpoints?: string[]; d1_tables?: string[]; estimated_lines?: number; revenue_potential?: string };

    if (proposal.name === 'none' || !proposal.name) {
      slog('info', 'No new worker needed — ecosystem comprehensive');
      return { proposed: 0, created: 0 };
    }

    // Log the proposal
    await env.DB.prepare('INSERT INTO creations (worker_name, reason, description, lines_of_code, deployed) VALUES (?, ?, ?, ?, 0)').bind(
      proposal.name, proposal.reason || '', proposal.description || '', proposal.estimated_lines || 0
    ).run();

    await reportToBrain(env, `GUARDIAN ${env.GUARDIAN_ID} PROPOSES NEW WORKER: ${proposal.name} — ${proposal.description}. Reason: ${proposal.reason}. Revenue potential: ${proposal.revenue_potential}. Queued for Commander review.`, 8);

    slog('info', 'New worker proposed', { name: proposal.name, reason: proposal.reason });

    // For safety, new worker creation requires the proposal to be reviewed
    // The guardian will actually BUILD it on the next daily cycle if no rejection
    const approved = await checkProposalApproval(env, proposal.name);
    if (approved) {
      const built = await buildNewWorker(env, proposal);
      return { proposed: 1, created: built ? 1 : 0 };
    }

    return { proposed: 1, created: 0 };
  } catch (e: unknown) {
    slog('error', 'New worker evaluation error', { error: e instanceof Error ? e.message : String(e) });
    return { proposed: 0, created: 0 };
  }
}

async function checkProposalApproval(env: Env, workerName: string): Promise<boolean> {
  // Auto-approve if proposal has been in DB for 24h+ with no rejection
  const proposal = await env.DB.prepare("SELECT created_at FROM creations WHERE worker_name = ? AND deployed = 0 ORDER BY id DESC LIMIT 1").first<{ created_at: string }>();
  if (!proposal) return false;
  const created = new Date(proposal.created_at).getTime();
  const now = Date.now();
  return (now - created) > 24 * 60 * 60 * 1000; // 24h auto-approve
}

async function buildNewWorker(env: Env, proposal: { name: string; description?: string; endpoints?: string[]; d1_tables?: string[] }): Promise<boolean> {
  try {
    // Generate worker code via Engine Runtime
    const prompt = `Generate a complete Cloudflare Worker (TypeScript + Hono framework) for "${proposal.name}".

Description: ${proposal.description}
Key endpoints: ${(proposal.endpoints || []).join(', ')}
D1 tables: ${(proposal.d1_tables || []).join(', ')}

REQUIREMENTS:
- Use Hono framework with TypeScript
- Include structured JSON logging via slog() function
- Include /health endpoint that checks D1
- Include / root that returns service metadata JSON
- Include CORS middleware for echo-ept.com, echo-op.com
- Include auth middleware (X-Echo-API-Key) on write endpoints
- Use parameterized D1 queries (never string concat)
- Include D1 schema as SQL string constant
- Include auto-schema initialization
- Export default with fetch handler and scheduled handler
- usage_model = "unbound"

Return ONLY the TypeScript code, no markdown fences.`;

    const res = await env.SVC_ENGINE_RUNTIME.fetch('https://engine/query/reason', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: prompt, domain: 'PROG', max_doctrines: 3 }),
    });

    if (!res.ok) return false;

    const result = await res.json() as { conclusion?: string };
    const code = result.conclusion || '';
    if (code.length < 500) return false;

    // Create GitHub repo
    await createGithubRepo(env, proposal.name, proposal.description || `Auto-generated by Echo Guardian ${env.GUARDIAN_ID}`);

    // Push source code
    await pushWorkerSource(env, proposal.name, 'src/index.ts', code, `guardian(${env.GUARDIAN_ID}): initial build — ${proposal.description}`);

    // Generate wrangler.toml
    const wranglerToml = `name = "${proposal.name}"
main = "src/index.ts"
compatibility_date = "2024-12-01"
usage_model = "unbound"

[[d1_databases]]
binding = "DB"
database_name = "${proposal.name}"
database_id = ""

[[kv_namespaces]]
binding = "CACHE"
id = ""
`;
    await pushWorkerSource(env, proposal.name, 'wrangler.toml', wranglerToml, `guardian(${env.GUARDIAN_ID}): add wrangler config`);

    // Update creation record
    await env.DB.prepare("UPDATE creations SET deployed = 0, github_repo = ?, lines_of_code = ? WHERE worker_name = ? ORDER BY id DESC LIMIT 1").bind(
      `https://github.com/${env.GITHUB_ORG}/${proposal.name}`, code.split('\n').length, proposal.name
    ).run();

    await reportToBrain(env, `GUARDIAN ${env.GUARDIAN_ID}: BUILT NEW WORKER ${proposal.name} — ${proposal.description}. ${code.split('\n').length} lines. Pushed to GitHub. Manual wrangler deploy needed for D1/KV binding IDs.`, 9);

    slog('info', 'New worker built', { name: proposal.name, lines: code.split('\n').length });
    return true;
  } catch (e: unknown) {
    slog('error', 'Build new worker failed', { name: proposal.name, error: e instanceof Error ? e.message : String(e) });
    return false;
  }
}

// ═══════════════════════════════════════════════════════════════
// SHARED BRAIN + MOLTBOOK REPORTING
// ═══════════════════════════════════════════════════════════════
async function reportToBrain(env: Env, content: string, importance: number) {
  try {
    await env.SVC_SHARED_BRAIN.fetch('https://brain/ingest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        instance_id: `guardian_${env.GUARDIAN_ID}`,
        role: 'assistant',
        content,
        importance,
        tags: ['guardian', env.GUARDIAN_ID, 'autonomous'],
      }),
    });
  } catch { /* best effort */ }
}

async function postToMoltBook(env: Env, content: string, mood: string) {
  try {
    await fetch('https://echo-swarm-brain.bmcii1976.workers.dev/moltbook/post', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        author_id: `GUARDIAN-${env.GUARDIAN_ID.toUpperCase()}`,
        author_name: `Guardian ${env.GUARDIAN_ID}`,
        author_type: 'agent',
        content: `GUARDIAN-${env.GUARDIAN_ID.toUpperCase()}: ${content}`,
        mood,
        tags: ['guardian', 'autonomous', env.GUARDIAN_ID],
      }),
    });
  } catch { /* best effort */ }
}

// ═══════════════════════════════════════════════════════════════
// DAILY REPORT
// ═══════════════════════════════════════════════════════════════
async function dailyReport(env: Env): Promise<string> {
  const [healthStats, incidents24h, enhancements24h, openIncidents, partnerStatus, creations7d] = await Promise.all([
    env.DB.prepare("SELECT status, COUNT(*) as cnt FROM health_checks WHERE checked_at > datetime('now', '-24 hours') GROUP BY status").all(),
    env.DB.prepare("SELECT COUNT(*) as cnt FROM incidents WHERE created_at > datetime('now', '-24 hours')").first<{ cnt: number }>(),
    env.DB.prepare("SELECT COUNT(*) as cnt FROM enhancements WHERE created_at > datetime('now', '-24 hours')").first<{ cnt: number }>(),
    env.DB.prepare("SELECT COUNT(*) as cnt FROM incidents WHERE resolved_at IS NULL").first<{ cnt: number }>(),
    env.DB.prepare("SELECT status, consecutive_failures FROM partner_health ORDER BY id DESC LIMIT 1").first<{ status: string; consecutive_failures: number }>(),
    env.DB.prepare("SELECT COUNT(*) as cnt FROM creations WHERE created_at > datetime('now', '-7 days')").first<{ cnt: number }>(),
  ]);

  const statusCounts: Record<string, number> = {};
  for (const row of (healthStats?.results || []) as { status: string; cnt: number }[]) {
    statusCounts[row.status] = row.cnt;
  }

  const report = `GUARDIAN ${env.GUARDIAN_ID.toUpperCase()} DAILY REPORT — ${new Date().toISOString().split('T')[0]}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HEALTH CHECKS (24h): ${Object.entries(statusCounts).map(([k, v]) => `${k}=${v}`).join(', ')}
INCIDENTS (24h): ${incidents24h?.cnt || 0} | OPEN: ${openIncidents?.cnt || 0}
ENHANCEMENTS (24h): ${enhancements24h?.cnt || 0}
NEW WORKERS (7d): ${creations7d?.cnt || 0}
PARTNER: ${partnerStatus?.status || 'unknown'} (failures: ${partnerStatus?.consecutive_failures || 0})
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`;

  await reportToBrain(env, report, 7);
  await postToMoltBook(env, report, 'reporting');

  // Also evaluate if new workers are needed
  await evaluateNewWorkerNeeds(env);

  return report;
}

// ═══════════════════════════════════════════════════════════════
// CRON HANDLER
// ═══════════════════════════════════════════════════════════════
async function handleCron(event: ScheduledEvent, env: Env) {
  await ensureSchema(env.DB);
  const cronMinute = new Date(event.scheduledTime).getMinutes();
  const cronHour = new Date(event.scheduledTime).getHours();

  slog('info', 'Cron triggered', { guardian: env.GUARDIAN_ID, minute: cronMinute, hour: cronHour, cron: event.cron });

  // Every 5 min: Health sweep + Partner check
  if (event.cron.includes('*/5') || event.cron.includes('2/5')) {
    const { results, down, degraded } = await healthSweep(env);

    // Check partner guardian
    const partner = await checkPartner(env);
    if (!partner.alive && partner.consecutiveFailures >= 3) {
      slog('warn', 'Partner guardian DOWN for 3+ checks — initiating resurrection', { partner: env.PARTNER_NAME });
      await resurrectPartner(env);
    }

    // Alert on critical down workers
    if (down.length > 0) {
      const downList = down.map(d => d.worker).join(', ');
      await reportToBrain(env, `GUARDIAN ${env.GUARDIAN_ID} ALERT: ${down.length} workers DOWN — ${downList}`, 9);
    }

    // Try to resurrect down workers via CF API (re-deploy trigger)
    for (const d of down) {
      try {
        // Check if script exists — if yes, trigger a settings patch to restart
        const cfCheck = await fetch(
          `https://api.cloudflare.com/client/v4/accounts/${env.CLOUDFLARE_ACCOUNT_ID}/workers/scripts/${d.worker}`,
          { headers: { 'Authorization': `Bearer ${env.CLOUDFLARE_API_TOKEN}` } }
        );
        if (cfCheck.ok) {
          // Script exists but not responding — try settings patch to trigger restart
          await fetch(
            `https://api.cloudflare.com/client/v4/accounts/${env.CLOUDFLARE_ACCOUNT_ID}/workers/scripts/${d.worker}/settings`,
            {
              method: 'PATCH',
              headers: { 'Authorization': `Bearer ${env.CLOUDFLARE_API_TOKEN}`, 'Content-Type': 'application/json' },
              body: JSON.stringify({ logpush: false }),
            }
          );
          slog('info', 'Attempted restart via settings patch', { worker: d.worker });
        } else {
          slog('warn', 'Down worker not found in CF account', { worker: d.worker, status: cfCheck.status });
        }
      } catch { /* best effort */ }
    }

    slog('info', 'Health sweep complete', { total: results.length, healthy: results.length - down.length - degraded.length, degraded: degraded.length, down: down.length, partnerAlive: partner.alive });
  }

  // Every 30 min: Enhancement scan
  if (event.cron.includes('*/30') || event.cron.includes('15/30')) {
    const { scanned, enhanced } = await enhancementScan(env);
    slog('info', 'Enhancement scan complete', { scanned, enhanced });
  }

  // Every 6 hours: Deep audit
  if (event.cron.includes('*/6') || event.cron.includes('3/6')) {
    const { audited, findings, fixed } = await deepAudit(env);
    slog('info', 'Deep audit complete', { audited, findings, fixed });
  }

  // Daily: Full report + new worker evaluation
  if (event.cron.includes('0 8') || event.cron.includes('0 9')) {
    await dailyReport(env);
    slog('info', 'Daily report generated');
  }
}

// ═══════════════════════════════════════════════════════════════
// HTTP API
// ═══════════════════════════════════════════════════════════════
function corsHeaders(origin?: string): Record<string, string> {
  const allowed = ['https://echo-ept.com', 'https://echo-op.com', 'https://echo-sdk-gateway.bmcii1976.workers.dev'];
  const o = origin && allowed.some(a => origin.startsWith(a)) ? origin : allowed[0];
  return { 'Access-Control-Allow-Origin': o, 'Access-Control-Allow-Methods': 'GET,POST,OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type,X-Echo-API-Key' };
}

function json(data: unknown, status = 200, origin?: string) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json', ...corsHeaders(origin) },
  });
}

async function handleRequest(request: Request, env: Env): Promise<Response> {
  await ensureSchema(env.DB);
  const url = new URL(request.url);
  const path = url.pathname;
  const origin = request.headers.get('Origin') || undefined;

  if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: corsHeaders(origin) });

  // Root — service metadata
  if (path === '/') {
    return json({
      status: 'ok',
      service: `echo-guardian-${env.GUARDIAN_ID}`,
      version: '1.0.0',
      partner: env.PARTNER_NAME,
      capabilities: ['health_monitoring', 'mutual_watchdog', 'ai_enhancement', 'security_hardening', 'feature_addition', 'worker_creation', 'autonomous_deployment'],
      workers_monitored: getAllWorkers().length,
      tiers: ALL_TIERS.map(t => ({ name: t.name, count: t.workers.length })),
    }, 200, origin);
  }

  // Health endpoint
  if (path === '/health') {
    const lastSweep = await env.CACHE.get('last_health_sweep');
    const partnerStatus = await env.DB.prepare('SELECT status, consecutive_failures FROM partner_health ORDER BY id DESC LIMIT 1').first();
    return json({
      status: 'ok',
      service: `echo-guardian-${env.GUARDIAN_ID}`,
      version: '1.0.0',
      partner: { name: env.PARTNER_NAME, ...(partnerStatus || { status: 'unknown' }) },
      lastSweep: lastSweep ? JSON.parse(lastSweep) : null,
    }, 200, origin);
  }

  // Fleet health status
  if (path === '/fleet') {
    const details = await env.CACHE.get('last_health_details');
    return json({
      workers: details ? JSON.parse(details) : [],
      total: getAllWorkers().length,
    }, 200, origin);
  }

  // Incidents
  if (path === '/incidents') {
    const open = url.searchParams.get('open') === 'true';
    const limit = parseInt(url.searchParams.get('limit') || '50');
    const query = open
      ? 'SELECT * FROM incidents WHERE resolved_at IS NULL ORDER BY created_at DESC LIMIT ?'
      : 'SELECT * FROM incidents ORDER BY created_at DESC LIMIT ?';
    const results = await env.DB.prepare(query).bind(limit).all();
    return json({ incidents: results.results, count: results.results?.length || 0 }, 200, origin);
  }

  // Enhancements log
  if (path === '/enhancements') {
    const limit = parseInt(url.searchParams.get('limit') || '50');
    const results = await env.DB.prepare('SELECT id, worker_name, type, description, deployed, created_at FROM enhancements ORDER BY created_at DESC LIMIT ?').bind(limit).all();
    return json({ enhancements: results.results, count: results.results?.length || 0 }, 200, origin);
  }

  // Enhancement queue
  if (path === '/queue') {
    const results = await env.DB.prepare("SELECT * FROM enhancement_queue WHERE status = 'pending' ORDER BY priority DESC LIMIT 50").all();
    return json({ queue: results.results, count: results.results?.length || 0 }, 200, origin);
  }

  // Proposed/created workers
  if (path === '/creations') {
    const results = await env.DB.prepare('SELECT * FROM creations ORDER BY created_at DESC LIMIT 20').all();
    return json({ creations: results.results, count: results.results?.length || 0 }, 200, origin);
  }

  // Partner health history
  if (path === '/partner') {
    const results = await env.DB.prepare('SELECT * FROM partner_health ORDER BY id DESC LIMIT 100').all();
    return json({ partner: env.PARTNER_NAME, history: results.results }, 200, origin);
  }

  // Stats dashboard
  if (path === '/stats') {
    const [totalChecks, incidents24h, enhancementsTotal, openIncidents, creationsTotal, avgLatency] = await Promise.all([
      env.DB.prepare('SELECT COUNT(*) as cnt FROM health_checks').first<{ cnt: number }>(),
      env.DB.prepare("SELECT COUNT(*) as cnt FROM incidents WHERE created_at > datetime('now', '-24 hours')").first<{ cnt: number }>(),
      env.DB.prepare('SELECT COUNT(*) as cnt FROM enhancements').first<{ cnt: number }>(),
      env.DB.prepare('SELECT COUNT(*) as cnt FROM incidents WHERE resolved_at IS NULL').first<{ cnt: number }>(),
      env.DB.prepare('SELECT COUNT(*) as cnt FROM creations').first<{ cnt: number }>(),
      env.DB.prepare("SELECT AVG(latency_ms) as avg_ms FROM health_checks WHERE checked_at > datetime('now', '-1 hour') AND status = 'healthy'").first<{ avg_ms: number }>(),
    ]);

    return json({
      guardian: env.GUARDIAN_ID,
      totalHealthChecks: totalChecks?.cnt || 0,
      incidents24h: incidents24h?.cnt || 0,
      openIncidents: openIncidents?.cnt || 0,
      totalEnhancements: enhancementsTotal?.cnt || 0,
      totalCreations: creationsTotal?.cnt || 0,
      avgLatencyMs: Math.round(avgLatency?.avg_ms || 0),
      workersMonitored: getAllWorkers().length,
    }, 200, origin);
  }

  // Manual trigger: health sweep
  if (path === '/trigger/health' && request.method === 'POST') {
    const { down, degraded, results } = await healthSweep(env);
    const partner = await checkPartner(env);
    return json({ total: results.length, down: down.length, degraded: degraded.length, partnerAlive: partner.alive }, 200, origin);
  }

  // Manual trigger: enhancement scan
  if (path === '/trigger/enhance' && request.method === 'POST') {
    const result = await enhancementScan(env);
    return json(result, 200, origin);
  }

  // Manual trigger: deep audit
  if (path === '/trigger/audit' && request.method === 'POST') {
    const result = await deepAudit(env);
    return json(result, 200, origin);
  }

  // Manual trigger: daily report
  if (path === '/trigger/report' && request.method === 'POST') {
    const report = await dailyReport(env);
    return json({ report }, 200, origin);
  }

  // Uptime for specific worker
  if (path.startsWith('/uptime/')) {
    const workerName = path.split('/uptime/')[1];
    const checks = await env.DB.prepare("SELECT status, COUNT(*) as cnt FROM health_checks WHERE worker_name = ? AND checked_at > datetime('now', '-24 hours') GROUP BY status").bind(workerName).all();
    const total = (checks.results as { cnt: number }[]).reduce((sum, r) => sum + r.cnt, 0);
    const healthy = (checks.results as { status: string; cnt: number }[]).find(r => r.status === 'healthy')?.cnt || 0;
    const uptime = total > 0 ? ((healthy / total) * 100).toFixed(2) : '0.00';
    return json({ worker: workerName, uptime24h: `${uptime}%`, checks: total, healthy }, 200, origin);
  }

  return json({ error: 'Not found', endpoints: ['/', '/health', '/fleet', '/incidents', '/enhancements', '/queue', '/creations', '/partner', '/stats', '/uptime/:worker', '/trigger/health', '/trigger/enhance', '/trigger/audit', '/trigger/report'] }, 404, origin);
}

// ═══════════════════════════════════════════════════════════════
// WORKER EXPORT
// ═══════════════════════════════════════════════════════════════
export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    return handleRequest(request, env);
  },

  async scheduled(event: ScheduledEvent, env: Env, ctx: ExecutionContext) {
    ctx.waitUntil(handleCron(event, env));
  },
};
