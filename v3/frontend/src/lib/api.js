// Calls the backend and returns parsed JSON; failures throw an Error whose message is the server's explanation.
async function request(method, path, body) {
  let res;
  try {
    res = await fetch(`/api${path}`, {
      method,
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new Error("Cannot reach the server. Is it running?");
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* no body */
  }
  if (!res.ok) {
    const detail = data && data.detail;
    const message = Array.isArray(detail)
      ? detail.map((d) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ")
      : detail || `Request failed (${res.status})`;
    throw new Error(message);
  }
  return data;
}

export const api = {
  health: () => request("GET", "/health"),

  listSecrets: () => request("GET", "/secrets"),
  createSecret: (body) => request("POST", "/secrets", body),
  updateSecret: (id, body) => request("PUT", `/secrets/${id}`, body),
  deleteSecret: (id) => request("DELETE", `/secrets/${id}`),
  devPrefill: (kind) => request("GET", `/dev-prefill${kind ? `?kind=${kind}` : ""}`),
  listEnvSecrets: () => request("GET", "/env-secrets"),
  importEnvSecret: (kind) => request("POST", `/env-secrets/${kind}/import`),

  listWorkflows: () => request("GET", "/workflows"),
  getWorkflow: (id) => request("GET", `/workflows/${id}`),
  createWorkflow: (body) => request("POST", "/workflows", body),
  updateWorkflow: (id, body) => request("PUT", `/workflows/${id}`, body),
  deleteWorkflow: (id) => request("DELETE", `/workflows/${id}`),
  resetCache: (id) => request("POST", `/workflows/${id}/reset-cache`),
  generatePrompt: (body) => request("POST", "/generate-prompt", body),
  previewPrompt: (body) => request("POST", "/preview-prompt", body),

  startRun: (workflowId, limit) => request("POST", `/workflows/${workflowId}/runs`, { limit: limit ?? null }),
  listRuns: () => request("GET", "/runs"),
  getRun: (id) => request("GET", `/runs/${id}`),
  runEvents: (id, after) => request("GET", `/runs/${id}/events?after=${after}`),
  abortRun: (id) => request("POST", `/runs/${id}/abort`),
};
