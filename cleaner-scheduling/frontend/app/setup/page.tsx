import { sessionRoleCookie, workspaceMode } from "../../lib/session";
import { cookies } from "next/headers";
import { SetupGuide } from "../../components/SetupGuide";
export default async function Page() {
 const role = (await cookies()).get(sessionRoleCookie)?.value;
 return <SetupGuide demo={process.env.LOCAL_DEMO === "1"} canWrite={role === "manager" || role === "owner"} sourceMode={workspaceMode} />;
}
