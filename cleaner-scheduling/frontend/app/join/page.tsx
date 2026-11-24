import { notFound } from "next/navigation";
import { cookies } from "next/headers";
import { WorkspacePicker } from "../../components/WorkspacePicker";
import { sessionTokenCookie, workspaceSignupEnabled } from "../../lib/session";

export default async function Page() {
  if (!workspaceSignupEnabled) notFound();
  return <WorkspacePicker invitation authenticated={Boolean((await cookies()).get(sessionTokenCookie)?.value)} />;
}
