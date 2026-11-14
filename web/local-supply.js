let supplierSignature = '';
let supplierDraft = null;
const localDrafts = new Map();
function renderSupplier() {
  if (!dataset.local_supplier || !dataset.catalog) return;
  const container = document.querySelector('#local-supplier-editor');
  const signature = JSON.stringify([dataset.local_supplier, dataset.catalog.properties]);
  if (container.querySelector('form')?.contains(document.activeElement) || (signature === supplierSignature && container.firstChild)) return;
  supplierSignature = signature;
  container.replaceChildren();
  const base = supplierDraft || dataset.local_supplier;
  const form = node('form', '', 'supplier-form');
  const emailLabel = node('label', 'Cleaner email');
  const email = document.createElement('input');
  email.name = 'email';
  email.type = 'email';
  email.maxLength = 254;
  email.value = base.email;
  emailLabel.append(email);
  form.append(emailLabel, node('h4', 'Potential supplies — ask about actual stock each time'));
  for (const kind of dataset.local_supply_types) {
    const label = node('label', '', 'checkbox-label');
    const check = document.createElement('input');
    check.type = 'checkbox';
    check.name = 'item_types';
    check.value = kind;
    check.checked = base.item_types.includes(kind);
    label.append(check, node('span', dataset.catalog.item_types.find(item => item.id === kind).label));
    form.append(label);
  }
  form.append(node('h4', 'Units the manager has confirmed the cleaner can serve'));
  const searchLabel = node('label', 'Find a unit');
  const search = document.createElement('input');
  search.type = 'search';
  search.placeholder = 'Filter by property name';
  searchLabel.append(search);
  form.append(searchLabel);
  const coverage = node('div', '', 'coverage-list');
  for (const unit of dataset.catalog.properties) {
    const duplicate = dataset.catalog.properties.some(other => other.id !== unit.id && other.name === unit.name);
    const label = node('label', '', 'checkbox-label');
    label.dataset.name = unit.name.toLowerCase();
    const check = document.createElement('input');
    check.type = 'checkbox';
    check.name = 'property_ids';
    check.value = unit.id;
    check.checked = base.property_ids.includes(unit.id);
    label.append(check, node('span', duplicate ? `${unit.name} · ${unit.id.slice(0, 8)}` : unit.name));
    coverage.append(label);
  }
  search.oninput = () => { for (const label of coverage.children) label.hidden = !label.dataset.name.includes(search.value.trim().toLowerCase()); };
  form.append(coverage, node('p', 'Leave coverage empty until confirmed. No Hiddenbrook residence or nearby unit is inferred from its name.', 'help-text'));
  const collect = () => ({email: email.value.trim(), item_types: [...form.querySelectorAll('[name=item_types]:checked')].map(input => input.value), property_ids: [...coverage.querySelectorAll('input:checked')].map(input => input.value), version: base.version});
  form.oninput = event => { if (event.target !== search) supplierDraft = collect(); };
  const save = node('button', 'Save cleaner details');
  const discard = node('button', 'Discard unsaved changes', 'secondary');
  discard.type = 'button';
  discard.onclick = () => { supplierDraft = null; document.activeElement?.blur(); supplierSignature = ''; renderSupplier(); };
  const feedback = node('p', '', 'save-status');
  feedback.setAttribute('role', 'status');
  form.append(save, discard, feedback);
  form.onsubmit = async event => {
    event.preventDefault();
    const data = collect();
    for (const control of form.elements) control.disabled = true;
    try {
      await submitAction('/api/local-supplier', data);
      supplierDraft = null;
      supplierSignature = '';
      document.activeElement?.blur();
      await load();
      notice.textContent = 'Cleaner details saved. Stock and daily availability remain unconfirmed.';
    } catch (error) { feedback.textContent = error.message; }
    finally { for (const control of form.elements) control.disabled = false; }
  };
  container.append(form);
}
function cleanerPanel(issue) {
  const supplier = dataset.local_supplier;
  const types = issue.item_types.filter(kind => supplier.item_types.includes(kind));
  if (!types.length && !issue.local_requests.length) return null;
  const section = node('section', '', 'cleaner-option');
  section.append(node('h3', 'Try the Hiddenbrook cleaner'), node('p', 'Ask about cleaning supplies or toiletries before relying on them. Availability changes by day. Keep Amazon open if it can arrive sooner. Times below use your device’s local time zone.', 'help-text'));
  for (const request of issue.local_requests) {
    const card = node('div', '', 'purchase-request');
    card.append(node('strong', request.status), node('p', request.details.item_types.map(kind => dataset.catalog.item_types.find(item => item.id === kind).label).join(', '), 'help-text'), node('p', `Reply by ${date(request.details.reply_by)} · Needed by ${date(request.details.needed_by)}`, 'help-text'));
    if (request.details.arrival) card.append(node('p', `Cleaner-reported arrival: ${date(request.details.arrival)}. Confirm any fee before arranging delivery.`, 'help-text'));
    if (request.fallback) card.append(node('p', 'Continue with the Amazon options below. No purchase or cleaner dispatch happens automatically.', 'delivery-priority'));
    if (request.fallback) {
      const amazon = node('button', 'View Amazon replacements', 'secondary');
      amazon.type = 'button';
      amazon.onclick = () => document.querySelector('.shopping')?.scrollIntoView({behavior: 'smooth', block: 'start'});
      card.append(amazon);
    }
    if (request.details.received_at) card.append(node('p', `Receipt recorded ${date(request.details.received_at)}. No payment or dispatch was made by this app.`, 'help-text'));
    if (['pending', 'available'].includes(request.state) && !request.details.received_at) {
      const actions = node('div', '', 'purchase-actions');
      for (const [action, label] of [['cancel', 'Cancel this check'], ['unavailable', 'Record: cleaner cannot help']]) {
        const button = node('button', label, 'secondary');
        button.type = 'button';
        button.onclick = async () => {
          button.disabled = true;
          try { await submitAction(`/api/local-requests/${request.id}/${action}`, {version: request.version}); await load(); }
          catch (error) { notice.textContent = error.message; }
          finally { button.disabled = false; }
        };
        actions.append(button);
      }
      card.append(actions);
      if (request.state === 'available') {
        const receipt = document.createElement('form');
        const label = node('label', '', 'checkbox-label');
        const confirmed = document.createElement('input');
        confirmed.type = 'checkbox';
        confirmed.required = true;
        label.append(confirmed, node('span', 'All requested items have actually been received at the property.'));
        const save = node('button', 'Record items received');
        const feedback = node('p', '', 'save-status');
        feedback.setAttribute('role', 'status');
        receipt.append(label, save, feedback);
        receipt.onsubmit = async event => {
          event.preventDefault();
          save.disabled = true;
          try {
            await submitAction(`/api/local-requests/${request.id}/received`, {version: request.version, items_received: confirmed.checked});
            document.activeElement?.blur();
            await load();
          }
          catch (error) { feedback.textContent = error.message; }
          finally { save.disabled = false; }
        };
        card.append(receipt);
      }
      if (request.state === 'pending' && request.notice_status === 'sent' && !request.fallback) {
        const reply = document.createElement('details');
        reply.append(node('summary', 'Record a reply received by the manager'));
        const form = document.createElement('form');
        const label = node('label', 'Cleaner-confirmed arrival time (your local time)');
        const arrival = document.createElement('input');
        arrival.type = 'datetime-local';
        arrival.required = true;
        label.append(arrival);
        const confirmed = node('label', '', 'checkbox-label');
        const check = document.createElement('input');
        check.type = 'checkbox';
        check.required = true;
        confirmed.append(check, node('span', 'The cleaner confirmed they have all the requested items.'));
        const save = node('button', 'Record availability');
        const feedback = node('p', '', 'save-status');
        feedback.setAttribute('role', 'status');
        form.append(label, confirmed, save, feedback);
        form.onsubmit = async event => {
          event.preventDefault();
          save.disabled = true;
          try {
            await submitAction(`/api/local-requests/${request.id}/available`, {version: request.version, arrival: new Date(arrival.value).toISOString().replace('Z', '+00:00'), items_confirmed: check.checked});
            document.activeElement?.blur();
            await load();
          } catch (error) { feedback.textContent = error.message; }
          finally { save.disabled = false; }
        };
        reply.append(form);
        card.append(reply);
      }
    }
    section.append(card);
  }
  if (issue.local_requests.some(request => ['pending', 'available'].includes(request.state))) return section;
  const eligible = issue.property_ids.length === 1 && supplier.property_ids.includes(issue.property_ids[0]) && supplier.email && types.length;
  if (!eligible) { section.append(node('p', 'Confirm the cleaner’s email and this unit’s coverage in “Hiddenbrook cleaner” above before contacting them.', 'context-note')); return section; }
  const details = document.createElement('details');
  details.open = localDrafts.has(issue.id);
  details.append(node('summary', 'Ask whether the cleaner can help'));
  const form = node('form', '', 'local-request-form');
  const draft = localDrafts.get(issue.id) || {needed: '', minutes: '10'};
  const neededLabel = node('label', `Need these items by (${Intl.DateTimeFormat().resolvedOptions().timeZone})`);
  const needed = document.createElement('input');
  needed.type = 'datetime-local';
  needed.value = draft.needed;
  needed.required = true;
  neededLabel.append(needed);
  const waitLabel = node('label', 'How long should we wait for confirmation before using Amazon?');
  const minutes = document.createElement('select');
  for (const count of [5, 10, 15, 30]) { const option = node('option', `${count} minutes`); option.value = count; minutes.append(option); }
  minutes.value = draft.minutes;
  waitLabel.append(minutes);
  const send = node('button', 'Queue an availability email');
  const feedback = node('p', '', 'save-status');
  feedback.setAttribute('role', 'status');
  form.append(node('p', `Ask about: ${types.map(kind => dataset.catalog.item_types.find(item => item.id === kind).label).join(', ')}. This is an availability inquiry, not a delivery instruction. Replies must be recorded by the manager.`, 'help-text'), neededLabel, waitLabel, send, feedback);
  form.oninput = () => localDrafts.set(issue.id, {needed: needed.value, minutes: minutes.value});
  form.onsubmit = async event => {
    event.preventDefault();
    send.disabled = true;
    try {
      await submitAction('/api/local-requests', {issue_id: issue.id, needed_by: new Date(needed.value).toISOString().replace('Z', '+00:00'), reply_minutes: Number(minutes.value)});
      localDrafts.delete(issue.id);
      document.activeElement?.blur();
      await load();
      notice.textContent = 'Cleaner availability check queued. Amazon remains available; nothing has been dispatched or purchased.';
    } catch (error) { feedback.textContent = error.message; }
    finally { send.disabled = false; }
  };
  details.append(form);
  if (!dataset.purchase_policy.email_configured) section.append(node('p', 'Email sending is not configured. The cleaner cannot be contacted yet; do not wait instead of choosing a faster option.', 'context-note'));
  section.append(details);
  return section;
}
window.addEventListener('beforeunload', event => { if (supplierDraft || localDrafts.size) { event.preventDefault(); event.returnValue = ''; } });
