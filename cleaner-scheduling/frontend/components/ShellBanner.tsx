import { shellNotice } from "../lib/runtime";

export function ShellBanner() {
  return <p className="shell-banner">{shellNotice}</p>;
}
