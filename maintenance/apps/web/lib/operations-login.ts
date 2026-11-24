export const operationsFlowCookie = "__Host-maintenance-operations";
export const operationsValue = /^[A-Za-z0-9_-]{43}$/;

function httpsOrigin(value: string | undefined) {
  try {
    if (!value) return null;
    const url = new URL(value);
    return url.protocol === "https:" && value === url.origin ? value : null;
  } catch {
    return null;
  }
}

export function operationsOrigins() {
  const turnover = httpsOrigin(process.env.OPERATIONS_TURNOVER_ORIGIN);
  const maintenance = httpsOrigin(process.env.OPERATIONS_MAINTENANCE_ORIGIN);
  return turnover && maintenance ? { turnover, maintenance } : null;
}
