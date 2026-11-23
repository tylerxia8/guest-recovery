import { operationsAuthorization } from "../../lib/operations-login";
import { notFound } from "next/navigation";
import { workspaceSignupEnabled, workspaceRegistrationEnabled } from "../../lib/session";
import { WorkspaceSignup } from "../../components/WorkspaceSignup";

export default async function Page({searchParams}: {searchParams: Promise<Record<string, string | string[] | undefined>>}) {
  if (!workspaceSignupEnabled) notFound();
  const query = await searchParams;
  const params = new URLSearchParams();
  for (const key of ["client", "challenge", "state"]) {
    if (typeof query[key] === "string") params.set(key, query[key]);
  }
  const resumeOperations = operationsAuthorization(params) ? "/auth/operations/authorize?" + params : undefined;
  return <WorkspaceSignup resumeOperations={resumeOperations} login={true} authenticated={false} registrationEnabled={workspaceRegistrationEnabled} />;
}
