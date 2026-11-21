export async function readRequestBody(request: Request, limit: number): Promise<{body: string} | {response: Response}> {
  const length = request.headers.get("content-length");
  if (length && (!/^\d+$/.test(length) || !Number.isSafeInteger(Number(length)))) return {response: Response.json({error: {message: "Invalid request length."}}, {status: 400})};
  if (length && Number(length) > limit) {
    void request.body?.cancel().catch(() => {});
    return {response: Response.json({error: {message: "Request body is too large."}}, {status: 413})};
  }
  if (!request.body) return {body: ""};
  const reader = request.body.getReader();
  const decoder = new TextDecoder();
  let received = 0;
  let body = "";
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    void reader.cancel().catch(() => {});
  }, 10000);
  try {
    while (true) {
      const {done, value} = await reader.read();
      if (timedOut) return {response: Response.json({error: {message: "Request body timed out."}}, {status: 408})};
      if (done) return {body: body + decoder.decode()};
      received += value.byteLength;
      if (received > limit) {
        void reader.cancel().catch(() => {});
        return {response: Response.json({error: {message: "Request body is too large."}}, {status: 413})};
      }
      body += decoder.decode(value, {stream: true});
    }
  } catch {
    return {response: Response.json({error: {message: timedOut ? "Request body timed out." : "Could not read request body."}}, {status: timedOut ? 408 : 400})};
  } finally {
    clearTimeout(timer);
    reader.releaseLock();
  }
}
