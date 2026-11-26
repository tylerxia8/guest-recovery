import type { NextRequest } from "next/server";
import { postOperationsCommand } from "../../../../lib/operations-schedule-proxy";

export function POST(request: NextRequest) {
  return postOperationsCommand(request, "cleaning-touchup");
}
