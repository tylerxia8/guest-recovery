import type { Metadata } from "next";
import Link from "next/link";
import { CalendarCheck, MessageSquare, Wrench } from "lucide-react";
import { redirect } from "next/navigation";
import { operationsToolLinks } from "../../lib/operations-login";
import { workspaceSignupEnabled } from "../../lib/session";

export const metadata: Metadata = {title: "Workspace home · Lucena Systems"};

export default function WorkspaceHome() {
  if (!workspaceSignupEnabled) redirect("/");
  const tools = [
    {label: "Cleaner", href: "/dashboard", icon: CalendarCheck, detail: "Manage turnovers, cleaner availability, and schedules."},
    ...operationsToolLinks().map(tool => tool.label === "Guest Messages"
      ? {...tool, icon: MessageSquare, detail: "Review guest conversations and coordinate requests."}
      : {...tool, icon: Wrench, detail: "Review repairs, assign work, and track progress."}),
  ];

  return <main>
    <div className="hero"><h1>Workspace home</h1><p className="hero-copy">Manage your properties and guest operations.</p></div>
    <div className="card-grid tour">
      {tools.map(({label, href, icon: Icon, detail}) => <section key={label}>
        <Icon size={26} aria-hidden="true" />
        <h2>{label}</h2>
        <p>{detail}</p>
        {href.startsWith("/") ? <Link href={href} className="button">Open {label}</Link>
          : <a href={href} className="button">Open {label}</a>}
      </section>)}
    </div>
    <p><Link href="/action-inbox">Review the shared action inbox</Link></p>
  </main>;
}
