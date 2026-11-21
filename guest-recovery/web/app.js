let dataset = {issues: []};
let selected = null;
let loading = false;
let purchaseLinkSelected = false;
const selectedIssueByText = new Map();
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
function problemTypes(issue, message) {
  return issue.problem_types?.[message.id] || [issue.category];
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
      if (request.offer.fulfillment) {
        const address = request.offer.fulfillment.address;
        card.append(node('p', `Saved proposal destination: ${[address.line1, address.line2, `${address.city}, ${address.region} ${address.postal_code}`, address.country].filter(Boolean).join(' · ')}`, 'help-text'));
        if (request.offer.hand_towels_needed) card.append(node('p', `${request.offer.hand_towels_needed} individual towels needed · Confirm pack quantity before checkout`, 'help-text'));
      } else card.append(node('p', 'No confirmed unit delivery profile is attached to this proposal. Simulation only.', 'help-text'));
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
    const unitProducts = dataset.catalog.shared_products.filter(product => product.item_type === kind);
    return unitProducts[0] || dataset.catalog.defaults.find(product => product.item_type === kind);
  }).filter(product => product?.asin).filter((product, index, products) => products.findIndex(item => item.asin === product.asin) === index);
  const destination = issue.fulfillment;
  const draft = purchaseDrafts.get(issue.id) || {summary: picks.map(product => { const amount = issue.supply_quantities?.find(item => item.item_type === product.item_type); return product.title + (amount ? ` · Need ${amount.quantity} ${amount.unit}; check pack size` : ''); }).join('; '), asins: picks.map(product => product.asin).join(', '), location: destination?.verified ? `${destination.address.city}, ${destination.address.region}` : 'Charlotte, NC', timezone: destination?.verified ? destination.address.timezone : 'America/New_York', arrival_date: '', delivery_window: '', address_confirmed: false, total: '', gift: '0', all_in_confirmed: false, fulfillment_version: destination?.verified ? destination.version : null};
  const bound = draft.fulfillment_version !== null && draft.fulfillment_version !== undefined;
  form.append(node('p', 'Charlotte, NC is the default. Change it when the property is elsewhere. Select the actual property address in Amazon, confirm compatibility, then copy its full price and arrival estimate below. This app does not fetch live offers.', 'help-text'));
  for (const [name, title, type] of [['summary', 'Proposed items (include any chocolates)', 'text'], ['asins', 'Amazon ASINs (comma-separated, including any gift)', 'text'], ['location', 'Delivery city / location', 'text'], ['arrival_date', 'Estimated arrival date at the property', 'date'], ['delivery_window', 'Arrival window as shown by Amazon (property local time)', 'text'], ['total', 'Full order total ($)', 'number'], ['gift', 'Gift cost already included in total ($)', 'number']]) {
    const label = node('label', title);
    const input = document.createElement('input');
    input.name = name;
    input.type = type;
    input.value = draft[name] || '';
    input.required = true;
    if (name === 'location' && bound) input.readOnly = true;
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
  if (draft.timezone && ![...timezoneSelect.options].some(option => option.value === draft.timezone)) {
    const option = node('option', draft.timezone);
    option.value = draft.timezone;
    timezoneSelect.append(option);
  }
  timezoneSelect.value = draft.timezone || 'America/New_York';
  timezoneSelect.disabled = bound;
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
    purchaseDrafts.set(issue.id, {...Object.fromEntries(new FormData(form)), timezone: timezoneSelect.value, fulfillment_version: draft.fulfillment_version, purchase_plan: draft.purchase_plan, all_in_confirmed: check.checked, address_confirmed: addressCheck.checked});
  };
  form.onsubmit = async event => {
    event.preventDefault();
    const data = {issue_id: issue.id, summary: form.elements.summary.value, total_cents: Math.round(Number(form.elements.total.value) * 100), gift_cents: Math.round(Number(form.elements.gift.value) * 100), all_in_confirmed: check.checked};
    data.offer = {location: form.elements.location.value, timezone: timezoneSelect.value, asins: form.elements.asins.value.split(',').map(value => value.trim().toUpperCase()).filter(Boolean), arrival_date: form.elements.arrival_date.value, delivery_window: form.elements.delivery_window.value, address_confirmed: addressCheck.checked};
    if (draft.fulfillment_version !== null && draft.fulfillment_version !== undefined) data.fulfillment_version = draft.fulfillment_version;
    if (draft.purchase_plan) data.purchase_plan = draft.purchase_plan;
    for (const control of form.elements) control.disabled = true;
    try {
      await submitAction('/api/purchases', data);
      purchaseDrafts.delete(issue.id);
      form.blur();
      document.activeElement?.blur();
      await load();
      notice.textContent = 'Proposal saved. The clock starts after a successful Gmail send; uncertain sends stay blocked. No purchase will be made in simulation mode.';
    } catch (error) { feedback.textContent = error.message; }
    finally { for (const control of form.elements) control.disabled = false; timezoneSelect.disabled = bound; }
  };
  details.append(form);
  section.append(details);
  return section;
}
function updateCountdowns() {
  for (const element of document.querySelectorAll('[data-deadline]')) {
    const seconds = Math.max(0, Math.ceil((new Date(element.dataset.deadline) - Date.now()) / 1000));
    element.textContent = seconds ? `Time to deny: ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}` : 'Window ended · checkout not connected.';
  }
}
function showSettings() {
  document.querySelector('#settings-workspace').hidden = false;
  document.querySelector('#inbox-workspace').hidden = true;
  document.querySelector('#issue-summary').hidden = true;
  document.querySelector('#settings-toggle').textContent = 'Back to inbox';
  document.querySelector('#page-title').textContent = 'Settings';
  document.querySelector('#page-subtitle').textContent = 'Properties, maintenance, cleaner, and email.';
}
document.querySelector('#settings-toggle').onclick = () => {
  if (document.querySelector('#settings-workspace').hidden) { showSettings(); return; }
  document.querySelector('#settings-workspace').hidden = true;
  document.querySelector('#inbox-workspace').hidden = false;
  document.querySelector('#issue-summary').hidden = false;
  document.querySelector('#settings-toggle').textContent = 'Settings';
  document.querySelector('#page-title').textContent = 'Guest inbox';
  document.querySelector('#page-subtitle').textContent = 'Read the conversation. Review the solution.';
};
function render() {
  const search = document.querySelector('#search').value.trim().toLowerCase();
  const category = document.querySelector('#category').value;
  const view = document.querySelector('#request-view');
  const summary = document.querySelector('#issue-counts');
  const counts = new Map(['Check-in & checkout', 'Maintenance', 'Missing supplies', 'Safety', 'Cleaning', 'Other'].map(label => [label, 0]));
  const labels = {Maintenance: 'Maintenance', Supplies: 'Missing supplies', Safety: 'Safety', Cleaning: 'Cleaning'};
  let total = 0;
  for (const issue of dataset.issues) {
    if (view.value !== 'all' && issue.lane !== view.value) continue;
    const buckets = issue.category === 'Instructions'
      ? new Set(issue.evidence.flatMap(message => problemTypes(issue, message))
        .map(type => type === 'Check-in & checkout' ? type : 'Other'))
      : [labels[issue.category] || 'Other'];
    for (const bucket of buckets) {
      counts.set(bucket, counts.get(bucket) + 1);
      total++;
    }
  }
  const list = node('ul');
  for (const [label, count] of counts) list.append(node('li', `${label}: ${count} (${total ? (100 * count / total).toFixed(1) : '0.0'}%)`));
  summary.replaceChildren(node('p', `${total} detected issues in this view`), list);
  const entries = new Map();
  const priority = ['Safety', 'Maintenance', 'Cleaning', 'Supplies', 'Instructions'];
  for (const issue of dataset.issues) {
    for (const message of issue.evidence) {
      const key = issue.reservation_id + ':' + message.id;
      if (!entries.has(key)) entries.set(key, {key, message, issues: []});
      entries.get(key).issues.push(issue);
    }
  }
  const texts = [...entries.values()].sort((a, b) => b.message.created_at.localeCompare(a.message.created_at));
  for (const text of texts) text.issues.sort((a, b) => priority.indexOf(a.category) - priority.indexOf(b.category));
  if (!purchaseLinkSelected && dataset.purchase_policy) {
    purchaseLinkSelected = true;
    const requestedPurchase = new URLSearchParams(window.location.search).get('purchase');
    const target = texts.find(text => text.issues.some(issue => issue.purchases.some(request => request.id === requestedPurchase)));
    if (target) {
      selected = target.key;
      const targetIssue = target.issues.find(issue => issue.purchases.some(request => request.id === requestedPurchase));
      selectedIssueByText.set(target.key, targetIssue.id);
      view.value = target.issues[0].lane || 'practice';
    }
  }
  for (const [lane, label] of [['needs_attention', 'Needs attention'], ['likely_resolved', 'Likely resolved'], ['practice', 'Practice']]) view.querySelector(`option[value="${lane}"]`).textContent = `${label} (${texts.filter(text => (text.issues[0].lane || 'practice') === lane).length})`;
  view.querySelector('option[value="all"]').textContent = `All saved texts (${texts.length})`;
  const visible = texts.filter(text => (view.value === 'all' || (text.issues[0].lane || 'practice') === view.value) && (!category || text.issues.some(issue => problemTypes(issue, text.message).includes(category))) && (!search || `${text.issues[0].property} ${text.message.body}`.toLowerCase().includes(search)));
  if (!visible.some(text => text.key === selected)) selected = visible[0]?.key;
  document.querySelector('#result-count').textContent = String(visible.length);
  const requests = document.querySelector('#requests');
  requests.replaceChildren();
  for (const text of visible) {
    const issue = text.issues[0];
    const button = node('button', '', 'request');
    button.type = 'button';
    button.setAttribute('aria-pressed', String(text.key === selected));
    button.append(node('span', issue.property, 'request-property'), node('span', text.message.body, 'request-preview'), node('span', date(text.message.created_at), 'request-time'));
    if (issue.priority === 'Urgent') button.append(node('span', 'Urgent', 'urgent'));
    button.onclick = () => { selected = text.key; render(); if (window.matchMedia('(max-width:760px)').matches) document.querySelector('#issues').scrollIntoView({behavior: 'smooth', block: 'start'}); };
    requests.append(button);
  }
  const container = document.querySelector('#issues');
  container.replaceChildren();
  const text = visible.find(item => item.key === selected);
  if (!text) {
    container.append(node('p', 'No texts match these filters.', 'empty'));
    if (texts.length) {
      const showAll = node('button', 'Show all saved texts', 'secondary');
      showAll.type = 'button';
      showAll.onclick = () => { view.value = 'all'; document.querySelector('#category').value = ''; document.querySelector('#search').value = ''; render(); };
      container.append(showAll);
    }
    return;
  }
  const issue = (category ? text.issues.find(item => problemTypes(item, text.message).includes(category)) : text.issues.find(item => item.id === selectedIssueByText.get(text.key))) || text.issues[0];
  const card = node('article', '', 'detail-card');
  const back = node('button', '← Texts', 'mobile-back secondary');
  back.type = 'button';
  back.onclick = () => document.querySelector('aside').scrollIntoView({behavior: 'smooth', block: 'start'});
  card.append(back);
  card.append(node('p', issue.property, 'eyebrow'), node('p', issue.lane === 'practice' ? 'Practice · historical or unverified stay' : issue.stay_phase === 'upcoming' ? 'Upcoming arrival' : 'Current stay', 'help-text'));
  const quote = node('blockquote', text.message.body, 'guest-text');
  quote.append(node('cite', date(text.message.created_at)));
  card.append(quote);
  const nextStep = node('section', '', 'next-step');
  card.append(nextStep);
  const replyPanel = guestReplyPanel(issue, text.message);
  card.append(replyPanel);
  const propertyDetails = node('details', '', 'property-details');
  propertyDetails.append(node('summary', 'Property details'));
  card.append(propertyDetails);
  if (issue.supply_quantities?.length) {
    const amounts = issue.supply_quantities.map(item => `${dataset.catalog.item_types.find(kind => kind.id === item.item_type)?.label || item.item_type}: ${item.quantity} ${item.unit} · ${item.basis}`);
    propertyDetails.append(node('p', amounts.join(' / '), 'help-text'), node('p', 'Quantities are individual items, not Amazon packs.', 'help-text'));
  }
  let profileButton = null;
  const destination = issue.fulfillment;
  if (destination) {
    const delivery = node('section');
    delivery.append(node('h3', 'Delivery destination'), node('p', destination.verified ? (destination.source === 'hospitable' ? 'From Hospitable' : 'Manager override') : 'Delivery address incomplete', 'help-text'));
    if (destination.verified) {
      delivery.append(node('p', [destination.address.line1, destination.address.line2, `${destination.address.city}, ${destination.address.region} ${destination.address.postal_code}`, destination.address.country].filter(Boolean).join(' · ')));
      if (destination.instructions) delivery.append(node('p', destination.instructions));
    }

    const edit = node('button', destination.verified ? 'Edit delivery details' : 'Add missing delivery details', 'secondary');
    edit.type = 'button';
    edit.onclick = () => {
      showSettings();
      selectedUnit = destination.property_id;
      catalogSignature = '';
      renderCatalog();
      const panel = document.querySelector('#property-panel');
      panel.open = true;
      const profile = panel.querySelector('.fulfillment-form');
      if (profile) { profile.closest('details').open = true; profile.scrollIntoView({behavior: 'smooth', block: 'start'}); }
    };
    profileButton = edit;
    delivery.append(edit);
    propertyDetails.append(delivery);
  } else propertyDetails.append(node('p', 'This reservation does not map to exactly one unit. Confirm the property before choosing a delivery destination.', 'context-note'));
  if (issue.resolution_hint) {
    const confirmation = node('details', '', 'resolution-details');
    confirmation.append(node('summary', 'Guest reported a fix'));
    const resolution = node('blockquote', issue.resolution_hint.body);
    resolution.append(node('cite', `Guest confirmation · ${date(issue.resolution_hint.created_at)}`));
    confirmation.append(resolution);
    card.append(confirmation);
  }
  const solution = node('div', '', 'saved-plan');
  if (issue.category === 'Supplies') {
    solution.append(purchasePlanPanel(issue, text.message));
    const shopping = node('details', '', 'shopping');
    shopping.append(node('summary', 'More Amazon options'));
    shopping.append(node('p', 'Check the earliest arrival. Skip the gift if it delays the essentials.', 'help-text'));
    const catalog = dataset.catalog;
    const units = issue.property_ids || [];
    const matching = [...catalog.shared_products.filter(product => issue.item_types.includes(product.item_type)), ...(units.length === 1 ? catalog.products.filter(product => product.property_id === units[0] && product.item_type === 'tv_remote' && issue.item_types.includes(product.item_type)) : [])];
    if (units.length !== 1) shopping.append(node('p', 'This request is not mapped to exactly one rental unit. Confirm the unit before choosing a preferred product.', 'context-note'));
    for (const product of matching) {
      const preference = node('div', '', 'matched-product');
      const label = catalog.item_types.find(item => item.id === product.item_type).label;
      preference.append(node('strong', `${product.property_id === 'shared' ? 'Shared preferred' : 'Unit’s preferred'} ${label.toLowerCase()}${product.room ? ' · ' + product.room : ''}`), node('p', product.title));
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
        purchaseDrafts.set(issue.id, {...previous, summary: previous?.summary ? previous.summary + '; ' + summary : summary, asins: previous?.asins ? previous.asins + ', ' + product.asin : product.asin, location: previous?.location || (issue.fulfillment?.verified ? `${issue.fulfillment.address.city}, ${issue.fulfillment.address.region}` : 'Charlotte, NC'), timezone: previous?.timezone || (issue.fulfillment?.verified ? issue.fulfillment.address.timezone : 'America/New_York'), fulfillment_version: previous ? previous.fulfillment_version : (issue.fulfillment?.verified ? issue.fulfillment.version : null), arrival_date: '', delivery_window: '', address_confirmed: false, total: '', gift: previous?.gift || '0', all_in_confirmed: false});
        render();
        const form = document.querySelector('.purchase-form');
        if (form) { form.closest('.action-details').open = true; form.closest('details').open = true; form.elements.summary.focus(); }
      };
      use.disabled = issue.purchases.some(request => request.decision !== 'vetoed');
      preference.append(link, use);
      shopping.append(preference);
    }
    if (!matching.length && units.length === 1) shopping.append(node('p', 'Shared defaults apply; override them under “Delivery items” above.', 'help-text'));
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
    if (searches.length || matching.length) {
      const preferredGift = catalog.defaults.find(product => product.item_type === 'chocolates');
      const gift = node('a', preferredGift ? `Optional: ${preferredGift.title} · up to $3 ↗` : 'Optional chocolates, up to $3 ↗', 'amazon-link gift-link');
      gift.href = preferredGift?.asin ? 'https://www.amazon.com/dp/' + preferredGift.asin : 'https://www.amazon.com/s?' + new URLSearchParams({k: preferredGift?.title || 'small chocolate gift under 3 dollars'});
      gift.target = '_blank';
      gift.rel = 'noopener noreferrer';
      links.append(gift);
      if (preferredGift) links.append(node('p', preferredGift.check, 'help-text'));
    }
    shopping.append(links, node('p', 'Price and Charlotte delivery unverified', 'delivery-status'), node('p', 'Selected listings identify the preferred product and variant. Search links are fallbacks. In Amazon, confirm the unit compatibility, seller, full price, and arrival date at the property address. Do not place an order yet.', 'help-text'));
    solution.append(shopping);
    const cleaner = cleanerPanel(issue);
    if (cleaner) solution.append(cleaner);
  }

  const controls = node('details', '', 'action-details');
  controls.append(node('summary', 'Action details'), solution);
  if (issue.category === 'Supplies' && dataset.purchase_policy) controls.append(purchasePanel(issue));
  card.append(controls);
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
  let heading = 'Review this request';
  let detail = 'Check the conversation before responding.';
  let actionLabel = 'View conversation';
  let target = history;
  const pendingPurchase = issue.purchases.find(request => request.decision !== 'vetoed');
  const activeCleaner = issue.local_requests.find(request => ['pending', 'available'].includes(request.state) && !request.fallback);
  if (pendingPurchase) {
    heading = 'Purchase notice'; detail = pendingPurchase.status; actionLabel = 'Review purchase notice'; target = controls.querySelector('.purchase-panel');
  } else if (activeCleaner) {
    heading = 'Cleaner request'; detail = activeCleaner.status; actionLabel = 'View cleaner request'; target = controls.querySelector('.cleaner-option');
  } else if (issue.resolution_hint) {
    heading = 'Guest reported a fix'; detail = 'Review their confirmation before taking another action.'; actionLabel = 'Read confirmation'; target = card.querySelector('.resolution-details');
  } else if (issue.category === 'Supplies' && profileButton && !destination.verified) {
    heading = 'Complete the delivery address'; detail = 'Hospitable is missing delivery details for this unit.'; actionLabel = 'Add delivery details'; target = null;
  } else if (issue.category === 'Supplies' && issue.local_requests[0]?.fallback) {
    heading = 'Use Amazon instead'; detail = issue.local_requests[0].status; actionLabel = 'Review replacement'; target = controls.querySelector('.purchase-plan');

  } else if (issue.category === 'Supplies') {
    heading = 'Order a replacement from Amazon'; detail = 'Choose quality and earliest delivery, then cost. Checkout is not connected yet.'; actionLabel = 'Review purchase plan'; target = controls.querySelector('.purchase-plan');
  } else if (issue.category === 'Maintenance') {
    heading = /remote/i.test(text.message.body) ? 'Check the remote' : 'Review a repair'; detail = /remote/i.test(text.message.body) ? 'Start with the batteries. Repair dispatch needs a manager.' : 'Repair dispatch needs a manager.';
  } else if (issue.category === 'Cleaning') {
    heading = 'Arrange a cleaning touch-up'; detail = 'Cleaning dispatch needs a manager.';
  } else if (issue.category === 'Instructions') {
    heading = 'Find the right instructions'; detail = 'Review the conversation and prepare a reply for approval.';
  } else if (issue.category === 'Safety') {
    heading = 'Urgent manager review'; detail = 'Check the reported hazard now.';
  }
  nextStep.append(node('span', 'Next step', 'eyebrow'), node('h2', heading), node('p', detail));
  const action = node('button', actionLabel);
  action.type = 'button';
  action.onclick = () => {
    if (!target) { profileButton?.click(); return; }
    if (target === history) { thread.scrollIntoView({behavior: 'smooth', block: 'start'}); return; }
    advanced.open = true;
    if (controls.contains(target)) controls.open = true;
    if (target.tagName === 'DETAILS') target.open = true;
    if (target.classList.contains('cleaner-option')) {
      const form = target.querySelector('.local-request-form');
      if (form) {
        form.closest('details').open = true;
        const mode = form.querySelector('select');
        if (issue.cleaner_live?.eligible && mode) { mode.value = 'live'; mode.dispatchEvent(new Event('change')); }
      }
    }
    target.scrollIntoView({behavior: 'smooth', block: 'start'});
  };
  const next = node('button', 'Next text →', 'secondary');
  next.type = 'button';
  next.disabled = visible.length < 2;
  next.onclick = () => { selected = visible[(visible.findIndex(item => item.key === selected) + 1) % visible.length].key; render(); };
  const actions = node('div', '', 'primary-actions');
  const reply = node('button', 'Reply draft', 'secondary');
  reply.type = 'button';
  reply.onclick = () => { replyPanel.open = true; replyPanel.scrollIntoView({behavior: 'smooth', block: 'start'}); };
  actions.append(action, reply, next);
  nextStep.append(actions);
  if (text.issues.length > 1) {
    const alternatives = node('details');
    alternatives.append(node('summary', 'Other possible fixes'));
    for (const related of text.issues.filter(item => item.id !== issue.id)) {
      const button = node('button', title(related), 'secondary');
      button.type = 'button';
      button.onclick = () => { selectedIssueByText.set(text.key, related.id); document.querySelector('#category').value = ''; render(); };
      alternatives.append(button);
    }
    card.append(alternatives);
  }
  card.append(node('p', 'Follow up on safety alerts and review guest replies that need your input.', 'automation-note'));
  const advanced = node('details', '', 'manager-advanced');
  advanced.append(node('summary', 'More controls'));
  while (card.firstChild) advanced.append(card.firstChild);
  const managerHeading = node('div', '', 'manager-heading');
  managerHeading.append(back, node('h2', issue.property), next);
  const layout = node('div', '', 'manager-layout');
  const thread = node('section', '', 'manager-thread');
  thread.append(node('h3', 'Text chain'), conversation);
  const selectedMessage = conversation.children[issue.messages.findIndex(item => item.id === text.message.id)];
  if (selectedMessage) selectedMessage.classList.add('selected-message');
  const purchaseIssue = issue.category === 'Maintenance' ? issue : text.issues.find(item => item.category === 'Supplies') || issue;
  let purchaseControls = controls;
  if (purchaseIssue !== issue) {
    purchaseControls = node('details', '', 'action-details');
    purchaseControls.append(node('summary', 'Purchase setup'), purchasePlanPanel(purchaseIssue, text.message), purchasePanel(purchaseIssue));
    advanced.append(purchaseControls);
  }
  if (text.issues.some(item => item.category === 'Safety')) managerHeading.append(node('span', 'Urgent safety concern', 'urgent'));
  const safetyIssue = text.issues.find(item => item.category === 'Safety');
  const solutionPanel = safetyIssue ? managerSolutionCard(safetyIssue, text.message, controls) : issue.category === 'Cleaning' ? cleanerReturnPanel(issue, text.message) : issue.category === 'Instructions' ? propertyInstructionPanel(issue, text.message) : managerSolutionCard(purchaseIssue, text.message, purchaseControls);
  layout.append(thread, solutionPanel);
  history.remove();
  card.append(managerHeading, layout, advanced);
  container.append(card);
  if (selectedMessage) conversation.scrollTop = conversation.scrollTop + selectedMessage.getBoundingClientRect().top - conversation.getBoundingClientRect().top - 24;
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
    const changed = previousIssues !== nextIssues || renderedPreferences !== JSON.stringify(next.catalog) || renderedSupplier !== JSON.stringify(next.local_supplier) || JSON.stringify(dataset.cleaner_scheduler) !== JSON.stringify(next.cleaner_scheduler) || JSON.stringify(dataset.maintenance) !== JSON.stringify(next.maintenance) || !document.querySelector('#issues').firstChild;
    dataset = next;
    renderInternalScheduler(dataset.cleaner_scheduler);
    const maintenanceLink = document.querySelector('#maintenance-workspace-link');
    maintenanceLink.hidden = !dataset.maintenance?.dashboard_url;
    if (dataset.maintenance?.dashboard_url) maintenanceLink.href = dataset.maintenance.dashboard_url + '/maintenance/review';
    const connection = dataset.integration;
    const connectionText = connection.status === 'syncing' ? 'Syncing Hospitable…' : connection.status === 'connected' ? `Hospitable · Updated ${date(connection.last_success)}` : connection.status === 'error' ? connection.error : 'Connecting to Hospitable…';
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
for (const id of ['search', 'category', 'request-view']) document.querySelector(`#${id}`).addEventListener('input', render);
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
