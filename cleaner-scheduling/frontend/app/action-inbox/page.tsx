import { notFound } from "next/navigation";
import { ActionInbox } from "../../components/ActionInbox";
import { workspaceSignupEnabled } from "../../lib/session";

export default function Page() {
  if (!workspaceSignupEnabled) notFound();
  return <ActionInbox />;
}
