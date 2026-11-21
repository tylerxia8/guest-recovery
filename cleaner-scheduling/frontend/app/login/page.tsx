import { notFound } from "next/navigation";
import { workspaceSignupEnabled, workspaceRegistrationEnabled } from "../../lib/session";
import { WorkspaceSignup } from "../../components/WorkspaceSignup";

export default function Page() {
  if (!workspaceSignupEnabled) notFound();
  return <WorkspaceSignup login={true} authenticated={false} registrationEnabled={workspaceRegistrationEnabled} />;
}
