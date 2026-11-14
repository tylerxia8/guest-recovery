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
const purchaseDrafts = new Map();
let renderedIssues = '';
let renderedPreferences = '';
let renderedSupplier = '';
async function submitAction(path, data) {
  const key = crypto.randomUUID();
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const response = await apiFetch(path, {method: 'POST', headers: {'Content-Type': 'application/json', 'Idempotency-Key': key}, body: JSON.stringify(data), signal: AbortSignal.timeout(15000)});
      if ((response.status === 429 || response.status >= 500) && !attempt) { await new Promise(resolve => setTimeout(resolve, 1000)); continue; }
      const result = await response.json();
      if (!response.ok) throw new Error(result.error);
      return result;
    } catch (error) {
      if (!attempt && ['TimeoutError', 'TypeError', 'AbortError'].includes(error.name)) { await new Promise(resolve => setTimeout(resolve, 1000)); continue; }
      throw error;
    }
  }
}
function purchasePanel(issue) {
  const section = node('section', '', 'purchase-panel');
  section.append(node('h3', 'Purchase notice & manager decision'));
  section.append(node('p', '$25 or less: email the manager, then wait 10 minutes for a possible veto. Above $25: get explicit approval. The total includes all items, gifts, tax, shipping, and fees.', 'help-text'));
  section.append(node('p', 'Simulation only · Amazon checkout is not connected. Manager decisions require sign-in.', 'delivery-status'));
  if (!dataset.purchase_policy.email_configured) section.append(node('p', 'Email setup incomplete. An administrator can configure sending under Email delivery settings. Notices and timers remain blocked until then.', 'context-note'));
  for (const request of issue.purchases) {
    const card = node('div', '', 'purchase-request');
    card.append(node('strong', request.summary), node('p', `$${(request.total_cents / 100).toFixed(2)} total · $${(request.gift_cents / 100).toFixed(2)} gift included`, 'help-text'), node('p', request.status, 'purchase-status'), node('p', request.email_evidence, 'help-text'));
    if (request.offer) {
      card.append(node('p', `${request.offer.location} · Arrival estimate: ${request.offer.arrival_date}, ${request.offer.delivery_window} (${request.offer.timezone})`, 'help-text'), node('p', `${request.offer.source} · Recorded ${date(request.offer.recorded_at)}`, 'help-text'));
      if (request.offer.stale) card.append(node('p', 'This arrival date has passed. Cancel and recreate the proposal with a current quote before proceeding.', 'warning'));
      for (const asin of request.offer.asins) {
        const link = node('a', `Amazon item ${asin} ↗`, 'amazon-link');
        link.href = 'https://www.amazon.com/dp/' + asin;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        card.append(link);
      }
    } else card.append(node('p', 'No address-specific delivery estimate recorded.', 'help-text'));
    if (request.eligible_at && request.decision !== 'vetoed') {
      const countdown = node('p', '', 'countdown');
      countdown.dataset.deadline = request.eligible_at;
      card.append(countdown);
    }
    if (request.error) card.append(node('p', request.error, 'warning'));
    if (request.decision !== 'vetoed') {
      const actions = node('div', '', 'purchase-actions');
      for (const [action, label] of [['veto', 'Veto / cancel'], ...(request.approval_required && request.decision !== 'approved' ? [['approve', 'Manager: approve this total']] : [])]) {
        const button = node('button', label, action === 'veto' ? 'secondary' : '');
        button.type = 'button';
        button.onclick = async () => {
          button.disabled = true;
          try { await submitAction(`/api/purchases/${request.id}/${action}`, {}); await load(); }
          catch (error) { notice.textContent = error.message; }
          finally { button.disabled = false; }
        };
        actions.append(button);
      }
      card.append(actions);
    }
    section.append(card);
  }
  if (issue.purchases.some(request => request.decision !== 'vetoed')) return section;
  const details = document.createElement('details');
  details.open = purchaseDrafts.has(issue.id);
  details.append(node('summary', 'Prepare a purchase notice'));
  const form = node('form', '', 'purchase-form');
  const picks = issue.item_types.filter(kind => kind !== 'tv_remote').map(kind => {
    const unitProducts = (issue.property_ids || []).length === 1 ? dataset.catalog.products.filter(product => product.property_id === issue.property_ids[0] && product.item_type === kind) : [];
    return unitProducts[0] || dataset.catalog.defaults.find(product => product.item_type === kind);
  }).filter(product => product?.asin);
  const draft = purchaseDrafts.get(issue.id) || {summary: picks.map(product => product.title).join('; '), asins: picks.map(product => product.asin).join(', '), location: 'Charlotte, NC', arrival_date: '', delivery_window: '', address_confirmed: false, total: '', gift: '0', all_in_confirmed: false};
  form.append(node('p', 'Charlotte, NC is the default. Change it when the property is elsewhere. Select the actual property address in Amazon, confirm compatibility, then copy its full price and arrival estimate below. This app does not fetch live offers.', 'help-text'));
  for (const [name, title, type] of [['summary', 'Proposed items (include any chocolates)', 'text'], ['asins', 'Amazon ASINs (comma-separated, including any gift)', 'text'], ['location', 'Delivery city / location', 'text'], ['arrival_date', 'Estimated arrival date at the property', 'date'], ['delivery_window', 'Arrival window as shown by Amazon (property local time)', 'text'], ['total', 'Full order total ($)', 'number'], ['gift', 'Gift cost already included in total ($)', 'number']]) {
    const label = node('label', title);
    const input = document.createElement('input');
    input.name = name;
    input.type = type;
    input.value = draft[name] || '';
    input.required = true;
    if (type === 'number') { input.min = name === 'total' ? '0.01' : '0'; input.max = name === 'gift' ? '3' : '10000'; input.step = '0.01'; }
    else input.maxLength = name === 'summary' ? 1000 : 200;
    label.append(input);
    form.append(label);
  }
  const timezoneLabel = node('label', 'Property time zone');
  const timezoneSelect = document.createElement('select');
  timezoneSelect.name = 'timezone';
  for (const [value, title] of [['America/New_York', 'Eastern · Charlotte'], ['America/Chicago', 'Central'], ['America/Denver', 'Mountain'], ['America/Phoenix', 'Arizona'], ['America/Los_Angeles', 'Pacific'], ['America/Anchorage', 'Alaska'], ['Pacific/Honolulu', 'Hawaii']]) {
    const option = node('option', title);
    option.value = value;
    timezoneSelect.append(option);
  }
  timezoneSelect.value = draft.timezone || 'America/New_York';
  timezoneLabel.append(timezoneSelect);
  form.append(timezoneLabel);
  const label = node('label', '', 'checkbox-label');
  const check = document.createElement('input');
  check.type = 'checkbox';
  check.name = 'all_in_confirmed';
  check.checked = draft.all_in_confirmed;
  check.required = true;
  label.append(check, node('span', 'This total includes all items, tax, shipping, and fees.'));
  const addressLabel = node('label', '', 'checkbox-label');
  const addressCheck = document.createElement('input');
  addressCheck.type = 'checkbox';
  addressCheck.name = 'address_confirmed';
  addressCheck.checked = draft.address_confirmed || false;
  addressCheck.required = true;
  addressLabel.append(addressCheck, node('span', 'I checked the items, quantities, compatibility, and arrival estimate for the actual property address on Amazon.'));
  const feedback = node('p', '', 'save-status');
  feedback.setAttribute('role', 'status');
  const submit = node('button', 'Save proposal & queue email notice');
  form.append(label, addressLabel, submit, feedback);
  form.oninput = event => {
    if (event.target !== check && event.target !== addressCheck) { check.checked = false; addressCheck.checked = false; }
    purchaseDrafts.set(issue.id, {...Object.fromEntries(new FormData(form)), all_in_confirmed: check.checked, address_confirmed: addressCheck.checked});
  };
  form.onsubmit = async event => {
    event.preventDefault();
    const data = {issue_id: issue.id, summary: form.elements.summary.value, total_cents: Math.round(Number(form.elements.total.value) * 100), gift_cents: Math.round(Number(form.elements.gift.value) * 100), all_in_confirmed: check.checked};
    data.offer = {location: form.elements.location.value, timezone: timezoneSelect.value, asins: form.elements.asins.value.split(',').map(value => value.trim().toUpperCase()).filter(Boolean), arrival_date: form.elements.arrival_date.value, delivery_window: form.elements.delivery_window.value, address_confirmed: addressCheck.checked};
    for (const control of form.elements) control.disabled = true;
    try {
      await submitAction('/api/purchases', data);
      purchaseDrafts.delete(issue.id);
      form.blur();
      document.activeElement?.blur();
      await load();
      notice.textContent = 'Proposal saved. The clock starts after a successful Gmail send or confirmed Resend delivery; uncertain sends stay blocked. No purchase will be made in simulation mode.';
    } catch (error) { feedback.textContent = error.message; }
    finally { for (const control of form.elements) control.disabled = false; }
  };
  details.append(form);
  section.append(details);
  return section;
}
function updateCountdowns() {
  for (const element of document.querySelectorAll('[data-deadline]')) {
    const seconds = Math.max(0, Math.ceil((new Date(element.dataset.deadline) - Date.now()) / 1000));
    element.textContent = seconds ? `Manager veto time remaining: ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}` : 'Veto window complete. Purchase remains blocked because checkout is not connected.';
  }
}
function render() {
  const search = document.querySelector('#search').value.trim().toLowerCase();
  const category = document.querySelector('#category').value;
  const visible = dataset.issues.filter(issue => (!category || issue.category === category) && (!search || `${issue.property} ${issue.category} ${issue.evidence.map(message => message.body).join(' ')}`.toLowerCase().includes(search)));
  if (!selected) {
    const requestedPurchase = new URLSearchParams(window.location.search).get('purchase');
    selected = visible.find(issue => issue.purchases.some(request => request.id === requestedPurchase))?.id || null;
  }
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
    solution.append(node('h3', 'Send hand towels. Add a small apology gift.'), node('p', 'Propose a pack of hand towels and optional chocolates up to $3, delivered together on the same day.'), node('p', '$25 or less: 10-minute manager veto window. Above $25: manager approval required.', 'budget'), node('p', 'Get the towels there as soon as possible. Compare Amazon arrival with housekeeping delivery; leave out the gift if it would slow down the towels.', 'help-text'));
  } else {
    const fixes = {
      Supplies: ['Replace the missing item.', 'Use the guest’s message to identify the item, then propose a suitable replacement. Check existing stock first.', '$25 or less uses the 10-minute veto policy; higher totals require manager approval.'],
      Maintenance: ['Have maintenance fix it.', 'Send the reported fault and property details to the maintenance team for assessment.', 'Get a quote before approving parts or repairs.'],
      Cleaning: ['Arrange a cleaning touch-up.', 'Ask housekeeping to address the reported area and check for related problems.', 'Confirm availability and any cleaning charge.'],
      Instructions: ['Give the guest the right instructions.', 'Find the property-specific answer and prepare a short, clear explanation.', 'Usually no purchase needed.'],
      Safety: ['Have the manager assess this immediately.', 'Check the reported hazard and arrange the appropriate emergency or licensed response.', 'Escalate before considering routine purchases.'],
    };
    const [heading, action, budget] = fixes[issue.category];
    solution.append(node('h3', heading), node('p', action), node('p', budget, 'help-text'));
  }
  if (issue.category === 'Supplies') {
    const cleaner = cleanerPanel(issue);
    if (cleaner) solution.append(cleaner);
    const shopping = node('section', '', 'shopping');
    shopping.append(node('h3', 'Find options on Amazon'));
    shopping.append(node('p', 'Delivery speed comes first', 'delivery-priority'), node('p', 'Choose the earliest verified arrival for the correct unit. Start with its preferred item, then use a compatible alternative if it can arrive sooner. Skip chocolates if they would delay the essential item.', 'help-text'));
    const catalog = dataset.catalog;
    const units = issue.property_ids || [];
    const matching = units.length === 1 ? catalog.products.filter(product => product.property_id === units[0] && issue.item_types.includes(product.item_type)) : [];
    if (units.length !== 1) shopping.append(node('p', 'This request is not mapped to exactly one rental unit. Confirm the unit before choosing a preferred product.', 'context-note'));
    for (const product of matching) {
      const preference = node('div', '', 'matched-product');
      const label = catalog.item_types.find(item => item.id === product.item_type).label;
      preference.append(node('strong', `Unit’s preferred ${label.toLowerCase()}${product.room ? ' · ' + product.room : ''}`), node('p', product.title));
      if (product.item_type === 'tv_remote') preference.append(node('p', `TV: ${product.tv_brand} · ${product.tv_model}. Confirm this is the affected TV and check the remote’s compatibility.`, 'help-text'));
      const link = node('a', 'Check preferred item on Amazon ↗', 'amazon-link');
      link.href = 'https://www.amazon.com/dp/' + product.asin;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      const use = node('button', 'Use in purchase proposal', 'secondary');
      use.type = 'button';
      use.onclick = () => {
        const previous = purchaseDrafts.get(issue.id);
        const summary = `${product.title} · ASIN ${product.asin}${product.item_type === 'tv_remote' ? ` · ${product.room}: ${product.tv_brand} ${product.tv_model}` : ''}`;
        purchaseDrafts.set(issue.id, {...previous, summary: previous?.summary ? previous.summary + '; ' + summary : summary, asins: previous?.asins ? previous.asins + ', ' + product.asin : product.asin, location: previous?.location || 'Charlotte, NC', arrival_date: '', delivery_window: '', address_confirmed: false, total: '', gift: previous?.gift || '0', all_in_confirmed: false});
        render();
        const form = document.querySelector('.purchase-form');
        if (form) { form.closest('details').open = true; form.elements.summary.focus(); }
      };
      use.disabled = issue.purchases.some(request => request.decision !== 'vetoed');
      preference.append(link, use);
      shopping.append(preference);
    }
    if (!matching.length && units.length === 1) shopping.append(node('p', 'No matching preference saved yet. Add it under “Preferred Amazon items by unit” above.', 'help-text'));
    const searches = issue.item_types.filter(kind => kind !== 'tv_remote' && !matching.some(product => product.item_type === kind)).map(kind => {
      const type = catalog.item_types.find(item => item.id === kind);
      const preferred = (catalog.defaults || []).find(product => product.item_type === kind);
      if (preferred) shopping.append(node('strong', 'Shared preference: ' + preferred.title), node('p', preferred.check, 'help-text'));
      return [preferred ? preferred.title : type.label, preferred ? preferred.title : type.search_query, preferred?.asin];
    });
    if (issue.item_types.includes('tv_remote')) {
      const remotes = matching.filter(product => product.item_type === 'tv_remote');
      if (!remotes.length) shopping.append(node('p', 'Add this unit’s TV brand and exact model before choosing a remote. A generic remote may not work.', 'context-note'));
      for (const remote of remotes) searches.push([`${remote.room} compatible remote`, `${remote.tv_brand} ${remote.tv_model} compatible replacement remote`]);
    }
    if (!searches.length && !matching.length) shopping.append(node('p', 'Identify the missing item in the guest message before choosing a replacement.', 'help-text'));
    const links = node('div', '', 'shopping-links');
    for (const [label, query, asin] of searches) {
      const link = node('a', `${asin ? 'View selected' : 'Find'} ${label} ↗`, 'amazon-link');
      link.href = asin ? 'https://www.amazon.com/dp/' + asin : 'https://www.amazon.com/s?' + new URLSearchParams({k: query});
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      links.append(link);
    }
    if (searches.length) {
      const gift = node('a', 'Optional chocolates, up to $3 ↗', 'amazon-link gift-link');
      gift.href = 'https://www.amazon.com/s?' + new URLSearchParams({k: 'small chocolate gift under 3 dollars'});
      gift.target = '_blank';
      gift.rel = 'noopener noreferrer';
      links.append(gift);
    }
    shopping.append(links, node('p', 'Price and Charlotte delivery unverified', 'delivery-status'), node('p', 'Selected listings identify the preferred product and variant. Search links are fallbacks. In Amazon, confirm the unit compatibility, seller, full price, and arrival date at the property address. Do not place an order yet.', 'help-text'));
    solution.append(shopping);
  }
  solution.append(node('p', 'No guest messages, bookings, or purchases. Manager notice emails can be sent when configured.', 'help-text'));
  card.append(solution);
  if (issue.category === 'Supplies' && dataset.purchase_policy) card.append(purchasePanel(issue));
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
  renderedIssues = JSON.stringify(dataset.issues.map(({worksheet, ...issue}) => issue));
  renderedPreferences = JSON.stringify(dataset.catalog);
  renderedSupplier = JSON.stringify(dataset.local_supplier);
  updateCountdowns();
}
async function load() {
  if (loading) return;
  loading = true;
  const refresh = document.querySelector('#refresh');
  refresh.disabled = true;
  notice.textContent = '';
  try {
    const response = await apiFetch('/api/research');
    if (!response.ok) throw new Error('Could not load saved requests. Please try again.');
    const next = await response.json();
    const previousIssues = renderedIssues;
    const nextIssues = JSON.stringify(next.issues.map(({worksheet, ...issue}) => issue));
    const changed = previousIssues !== nextIssues || renderedPreferences !== JSON.stringify(next.catalog) || renderedSupplier !== JSON.stringify(next.local_supplier) || !document.querySelector('#issues').firstChild;
    dataset = next;
    const connection = dataset.integration;
    const connectionText = connection.status === 'syncing' ? 'Updating guest messages from Hospitable…' : connection.status === 'connected' ? `Hospitable connected · Updated ${date(connection.last_success)} · Checks every 5 minutes` : connection.status === 'error' ? connection.error : 'Starting Hospitable connection…';
    document.querySelector('#connection').textContent = connectionText;
    document.querySelector('#connection').classList.toggle('connection-error', connection.status === 'error');
    document.querySelector('#stats').textContent = `${dataset.property_count || 0} properties · ${dataset.reservation_count || 0} saved conversations · ${dataset.guest_message_count || 0} guest messages`;
    document.querySelector('#scope').textContent = dataset.scope || 'Waiting for the first import.';
    document.querySelector('#updated').textContent = dataset.synced_at ? `Last imported ${date(dataset.synced_at)}. ${dataset.nontext_message_count} messages without text were excluded in the latest update.` : '';
    if (changed && !document.querySelector('#issues form:focus-within')) render();
    renderCatalog();
    renderSupplier();
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
        response = await apiFetch('/api/integration/sync', {method: 'POST', headers: {'Content-Type': 'application/json', 'Idempotency-Key': key}, body: '{}', signal: AbortSignal.timeout(15000)});
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
setInterval(updateCountdowns, 1000);
window.addEventListener('beforeunload', event => { if (purchaseDrafts.size) { event.preventDefault(); event.returnValue = ''; } });
load();
