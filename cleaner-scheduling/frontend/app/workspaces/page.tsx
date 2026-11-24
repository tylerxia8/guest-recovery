import { notFound } from "next/navigation";
import { WorkspacePicker } from "../../components/WorkspacePicker";
import { workspaceSignupEnabled } from "../../lib/session";

export default function Page() {
  if (!workspaceSignupEnabled) notFound();
  return <WorkspacePicker authenticated />;
}
