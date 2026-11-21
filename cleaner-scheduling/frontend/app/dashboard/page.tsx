import { sessionRoleCookie, workspaceSignupEnabled } from "../../lib/session";
import { cookies } from "next/headers";
import { OperationalDashboard } from "../../components/OperationalDashboard";
export default async function Page() {
 const role = (await cookies()).get(sessionRoleCookie)?.value;
 return <OperationalDashboard workspaceSignupEnabled={workspaceSignupEnabled} demo={process.env.LOCAL_DEMO === "1"} canWrite={process.env.HOSPITABLE_READ_ONLY !== "true" && (role === "manager" || role === "owner")} />;
}
