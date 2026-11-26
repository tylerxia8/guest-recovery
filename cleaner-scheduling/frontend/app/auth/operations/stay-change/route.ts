import type { NextRequest } from "next/server";
import { postOperationsSchedule } from "../../../../lib/operations-schedule-proxy";

export function POST(request: NextRequest) {
  return postOperationsSchedule(request, "stay-change");
}
