import { sessionRoleCookie, workspaceMode } from "../../lib/session";
import { cookies } from "next/headers";
import { CleanerSetup } from "../../components/CleanerSetup";
export default async function Page() {
  const role = (await cookies()).get(sessionRoleCookie)?.value;
  return <CleanerSetup canWrite={role === "manager" || role === "owner"} sourceMode={workspaceMode} configureEmail={role === "owner"} />;
}
