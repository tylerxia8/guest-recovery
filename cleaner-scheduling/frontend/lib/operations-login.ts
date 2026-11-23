const flowValue = /^[A-Za-z0-9_-]{43}$/;

const tools = [
  {client: "guest-recovery", label: "Guest Messages", callback: () => process.env.OPERATIONS_GUEST_RECOVERY_CALLBACK},
  {client: "maintenance", label: "Maintenance", callback: () => process.env.OPERATIONS_MAINTENANCE_CALLBACK},
] as const;

export type OperationsToolLink = {label: string; href: string};

function operationsCallback(value: string | undefined) {
  if (process.env.OPERATIONS_LOGIN_ENABLED !== "true" || !value) return null;
  try {
    const callback = new URL(value);
    if (callback.protocol !== "https:" || callback.username || callback.password || callback.search || callback.hash) return null;
    return callback;
  } catch {
    return null;
  }
}

export function operationsToolLinks(): OperationsToolLink[] {
  return tools.flatMap(tool => {
    const callback = operationsCallback(tool.callback());
    if (callback?.pathname !== "/auth/operations/callback") return [];
    return [{label: tool.label, href: new URL("/auth/operations/start", callback).href}];
  });
}

export function operationsAuthorization(parameters: URLSearchParams) {
  const client = parameters.get("client");
  const challenge = parameters.get("challenge");
  const state = parameters.get("state");
  if ((client !== "guest-recovery" && client !== "maintenance") || !challenge || !state || !flowValue.test(challenge) || !flowValue.test(state)) return null;
  const tool = tools.find(candidate => candidate.client === client);
  const callback = operationsCallback(tool?.callback());
  return callback ? {client, challenge, state, callback} : null;
}
