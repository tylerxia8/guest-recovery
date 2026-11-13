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
const drafts = new Map();
function worksheet(issue) {
  const plan = issue.worksheet;
  const section = node('section', '', 'worksheet');
  section.append(node('h3', 'Hand-towel resolution worksheet'));
  section.append(node('p', 'Record verified findings. Saving updates this local plan only. It does not authorize fulfillment.'));
  const checks = node('ul', '', 'checks');
  for (const check of plan.checks) checks.append(node('li', `${check.status.replaceAll('_', ' ')} · ${check.label}`, check.status));
  section.append(checks);
  if (plan.stale) section.append(node('p', 'Conversation changed since the last review. Recheck the evidence before saving.', 'warning'));
  const form = document.createElement('form');
  const draft = drafts.get(issue.id);
  const values = draft ? draft.inputs : plan.inputs;
  if (draft && (draft.version !== plan.version || draft.source_fingerprint !== plan.source_fingerprint)) section.append(node('p', 'This unsaved draft is based on older findings or evidence. Copy any notes you need, then discard the draft and review the current conversation.', 'warning'));
  if (draft) {
    const discard = node('button', 'Discard unsaved draft');
    discard.type = 'button';
    discard.onclick = () => { drafts.delete(issue.id); render(); };
    section.append(discard);
  }
  const choices = [
    ['guest_need', 'Guest need after reviewing the conversation', [['unknown', 'Unknown'], ['open', 'Still needs hand towels'], ['resolved', 'Already resolved']]],
    ['provided', 'Are hand towels included at this property?', [['unknown', 'Unknown'], ['yes', 'Yes'], ['no', 'No — manager decision needed']]],
    ['stock', 'Verified spare stock', [['unknown', 'Unknown'], ['accessible', 'Accessible onsite'], ['unavailable', 'No accessible spares']]],
    ['housekeeping', 'Housekeeping delivery', [['unknown', 'Not checked'], ['available', 'Available — enter confirmed arrival'], ['unavailable', 'Unavailable']]],
    ['address_verified', 'Delivery address matches this property', [['unknown', 'Not checked'], ['yes', 'Verified'], ['no', 'Does not match']]],
    ['delivery_verified', 'All items can arrive together on the recorded date', [['unknown', 'Not checked'], ['yes', 'Verified'], ['no', 'Cannot confirm']]],
  ];
  const headings = {'guest_need': '1 · Confirm the request', 'stock': '2 · Check onsite stock', 'housekeeping': '3 · Check housekeeping', 'address_verified': '5 · Verify the purchase proposal'};
  for (const [name, title, options] of choices) {
    if (headings[name]) form.append(node('h4', headings[name]));
    const label = node('label', title);
    const select = document.createElement('select');
    select.name = name;
    for (const [value, text] of options) {
      const option = node('option', text);
      option.value = value;
      select.append(option);
    }
    select.value = values[name];
    label.append(select);
    form.append(label);
    if (name === 'stock') field('stock_location', 'Verified location and access instructions', 'text', 500);
    if (name === 'housekeeping') {
      field('housekeeping_arrival', 'Confirmed arrival with offset, e.g. 2026-11-14T14:00:00-05:00', 'text', 40);
      form.append(node('h4', '4 · Optional replacement proposal'));
      field('product_title', 'Manager-approved product title', 'text', 300);
      field('asin', 'Amazon ASIN', 'text', 10);
      field('quantity', 'Number of product units or packs', 'number');
      field('total_cents', 'All-in proposal total in cents · $35 maximum', 'number');
      field('gift_cents', 'Optional gift cost included in total · $3 maximum', 'number');
      field('delivery_date', 'Verified arrival date for towels and any gift', 'date');
      field('property_timezone', 'Property timezone, e.g. America/New_York', 'text', 100);
    }
  }
  function field(name, title, type, maximum) {
    const label = node('label', title);
    const input = document.createElement('input');
    input.name = name;
    input.type = type;
    input.value = values[name];
    if (maximum) input.maxLength = maximum;
    const limits = {quantity: [1, 20], total_cents: [0, 3500], gift_cents: [0, 300], minimum_stock: [1, 100]};
    if (limits[name]) { input.min = limits[name][0]; input.max = limits[name][1]; input.step = 1; }
    label.append(input);
    form.append(label);
  }
  form.append(node('h4', '6 · Prevent recurrence'));
  field('minimum_stock', 'Proposed minimum hand-towel stock', 'number');
  field('notes', 'Reviewer notes and evidence for these findings', 'text', 2000);
  const feedback = node('p', '', 'save-status');
  feedback.setAttribute('role', 'status');
  const collect = () => {
    const data = Object.fromEntries(new FormData(form));
    for (const key of ['quantity', 'total_cents', 'gift_cents', 'minimum_stock']) data[key] = Number(data[key]);
    return data;
  };
  form.addEventListener('input', () => { const base = drafts.get(issue.id) || plan; drafts.set(issue.id, {inputs: collect(), version: base.version, source_fingerprint: base.source_fingerprint}); feedback.textContent = 'Unsaved findings. Save to update the proposal below.'; });
  const submit = node('button', 'Save findings and prepare proposal');
  form.append(submit, feedback);
  form.onsubmit = async event => {
    event.preventDefault();
    submit.disabled = true;
    const base = drafts.get(issue.id) || plan;
    const inputs = collect();
    const body = JSON.stringify({issue_id: issue.id, version: base.version, source_fingerprint: base.source_fingerprint, inputs});
    for (const control of form.elements) control.disabled = true;
    const key = crypto.randomUUID();
    try {
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          const response = await fetch('/api/worksheets', {method: 'POST', headers: {'Content-Type': 'application/json', 'Idempotency-Key': key}, body, signal: AbortSignal.timeout(15000)});
          if ((response.status === 429 || response.status >= 500) && attempt === 0) { await new Promise(resolve => setTimeout(resolve, 1000)); continue; }
          const result = await response.json();
          if (!response.ok) throw new Error(result.error);
          if (!drafts.has(issue.id) || JSON.stringify(drafts.get(issue.id).inputs) === JSON.stringify(inputs)) drafts.delete(issue.id);
          await load();
          notice.textContent = 'Worksheet saved. Proposal only — no reply sent and no purchase made.';
          return;
        } catch (error) {
          if (attempt === 0 && ['TimeoutError', 'TypeError', 'AbortError'].includes(error.name)) { await new Promise(resolve => setTimeout(resolve, 1000)); continue; }
          throw error;
        }
      }
    } catch (error) { feedback.textContent = error.message; }
    finally { for (const control of form.elements) control.disabled = false; }
  };
  section.append(form);
  const result = node('div', '', 'remedy');
  result.append(node('p', `${plan.readiness} · ${plan.route}`, 'eyebrow'), node('p', plan.proposal));
  if (plan.blockers.length) {
    result.append(node('h4', 'Still needs checking'));
    const blockers = document.createElement('ul');
    for (const blocker of plan.blockers) blockers.append(node('li', blocker));
    result.append(blockers);
  }
  result.append(node('p', plan.prevention));
  result.append(node('p', plan.saved_at ? `Findings saved ${date(plan.saved_at)}` : 'No reviewer findings saved yet.'));
  section.append(result);
  return section;
}
window.addEventListener('beforeunload', event => { if (drafts.size) { event.preventDefault(); event.returnValue = ''; } });
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
    if (issue.worksheet) fragment.querySelector('.worksheet-slot').append(worksheet(issue));
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
