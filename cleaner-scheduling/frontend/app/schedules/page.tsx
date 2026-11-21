import { sessionRoleCookie, workspaceMode } from "../../lib/session";
import { cookies } from "next/headers";
import { SchedulePlanner } from "../../components/SchedulePlanner";
export default async function SchedulesPage() {
  const role = (await cookies()).get(sessionRoleCookie)?.value;
  return <SchedulePlanner demo={process.env.LOCAL_DEMO === "1"} canWrite={role === "manager" || role === "owner"} sourceMode={workspaceMode} canApprove={role === "owner"} />;
}
