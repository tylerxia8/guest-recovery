import Link from "next/link";
import DemoEntry from "./demo-entry";
import styles from "./maintenance-shell.module.css";

export default function HomePage() {
  return <section className={styles.landing}>
    <h1>Lucena Repairs</h1>
    {process.env.NEXT_PUBLIC_DEMO_FIXTURE === "sample" ? <DemoEntry /> : <Link href="/login">Sign in to report or review a repair</Link>}
  </section>;
}
