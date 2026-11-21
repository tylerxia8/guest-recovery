import { workspaceSignupEnabled, workspaceRegistrationEnabled } from "../lib/session";
import { WorkspaceSignup } from "../components/WorkspaceSignup";
import Link from "next/link";

export default function Home() {
  if (workspaceSignupEnabled) return <WorkspaceSignup login={true} authenticated={false} registrationEnabled={workspaceRegistrationEnabled} />;
  if (process.env.HOSPITABLE_READ_ONLY === "true") return <main><h1>Read-only Hospitable workspace</h1><p>Connect above to view imported houses and bookings. The app makes no changes to your Hospitable account. Scheduling and SMS are disabled.</p><Link href="/reservations">Review Hospitable imports</Link></main>;
  return <main>
    <div className="hero"><p className="eyebrow">A CALMER TURNOVER DAY</p><h1>Cleaning coordination</h1>
      <p className="hero-copy">Know what needs attention. Give every cleaner a feasible plan. Close the day with confidence.</p>
      <Link className="button" href="/setup">Set up your operation</Link>{" "}<Link className="button" href="/dashboard">Open operational dashboard →</Link>
    </div>
    <div className="card-grid tour">
      <section><span className="step">01 / CONNECT</span><h2>Use your Hospitable data</h2><p>Use one source for houses and reservations. Adjust cleaning times only where needed.</p><Link href="/reservations">Review Hospitable →</Link></section>
      <section><span className="step">02 / TEAM</span><h2>Set up your cleaners</h2><p>Record who can clean each house, their regular weekly hours, and their consent.</p><Link href="/cleaners">Set up cleaners →</Link></section>
      <section><span className="step">03 / SUPERVISE</span><h2>Let scheduling run</h2><p>Activate the demo once, then review the dashboard for uncovered or late work. Manual planning is available when you need to intervene.</p><Link href="/dashboard">View daily cleaning →</Link></section>
    </div>
    <section><h2>Your demo walkthrough</h2><p>Choose Connect to Hospitable above to open the manager dashboard with the simulated account. Review the connected source and your cleaners, then start demo automation on the dashboard. In the local demo, simulate a cleaner reply and see the dashboard update.</p><p>Cleaners and reservations are fictional; local property examples use public housing records. Recorded SMS never reaches a carrier.</p></section>
  </main>;
}
