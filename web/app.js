let dataset = {issues: []};
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
function render() {
  const search = document.querySelector('#search').value.trim().toLowerCase();
  const category = document.querySelector('#category').value;
  const status = document.querySelector('#status').value;
  const visible = dataset.issues.filter(issue => (!category || issue.category === category) && (!status || issue.status === status) && (!search || `${issue.property} ${issue.category} ${issue.evidence.map(message => message.body).join(' ')}`.toLowerCase().includes(search)));
  document.querySelector('#result-count').textContent = `${visible.length} of ${dataset.issues.length}`;
  const container = document.querySelector('#issues');
  container.replaceChildren();
  if (!visible.length) container.append(node('p', dataset.synced_at ? 'No candidates match these filters.' : 'No research snapshot yet. Run python3 hospitable.py research to import active guest conversations.', 'empty'));
  for (const issue of visible) {
    const fragment = document.querySelector('#issue').content.cloneNode(true);
    fragment.querySelector('.property').textContent = issue.property;
    fragment.querySelector('.category').textContent = issue.category;
    fragment.querySelector('.status').textContent = issue.priority === 'Urgent' ? 'Urgent manager review' : issue.status;
    fragment.querySelector('.dates').textContent = `Latest evidence ${date(issue.latest_at)} · Checkout ${date(issue.check_out)}`;
    for (const message of issue.evidence) {
      const block = node('blockquote', message.body);
      block.append(node('cite', `Guest · ${date(message.created_at)}`));
      fragment.querySelector('.evidence').append(block);
    }
    fragment.querySelector('.route').textContent = issue.route;
    fragment.querySelector('.recommendation').textContent = issue.recommendation;
    if (issue.resolution_hint) {
      const resolution = fragment.querySelector('.resolution');
      resolution.append(node('h3', 'Later guest message suggests a resolution'));
      resolution.append(node('blockquote', issue.resolution_hint.body));
      resolution.append(node('p', 'Confirm that this addresses the same item. No resolution has been verified automatically.'));
    }
    fragment.querySelector('.conversation-title').textContent = `Read conversation · ${issue.messages.length} text messages`;
    const conversation = fragment.querySelector('.conversation');
    for (const message of issue.messages) {
      const item = node('div', '', `message ${message.sender_type === 'guest' ? 'guest' : 'host'}`);
      item.append(node('p', `${message.sender_type === 'guest' ? 'Guest' : 'Host / other'} · ${date(message.created_at)}`, 'message-meta'));
      item.append(node('p', message.body, 'message-body'));
      conversation.append(item);
    }
    container.append(fragment);
  }
}
async function load() {
  const refresh = document.querySelector('#refresh');
  refresh.disabled = true;
  notice.textContent = '';
  try {
    const response = await fetch('/api/research', {signal: AbortSignal.timeout(15000)});
    if (!response.ok) throw new Error('Could not load saved research data.');
    dataset = await response.json();
    const stats = document.querySelector('#stats');
    stats.replaceChildren();
    const values = [[dataset.property_count || 0, 'Connected properties'], [dataset.reservation_count || 0, 'Active reservations'], [dataset.guest_message_count || 0, 'Guest text messages'], [dataset.issues.length, 'Issue candidates']];
    for (const [value, label] of values) {
      const card = node('div', '', 'stat');
      card.append(node('strong', String(value)), node('span', label));
      stats.append(card);
    }
    const categories = document.querySelector('#categories');
    categories.replaceChildren();
    for (const category of ['Supplies', 'Maintenance', 'Cleaning', 'Instructions', 'Safety']) {
      const count = dataset.issues.filter(issue => issue.category === category).length;
      const button = node('button', `${category} · ${count}`, 'category-button');
      button.type = 'button';
      button.onclick = () => { document.querySelector('#category').value = category; render(); };
      categories.append(button);
    }
    document.querySelector('#scope').textContent = dataset.scope || 'Waiting for the first Hospitable import.';
    document.querySelector('#updated').textContent = dataset.synced_at ? `Saved ${date(dataset.synced_at)} · ${dataset.nontext_message_count} messages without text excluded. Counts represent flagged category threads, not confirmed complaints. Refresh imports with python3 hospitable.py research.` : '';
    render();
  } catch (error) { notice.textContent = error.message; }
  finally { refresh.disabled = false; }
}
for (const id of ['search', 'category', 'status']) document.querySelector(`#${id}`).addEventListener('input', render);
document.querySelector('#refresh').onclick = load;
load();
