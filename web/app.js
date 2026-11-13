let dataset = {issues: []};
let selected = null;
let loading = false;
const notice = document.querySelector('#notice');
function node(tag, text, className) {
  const element = document.createElement(tag);
  element.textContent = text;
  if (className) element.className = className;
  return element;
}
function date(value) {
  return new Date(value).toLocaleString([], {dateStyle: 'medium', timeStyle: 'short'});
}
function title(issue) {
  return issue.worksheet ? 'Missing hand towels' : {Supplies: 'Missing or needed supplies', Maintenance: 'Something needs fixing', Cleaning: 'A cleaning concern', Instructions: 'A guest needs directions', Safety: 'A possible safety concern'}[issue.category];
}
function render() {
  const search = document.querySelector('#search').value.trim().toLowerCase();
  const category = document.querySelector('#category').value;
  const visible = dataset.issues.filter(issue => (!category || issue.category === category) && (!search || `${issue.property} ${issue.category} ${issue.evidence.map(message => message.body).join(' ')}`.toLowerCase().includes(search)));
  if (!visible.some(issue => issue.id === selected)) selected = (visible.find(issue => issue.worksheet) || visible[0])?.id;
  document.querySelector('#result-count').textContent = String(visible.length);
  const requests = document.querySelector('#requests');
  requests.replaceChildren();
  for (const issue of visible) {
    const button = node('button', '', 'request');
    button.type = 'button';
    button.setAttribute('aria-pressed', String(issue.id === selected));
    button.append(node('span', issue.property, 'request-property'), node('strong', title(issue)), node('span', issue.evidence[issue.evidence.length - 1].body, 'request-preview'));
    if (issue.priority === 'Urgent') button.append(node('span', 'Urgent review', 'urgent'));
    button.onclick = () => { selected = issue.id; render(); };
    requests.append(button);
  }
  const container = document.querySelector('#issues');
  container.replaceChildren();
  const issue = visible.find(item => item.id === selected);
  if (!issue) { container.append(node('p', 'No requests match your filters.', 'empty')); return; }
  const card = node('article', '', 'detail-card');
  card.append(node('p', issue.property, 'eyebrow'), node('h2', title(issue)), node('p', `Original checkout: ${date(issue.check_out)} · Used as a live scenario`, 'help-text'));
  const quote = node('blockquote', issue.evidence[issue.evidence.length - 1].body);
  quote.append(node('cite', `Guest · ${date(issue.latest_at)}`));
  card.append(quote);
  if (issue.resolution_hint) card.append(node('p', 'The original conversation contains a possible resolution. For this exercise, assume the request is still open.', 'context-note'));
  const solution = node('div', '', 'saved-plan');
  solution.append(node('span', 'Suggested fix', 'eyebrow'));
  if (issue.worksheet) {
    solution.append(node('h3', 'Send hand towels. Add a small apology gift.'), node('p', 'Propose a pack of hand towels and optional chocolates up to $3, delivered together on the same day.'), node('p', 'Suggested spending cap: $35 total, including tax and delivery.', 'budget'), node('p', 'Before acting: confirm the property address, actual price, and delivery availability. If same-day delivery is unavailable, ask housekeeping to bring towels.', 'help-text'));
  } else {
    const fixes = {
      Supplies: ['Replace the missing item.', 'Use the guest’s message to identify the item, then propose a suitable replacement. Check existing stock first.', 'Keep the proposal within $35 total; confirm the actual price and arrival date.'],
      Maintenance: ['Have maintenance fix it.', 'Send the reported fault and property details to the maintenance team for assessment.', 'Get a quote before approving parts or repairs.'],
      Cleaning: ['Arrange a cleaning touch-up.', 'Ask housekeeping to address the reported area and check for related problems.', 'Confirm availability and any cleaning charge.'],
      Instructions: ['Give the guest the right instructions.', 'Find the property-specific answer and prepare a short, clear explanation.', 'Usually no purchase needed.'],
      Safety: ['Have the manager assess this immediately.', 'Check the reported hazard and arrange the appropriate emergency or licensed response.', 'Escalate before considering routine purchases.'],
    };
    const [heading, action, budget] = fixes[issue.category];
    solution.append(node('h3', heading), node('p', action), node('p', budget, 'help-text'));
  }
  solution.append(node('p', 'Proposal only. Nothing has been sent, booked, or purchased.', 'help-text'));
  card.append(solution);
  const history = node('details', '', 'conversation-details');
  history.append(node('summary', `Read the full conversation (${issue.messages.length} messages)`));
  const conversation = node('div', '', 'conversation');
  for (const message of issue.messages) {
    const item = node('div', '', `message ${message.sender_type === 'guest' ? 'guest' : 'host'}`);
    item.append(node('p', `${message.sender_type === 'guest' ? 'Guest' : 'Host / other'} · ${date(message.created_at)}`, 'message-meta'), node('p', message.body, 'message-body'));
    conversation.append(item);
  }
  history.append(conversation);
  card.append(history);
  if (issue.evidence.length > 1) {
    const evidence = document.createElement('details');
    evidence.append(node('summary', `Other messages about this issue (${issue.evidence.length - 1})`));
    for (const message of issue.evidence.slice(0, -1)) evidence.append(node('blockquote', message.body));
    card.append(evidence);
  }
  container.append(card);
}
async function load() {
  if (loading) return;
  loading = true;
  const refresh = document.querySelector('#refresh');
  refresh.disabled = true;
  notice.textContent = '';
  try {
    let response;
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        response = await fetch('/api/research', {signal: AbortSignal.timeout(15000)});
        if ((response.status === 429 || response.status >= 500) && attempt === 0) { await new Promise(resolve => setTimeout(resolve, 1000)); continue; }
        break;
      } catch (error) {
        if (attempt) throw error;
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
    }
    if (!response.ok) throw new Error('Could not load saved requests. Please try again.');
    const next = await response.json();
    const previousIssues = JSON.stringify(dataset.issues.map(({worksheet, ...issue}) => issue));
    const nextIssues = JSON.stringify(next.issues.map(({worksheet, ...issue}) => issue));
    const changed = previousIssues !== nextIssues || !document.querySelector('#issues').firstChild;
    dataset = next;
    const connection = dataset.integration;
    const connectionText = connection.status === 'syncing' ? 'Updating guest messages from Hospitable…' : connection.status === 'connected' ? `Hospitable connected · Updated ${date(connection.last_success)} · Checks every 5 minutes` : connection.status === 'error' ? connection.error : 'Starting Hospitable connection…';
    document.querySelector('#connection').textContent = connectionText;
    document.querySelector('#connection').classList.toggle('connection-error', connection.status === 'error');
    document.querySelector('#stats').textContent = `${dataset.property_count || 0} properties · ${dataset.reservation_count || 0} saved conversations · ${dataset.guest_message_count || 0} guest messages`;
    document.querySelector('#scope').textContent = dataset.scope || 'Waiting for the first import.';
    document.querySelector('#updated').textContent = dataset.synced_at ? `Last imported ${date(dataset.synced_at)}. ${dataset.nontext_message_count} messages without text were excluded in the latest update.` : '';
    if (changed) render();
  } catch (error) { notice.textContent = error.message; }
  finally { loading = false; refresh.disabled = false; }
}
for (const id of ['search', 'category']) document.querySelector(`#${id}`).addEventListener('input', render);
document.querySelector('#refresh').onclick = async () => {
  const button = document.querySelector('#refresh');
  button.disabled = true;
  const key = crypto.randomUUID();
  try {
    let response;
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        response = await fetch('/api/integration/sync', {method: 'POST', headers: {'Content-Type': 'application/json', 'Idempotency-Key': key}, body: '{}', signal: AbortSignal.timeout(15000)});
        if ((response.status === 429 || response.status >= 500) && !attempt) { await new Promise(resolve => setTimeout(resolve, 1000)); continue; }
        break;
      } catch (error) {
        if (attempt) throw error;
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
    }
    if (!response.ok) throw new Error('Could not request an update. Please try again.');
    const result = await response.json();
    await load();
    notice.textContent = result.status === 'syncing' ? 'An update is already in progress.' : 'Update requested. Allow up to a minute before it starts.';
  } catch (error) { notice.textContent = error.message; }
  finally { button.disabled = false; }
};
setInterval(() => { if (!document.hidden) load(); }, 15000);
load();
