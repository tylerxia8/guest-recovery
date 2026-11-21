import { notFound } from "next/navigation";
import { workspaceSignupEnabled } from "../../lib/session";
import { AccountSecurity } from "../../components/AccountSecurity";

export default function Page() {
  if (!workspaceSignupEnabled) notFound();
  return <AccountSecurity />;
}
