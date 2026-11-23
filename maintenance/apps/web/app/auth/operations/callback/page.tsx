import type { Metadata } from "next";
import { CompleteOperationsLogin } from "./complete";

export const metadata: Metadata = { title: "Sign in · Maintenance", referrer: "no-referrer" };

export default function Page() {
  return <CompleteOperationsLogin />;
}
