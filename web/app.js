let dataset = {issues: []};
let selected = null;
const drafts = new Map();
const steps = new Map();
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
function worksheet(issue) {
  const plan = issue.worksheet;
  const draft = drafts.get(issue.id);
  const inputs = {...(draft ? draft.inputs : plan.inputs)};
  const base = draft || plan;
  const section = node('section', '', 'worksheet');
  const form = document.createElement('form');
  const feedback = node('p', '', 'save-status');
  feedback.setAttribute('role', 'status');
  let saving = false;
  function update(key, value) {
    inputs[key] = value;
    drafts.set(issue.id, {inputs: {...inputs}, version: base.version, source_fingerprint: base.source_fingerprint});
    feedback.textContent = 'Changes haven’t been saved yet.';
  }
  function choice(name, options) {
    const group = node('div', '', 'choice-group');
    group.setAttribute('role', 'group');
    for (const [value, text, hint] of options) {
      const button = node('button', '', 'choice');
      button.type = 'button';
      button.setAttribute('aria-pressed', String(inputs[name] === value));
      button.append(node('strong', text));
      if (hint) button.append(node('span', hint));
      button.onclick = () => { update(name, value); draw(); form.querySelector('[aria-pressed="true"]')?.focus({preventScroll: true}); };
      group.append(button);
    }
    form.append(group);
  }
  function field(name, text, type = 'text', maximum = 500) {
    const label = node('label', text);
    const input = document.createElement('input');
    input.name = name;
    input.type = type;
    input.maxLength = maximum;
    const money = ['total_cents', 'gift_cents'].includes(name);
    if (type === 'datetime-local') {
      if (inputs[name]) {
        const time = new Date(inputs[name]);
        input.value = new Date(time.getTime() - time.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
      }
    } else input.value = money ? (inputs[name] / 100).toFixed(2) : inputs[name];
    const limits = {quantity: [1, 20], total_cents: [0, 35], gift_cents: [0, 3], minimum_stock: [1, 100]};
    if (limits[name]) { input.min = limits[name][0]; input.max = limits[name][1]; input.step = money ? '0.01' : '1'; }
    input.oninput = () => {
      let value = input.value;
      if (type === 'number') value = money ? Math.round(Number(value) * 100) : Number(value);
      if (type === 'datetime-local') {
        if (value && Number.isNaN(new Date(value).getTime())) return;
        value = value ? new Date(value).toISOString().replace('Z', '+00:00') : '';
      }
      update(name, value);
    };
    label.append(input);
    form.append(label);
  }
  function draw() {
    form.replaceChildren();
    const route = ['need'];
    if (inputs.guest_need === 'open') route.push('offering');
    if (inputs.guest_need === 'open' && inputs.provided === 'yes') route.push('stock');
    if (route.includes('stock') && inputs.stock === 'unavailable') route.push('housekeeping');
    if (route.includes('housekeeping') && inputs.housekeeping === 'unavailable') route.push('purchase');
    route.push('review');
    let index = Math.min(steps.get(issue.id) || 0, route.length - 1);
    steps.set(issue.id, index);
    const step = route[index];
    const names = {need: 'Check the request', offering: 'Check the property', stock: 'Look for spares', housekeeping: 'Check delivery help', purchase: 'Plan a replacement', review: 'Review your plan'};
    const progress = node('div', '', 'progress');
    progress.append(node('span', `Step ${index + 1} of ${route.length}`), node('span', names[step]));
    form.append(progress);
    if (step === 'need') {
      form.append(node('h3', 'Does the guest still need hand towels?'), node('p', 'Read the conversation below. Check for later messages, an existing order, or a manager who already handled it.'));
      choice('guest_need', [['open', 'Yes, they still need them'], ['resolved', 'It’s already taken care of'], ['unknown', 'I need to check']]);
    }
    if (step === 'offering') {
      form.append(node('h3', 'Should this property have hand towels?'), node('p', 'Confirm what’s normally provided before arranging a replacement.'));
      choice('provided', [['yes', 'Yes, hand towels are included'], ['no', 'No, this needs a manager’s decision'], ['unknown', 'I’m not sure yet']]);
    }
    if (step === 'stock') {
      form.append(node('h3', 'Are there spare hand towels onsite?'), node('p', 'Using supplies already at the property is usually the simplest fix.'));
      choice('stock', [['accessible', 'Yes, the guest can access them'], ['unavailable', 'No accessible spares'], ['unknown', 'I need to ask someone']]);
      if (inputs.stock === 'accessible') field('stock_location', 'Where are they, and how can the guest get them?');
    }
    if (step === 'housekeeping') {
      form.append(node('h3', 'Can housekeeping bring hand towels?'), node('p', 'Check their availability before proposing an Amazon order.'));
      choice('housekeeping', [['available', 'Yes, they can deliver'], ['unavailable', 'No, we need another option'], ['unknown', 'I haven’t checked yet']]);
      if (inputs.housekeeping === 'available') field('housekeeping_arrival', `When can they arrive? (${Intl.DateTimeFormat().resolvedOptions().timeZone})`, 'datetime-local');
    }
    if (step === 'purchase') {
      form.append(node('h3', 'Prepare a replacement option'), node('p', 'Choose an approved product. Keep the complete order at $35 or less, including tax and delivery. This does not place an order.'));
      field('product_title', 'Product name', 'text', 300);
      field('asin', 'Amazon product code (ASIN)', 'text', 10);
      field('quantity', 'Number of packs', 'number');
      field('total_cents', 'Full order total ($)', 'number');
      field('gift_cents', 'Optional chocolates included in that total ($, up to 3)', 'number');
      field('delivery_date', 'Date all items can arrive', 'date');
      field('property_timezone', 'Property timezone, e.g. America/New_York', 'text', 100);
      form.append(node('h4', 'Check the delivery details'));
      const delivery = node('div', '', 'delivery-checks');
      for (const [name, text] of [['address_verified', 'I checked that the address is this property.'], ['delivery_verified', 'I checked that the towels and any chocolates can arrive on that date.']]) {
        const label = node('label', '', 'checkbox-label');
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = inputs[name] === 'yes';
        checkbox.onchange = () => update(name, checkbox.checked ? 'yes' : 'unknown');
        label.append(checkbox, node('span', text));
        delivery.append(label);
      }
      form.append(delivery);
    }
    if (step === 'review') {
      form.append(node('h3', 'Here’s what you’ve recorded'));
      const outcomes = {unknown: 'Still needs checking', open: 'Guest still needs hand towels', resolved: 'Already taken care of', yes: 'Yes', no: 'No', accessible: 'Spares are accessible onsite', unavailable: 'Not available', available: 'Can deliver'};
      const summary = node('dl', '', 'summary-list');
      for (const [key, label] of [['guest_need', 'Guest need'], ['provided', 'Included at property'], ['stock', 'Onsite spares'], ['housekeeping', 'Housekeeping']]) {
        if ((key === 'provided' && !route.includes('offering')) || (key === 'stock' && !route.includes('stock')) || (key === 'housekeeping' && !route.includes('housekeeping'))) continue;
        summary.append(node('dt', label), node('dd', outcomes[inputs[key]]));
      }
      if (route.includes('stock') && inputs.stock === 'accessible') summary.append(node('dt', 'Where to find spares'), node('dd', inputs.stock_location || 'Location still needed'));
      if (route.includes('housekeeping') && inputs.housekeeping === 'available') summary.append(node('dt', 'Housekeeping arrival'), node('dd', inputs.housekeeping_arrival ? date(inputs.housekeeping_arrival) : 'Arrival time still needed'));
      if (route.includes('purchase')) summary.append(node('dt', 'Replacement option'), node('dd', inputs.product_title || 'Product still needed'), node('dt', 'Order total'), node('dd', `$${(inputs.total_cents / 100).toFixed(2)}`));
      if (route.includes('purchase')) summary.append(node('dt', 'Delivery date'), node('dd', inputs.delivery_date || 'Date still needed'));
      form.append(summary);
      field('notes', 'Anything else the manager should know?', 'text', 2000);
      const prevention = node('details', '', 'prevention-settings');
      prevention.append(node('summary', 'Help prevent this next time'));
      field('minimum_stock', 'Suggested minimum hand towels to keep onsite', 'number');
      prevention.append(form.lastElementChild);
      form.append(prevention, node('p', 'Save to see the recommended next step and anything still missing. You can save an incomplete plan and come back later.', 'help-text'));
    }
    const actions = node('div', '', 'wizard-actions');
    if (index) {
      const back = node('button', 'Back', 'secondary');
      back.type = 'button';
      back.onclick = () => { steps.set(issue.id, index - 1); draw(); form.querySelector('h3').focus(); };
      actions.append(back);
    }
    if (step !== 'review') {
      const next = node('button', 'Continue');
      next.type = 'button';
      next.onclick = () => { steps.set(issue.id, index + 1); draw(); form.querySelector('h3').focus(); };
      actions.append(next);
    } else actions.append(node('button', 'Save plan'));
    form.append(actions, feedback);
    form.querySelector('h3').tabIndex = -1;
  }
  const staleDraft = draft && (draft.version !== plan.version || draft.source_fingerprint !== plan.source_fingerprint);
  if (plan.stale || staleDraft) section.append(node('p', 'This conversation or plan has changed. Review the latest information before saving.', 'warning'));
  if (draft) {
    const discard = node('button', 'Discard unsaved changes', 'text-button');
    discard.type = 'button';
    discard.onclick = () => { drafts.delete(issue.id); steps.delete(issue.id); render(); };
    section.append(discard);
  }
  draw();
  form.onsubmit = async event => {
    event.preventDefault();
    if (saving) return;
    saving = true;
    const submitted = {...inputs};
    const body = JSON.stringify({issue_id: issue.id, version: base.version, source_fingerprint: base.source_fingerprint, inputs: submitted});
    for (const control of form.elements) control.disabled = true;
    const key = crypto.randomUUID();
    try {
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          const response = await fetch('/api/worksheets', {method: 'POST', headers: {'Content-Type': 'application/json', 'Idempotency-Key': key}, body, signal: AbortSignal.timeout(15000)});
          if ((response.status === 429 || response.status >= 500) && attempt === 0) { await new Promise(resolve => setTimeout(resolve, 1000)); continue; }
          const result = await response.json();
          if (!response.ok) throw new Error(result.error);
          if (!drafts.has(issue.id) || JSON.stringify(drafts.get(issue.id).inputs) === JSON.stringify(submitted)) drafts.delete(issue.id);
          await load();
          notice.textContent = 'Plan saved. No message sent and nothing ordered.';
          return;
        } catch (error) {
          if (attempt === 0 && ['TimeoutError', 'TypeError', 'AbortError'].includes(error.name)) { await new Promise(resolve => setTimeout(resolve, 1000)); continue; }
          throw error;
        }
      }
    } catch (error) { feedback.textContent = error.message; }
    finally { saving = false; for (const control of form.elements) control.disabled = false; }
  };
  section.append(form);
  if (plan.saved_at) {
    const saved = node('div', '', 'saved-plan');
    saved.append(node('span', 'Saved recommendation', 'eyebrow'), node('h3', plan.route), node('p', plan.proposal));
    if (plan.blockers.length) {
      const details = document.createElement('details');
      details.append(node('summary', `${plan.blockers.length} things still need checking`));
      const list = document.createElement('ul');
      for (const blocker of plan.blockers) list.append(node('li', blocker));
      details.append(list);
      saved.append(details);
    }
    saved.append(node('p', `Saved ${date(plan.saved_at)}`, 'help-text'));
    section.append(saved);
  }
  return section;
}
window.addEventListener('beforeunload', event => { if (drafts.size) { event.preventDefault(); event.returnValue = ''; } });
function render() {
  const search = document.querySelector('#search').value.trim().toLowerCase();
  const category = document.querySelector('#category').value;
  const status = document.querySelector('#status').value;
  const visible = dataset.issues.filter(issue => (!category || issue.category === category) && (!status || issue.status === status) && (!search || `${issue.property} ${issue.category} ${issue.evidence.map(message => message.body).join(' ')}`.toLowerCase().includes(search)));
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
  card.append(node('p', issue.property, 'eyebrow'), node('h2', title(issue)), node('p', `Guest checkout: ${date(issue.check_out)}`, 'help-text'));
  const quote = node('blockquote', issue.evidence[issue.evidence.length - 1].body);
  quote.append(node('cite', `Guest · ${date(issue.latest_at)}`));
  card.append(quote);
  if (issue.resolution_hint) card.append(node('p', 'A later message suggests this may already be resolved. Check the conversation first.', 'context-note'));
  if (issue.worksheet) card.append(worksheet(issue));
  else {
    const remedy = node('div', '', 'saved-plan');
    remedy.append(node('h3', 'Suggested next step'), node('p', issue.recommendation));
    card.append(remedy);
  }
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
    dataset = await response.json();
    document.querySelector('#stats').textContent = `${dataset.property_count || 0} properties · ${dataset.reservation_count || 0} active reservations · ${dataset.guest_message_count || 0} guest messages`;
    document.querySelector('#scope').textContent = dataset.scope || 'Waiting for the first import.';
    document.querySelector('#updated').textContent = dataset.synced_at ? `Last imported ${date(dataset.synced_at)}. ${dataset.nontext_message_count} messages without text were excluded.` : '';
    render();
  } catch (error) { notice.textContent = error.message; }
  finally { refresh.disabled = false; }
}
for (const id of ['search', 'category', 'status']) document.querySelector(`#${id}`).addEventListener('input', render);
document.querySelector('#refresh').onclick = load;
load();
