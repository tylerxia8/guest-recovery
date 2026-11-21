import { sessionRoleCookie } from "../../lib/session";
import { cookies } from "next/headers";
import { ReservationSource } from "../../components/ReservationSource";
export default async function Page() {
 const role = (await cookies()).get(sessionRoleCookie)?.value;
 return <ReservationSource demo={process.env.LOCAL_DEMO === "1"} canWrite={role === "manager" || role === "owner"} />;
}
