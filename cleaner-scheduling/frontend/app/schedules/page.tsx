import { sessionRoleCookie, workspaceMode } from "../../lib/session";
import { cookies } from "next/headers";
import { SchedulePlanner } from "../../components/SchedulePlanner";
export default async function SchedulesPage({ searchParams }: { searchParams: Promise<{ date?: string | string[] }> }) {
  const date = (await searchParams).date;
  const initialDate = typeof date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(date) && Number.isFinite(Date.parse(date)) ? date : undefined;
  const role = (await cookies()).get(sessionRoleCookie)?.value;
  return <SchedulePlanner initialDate={initialDate} demo={process.env.LOCAL_DEMO === "1"} canWrite={role === "manager" || role === "owner"} sourceMode={workspaceMode} canApprove={role === "owner"} />;
}
