import { sessionRoleCookie } from "../../lib/session";
import { cookies } from "next/headers";
import { ExceptionQueue } from "../../components/ExceptionQueue";

export default async function ExceptionsPage() {
  const role = (await cookies()).get(sessionRoleCookie)?.value ?? "";
  const canWrite = process.env.HOSPITABLE_READ_ONLY !== "true" && (role === "manager" || role === "owner");
  return <ExceptionQueue canWrite={canWrite} />;
}
