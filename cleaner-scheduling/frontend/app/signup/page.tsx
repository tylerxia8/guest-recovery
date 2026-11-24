import { notFound } from "next/navigation";
import { cookies } from "next/headers";
import { workspaceSignupEnabled, workspaceRegistrationEnabled, workspaceInviteSignupEnabled, sessionTokenCookie } from "../../lib/session";
import { WorkspaceSignup } from "../../components/WorkspaceSignup";

export default async function Page() {
  if (!workspaceSignupEnabled) notFound();
  const authenticated = Boolean((await cookies()).get(sessionTokenCookie)?.value);
  return <WorkspaceSignup login={false} authenticated={authenticated} registrationEnabled={workspaceRegistrationEnabled} inviteSignupEnabled={workspaceInviteSignupEnabled} internalConnection={process.env.WORKSPACE_INTERNAL_PAT_ENABLED === "true" || process.env.WORKSPACE_PILOT_PAT_ENABLED === "true"} />;
}
