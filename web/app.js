const versions = new Map();
const notice = document.querySelector('#notice');
async function request(path, data) {
  const headers = {};
  if (data) {
    headers['Content-Type'] = 'application/json';
    headers['Idempotency-Key'] = crypto.randomUUID();
  }
  const response = await fetch(path, {method: data ? 'POST' : 'GET', headers, body: data ? JSON.stringify(data) : undefined, signal: AbortSignal.timeout(15000)});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error);
  return result;
}
function field(form, label, name, type = 'text') {
  const wrapper = document.createElement('label');
  wrapper.textContent = label;
  const input = document.createElement('input');
  input.name = name;
  input.type = type;
  input.required = true;
  wrapper.append(input);
  form.append(wrapper);
  return input;
}
function button(parent, text, action) {
  const element = document.createElement('button');
  element.type = 'button';
  element.textContent = text;
  element.onclick = async () => {
    element.disabled = true;
    try { await action(); } catch (error) { notice.textContent = error.message; }
    finally { element.disabled = false; }
  };
  parent.append(element);
}
async function change(id, action, data = {}) {
  await request(`/api/incidents/${id}/${action}`, {...data, expected_updated_at: versions.get(id)});
  notice.textContent = 'Decision saved.';
  await load();
}
async function load() {
  const data = await request('/api/incidents');
  document.querySelector('#workspace').hidden = false;
  document.querySelector('#policy').textContent = `Every purchase needs approval. Incident limit: $${(data.incident_limit_cents / 100).toFixed(2)}, including delivery, tax, and an optional gift up to $3. Purchasing and guest replies are manual.`;
  const container = document.querySelector('#incidents');
  container.replaceChildren();
  if (!data.incidents.length) container.textContent = 'No guest requests yet. Add one below or import a Hospitable conversation.';
  versions.clear();
  for (const incident of data.incidents) {
    versions.set(incident.id, incident.updated_at);
    const fragment = document.querySelector('#incident').content.cloneNode(true);
    fragment.querySelector('.category').textContent = incident.category.replaceAll('_', ' ');
    fragment.querySelector('.status').textContent = incident.status;
    fragment.querySelector('.context').textContent = `Property ${incident.property_id} · Reservation ${incident.reservation_id}`;
    for (const message of incident.messages) {
      const quote = document.createElement('blockquote');
      quote.textContent = message;
      fragment.querySelector('.messages').append(quote);
    }
    if (incident.proposal) {
      const proposal = JSON.parse(incident.proposal);
      fragment.querySelector('.proposal').textContent = `${proposal.product_title} · $${(proposal.total_cents / 100).toFixed(2)} total · Delivery ${proposal.delivery_date}`;
    }
    if (incident.order_reference) fragment.querySelector('.order-reference').textContent = `Order: ${incident.order_reference}`;
    const actions = fragment.querySelector('.actions');
    if (incident.status === 'pending') {
      const form = document.createElement('form');
      field(form, 'Replacement product', 'product_title').maxLength = 300;
      const asin = field(form, 'Amazon ASIN', 'asin');
      asin.pattern = '[A-Z0-9]{10}';
      const total = field(form, 'All-in total (cents)', 'total_cents', 'number');
      total.min = 1;
      total.max = data.incident_limit_cents;
      const gift = field(form, 'Gift cost included in total (cents)', 'gift_cents', 'number');
      gift.value = 0;
      gift.min = 0;
      gift.max = 300;
      const timezone = field(form, 'Property timezone', 'property_timezone');
      timezone.value = Intl.DateTimeFormat().resolvedOptions().timeZone;
      field(form, 'Verified delivery date for all items', 'delivery_date', 'date');
      field(form, 'Guest checkout date', 'checkout_date', 'date');
      field(form, 'I verified an active reservation and its property address', 'reservation_verified', 'checkbox');
      field(form, 'I verified all items can arrive on the selected date', 'delivery_verified', 'checkbox');
      const submit = document.createElement('button');
      submit.textContent = 'Approve for manual purchase';
      form.append(submit);
      form.onsubmit = async event => {
        event.preventDefault();
        submit.disabled = true;
        const values = Object.fromEntries(new FormData(form));
        values.total_cents = Number(values.total_cents);
        values.gift_cents = Number(values.gift_cents);
        values.reservation_verified = form.elements.reservation_verified.checked;
        values.delivery_verified = form.elements.delivery_verified.checked;
        try { await change(incident.id, 'approve', values); } catch (error) { notice.textContent = error.message; }
        finally { submit.disabled = false; }
      };
      actions.append(form);
      button(actions, 'Reject request', () => change(incident.id, 'reject'));
    }
    if (incident.status === 'approved') {
      button(actions, 'Cancel approval', () => change(incident.id, 'reject'));
      const form = document.createElement('form');
      field(form, 'Amazon order confirmation reference', 'order_reference');
      const submit = document.createElement('button');
      submit.textContent = 'Record completed purchase';
      form.append(submit);
      form.onsubmit = async event => {
        event.preventDefault();
        submit.disabled = true;
        try { await change(incident.id, 'order', Object.fromEntries(new FormData(form))); } catch (error) { notice.textContent = error.message; }
        finally { submit.disabled = false; }
      };
      actions.append(form);
    }
    if (incident.status === 'ordered_review') {
      const warning = document.createElement('p');
      warning.textContent = 'New guest message after purchase. Review the conversation and address any outstanding issue before resolving.';
      actions.append(warning);
    }
    if (['ordered', 'ordered_review'].includes(incident.status)) button(actions, 'Confirm guest received items', () => change(incident.id, 'resolve'));
    for (const event of incident.audit) {
      const item = document.createElement('li');
      item.textContent = `${new Date(event.at).toLocaleString()} · ${event.action.replaceAll('_', ' ')}`;
      fragment.querySelector('.history').append(item);
    }
    container.append(fragment);
  }
}
document.querySelector('#refresh').onclick = () => load().catch(error => { notice.textContent = error.message; });
document.querySelector('#intake').onsubmit = async event => {
  event.preventDefault();
  const form = event.target;
  const submit = form.querySelector('button');
  submit.disabled = true;
  try {
    await request('/api/intake', {...Object.fromEntries(new FormData(form)), sender_type: 'guest'});
    form.reset();
    await load();
    notice.textContent = 'Guest request received.';
  } catch (error) { notice.textContent = error.message; }
  finally { submit.disabled = false; }
};

load().catch(error => { notice.textContent = error.message; });
