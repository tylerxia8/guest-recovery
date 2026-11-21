import { sessionRoleCookie, workspaceMode } from "../../lib/session";
import { cookies } from "next/headers";
import { PropertySetup } from "../../components/PropertySetup";
export default async function Page() {
  const role = (await cookies()).get(sessionRoleCookie)?.value;
  return <PropertySetup demo={process.env.LOCAL_DEMO === "1"} sourceMode={workspaceMode} canWrite={role === "manager" || role === "owner"} />;
}
