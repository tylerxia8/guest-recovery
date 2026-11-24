import { operationsOrigins } from "../../lib/operations-login";
import { LoginForm } from "./login-form";

export const dynamic = "force-dynamic";

export default function Page() {
  return <LoginForm sharedLogin={Boolean(operationsOrigins())} />;
}
