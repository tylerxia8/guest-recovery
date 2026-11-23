import type { NextRequest } from "next/server";
import { readOperationsStay } from "../../../../lib/operations-stay-proxy";

export function GET(request: NextRequest) {
  return readOperationsStay(request, "repair-context");
}
