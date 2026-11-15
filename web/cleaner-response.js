const replyToken = location.hash.slice(1);
const status = document.querySelector('#response-status');
const requestPanel = document.querySelector('#response-request');
const arrival = document.querySelector('#response-arrival');
const available = document.querySelector('#response-available');
const unavailable = document.querySelector('#response-unavailable');
let pendingReply = null;
async function send(path, data, key) {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const response = await fetch('/api/cleaner-response/' + path, {method: 'POST', credentials: 'omit', headers: {'Content-Type': 'application/json', ...(key ? {'Idempotency-Key': key} : {})}, body: JSON.stringify({token: replyToken, ...data}), signal: AbortSignal.timeout(15000)});
      const result = await response.json();
      if (!response.ok) {
        if ((response.status === 429 || response.status >= 500) && attempt < 2) {
          await new Promise(resolve => setTimeout(resolve, 1000 * 2 ** attempt));
          continue;
        }
        const error = new Error(result.error || 'Unable to save your reply.');
        error.status = response.status;
        throw error;
      }
      return result;
    } catch (error) {
      if (attempt < 2 && (error.name === 'TimeoutError' || error.name === 'AbortError' || error instanceof TypeError)) {
        await new Promise(resolve => setTimeout(resolve, 1000 * 2 ** attempt));
        continue;
      }
      throw error;
    }
  }
}
async function reply(data) {
  if (pendingReply && JSON.stringify(pendingReply.data) !== JSON.stringify(data)) {
    status.textContent = 'The previous reply may have been saved. Retry that reply or contact the manager before changing it.';
    return;
  }
  pendingReply ||= {data, key: crypto.randomUUID()};
  available.disabled = true;
  unavailable.disabled = true;
  status.textContent = 'Saving your reply…';
  try {
    const result = await send('reply', pendingReply.data, pendingReply.key);
    requestPanel.hidden = true;
    status.textContent = result.status;
  } catch (error) {
    if (error.status >= 400 && error.status < 500 && error.status !== 429) pendingReply = null;
    status.textContent = error.message;
    available.disabled = false;
    unavailable.disabled = false;
  }
}
document.querySelector('#response-form').onsubmit = event => {
  event.preventDefault();
  reply({action: 'available', arrival: arrival.value, charge_cents: Math.round(Number(document.querySelector('#response-charge').value) * 100), items_confirmed: document.querySelector('#response-confirmed').checked});
};
unavailable.onclick = () => reply({action: 'unavailable'});
async function loadRequest() {
  try {
    const result = await send('context', {}, null);
    if (result.recorded) { status.textContent = result.status; return; }
    const format = new Intl.DateTimeFormat(undefined, {timeZone: result.timezone, weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short'});
    document.querySelector('#response-property').textContent = result.property;
    for (const item of result.items) {
      const row = document.createElement('li');
      row.textContent = item.label + (item.quantity ? ` · ${item.quantity} ${item.unit}` : '');
      document.querySelector('#response-items').append(row);
    }
    document.querySelector('#response-deadline').textContent = 'Needed by ' + format.format(new Date(result.needed_by)) + '. Reply by ' + format.format(new Date(result.reply_by)) + '.';
    const deadline = Date.parse(result.needed_by);
    const times = [];
    for (let time = Math.ceil((Date.now() + 60000) / 300000) * 300000; time < deadline; time += 900000) times.push(time);
    times.push(deadline);
    for (const time of times) {
      const option = document.createElement('option');
      option.value = new Date(time).toISOString();
      option.textContent = format.format(new Date(time));
      arrival.append(option);
    }
    status.textContent = 'Please check your stock before answering.';
    requestPanel.hidden = false;
  } catch (error) { status.textContent = error.message; }
}
loadRequest();
