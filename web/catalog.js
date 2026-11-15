let selectedUnit = null;
let selectedProduct = null;
let catalogSignature = '';
const preferenceDrafts = new Map();
const fulfillmentDrafts = new Map();
function renderCatalog() {
  if (!dataset.catalog) return;
  const container = document.querySelector('#catalog-editor');
  const catalog = dataset.catalog;
  const signature = JSON.stringify(catalog);
  if (container.querySelector('form:focus-within')) return;
  if (signature === catalogSignature && container.firstChild) return;
  catalogSignature = signature;
  container.replaceChildren();
  const defaults = node('details');
  defaults.append(node('summary', 'Shared preferred items · Charlotte'));
  defaults.append(node('p', 'Choose suitable quality and earliest confirmed arrival, then compare full cost. Unit-specific products take priority. Sizes, prices, and delivery still require confirmation on Amazon.', 'help-text'));
  for (const product of catalog.defaults || []) {
    const row = node('div', '', 'preferred-row');
    const link = node('a', product.title + ' ↗', 'amazon-link');
    link.href = product.asin ? 'https://www.amazon.com/dp/' + product.asin : 'https://www.amazon.com/s?' + new URLSearchParams({k: product.title});
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    row.append(link, node('p', product.check, 'help-text'));
    if (product.asin) row.append(node('p', 'Selected listing: ' + product.asin + ' · Price and Charlotte delivery unverified', 'help-text'));
    if (product.source_url) {
      const source = node('a', 'Product identification source ↗', 'help-text');
      source.href = product.source_url;
      source.target = '_blank';
      source.rel = 'noopener noreferrer';
      row.append(source);
    }
    defaults.append(row);
  }
  container.append(defaults);
  if (!catalog.properties.length) { container.append(node('p', 'Units will appear after the next successful Hospitable import.')); return; }
  if (!catalog.properties.some(unit => unit.id === selectedUnit)) selectedUnit = catalog.properties[0].id;
  const unitLabel = node('label', 'Rental unit');
  const unitSelect = document.createElement('select');
  for (const unit of catalog.properties) {
    const duplicateName = catalog.properties.some(other => other.id !== unit.id && other.name === unit.name);
    const option = node('option', duplicateName ? `${unit.name} · ${unit.id.slice(0, 8)}` : unit.name);
    option.value = unit.id;
    unitSelect.append(option);
  }
  unitSelect.value = selectedUnit;
  unitSelect.onchange = () => { selectedUnit = unitSelect.value; selectedProduct = null; catalogSignature = ''; renderCatalog(); };
  unitLabel.append(unitSelect);
  container.append(unitLabel);
  const fulfillment = (catalog.fulfillment || []).find(item => item.property_id === selectedUnit);
  if (fulfillment) {
    const profileUnit = selectedUnit;
    const details = node('details');
    details.open = fulfillmentDrafts.has(profileUnit);
    details.append(node('summary', fulfillment.verified ? 'Delivery profile · confirmed' : 'Delivery profile · needs confirmation'));
    details.append(node('p', 'Matched by Hospitable property ID, never by guest name. Imported addresses need confirmation, including apartment/unit number. Instructions stay in this authenticated workspace and are not emailed automatically.', 'help-text'));
    if (fulfillment.previous_address) details.append(node('p', 'Hospitable address details changed. Review the new imported address and confirm again.', 'warning'));
    const draft = fulfillmentDrafts.get(profileUnit) || {...fulfillment, address: {line1: '', line2: '', ...fulfillment.address, city: fulfillment.address.city || 'Charlotte', region: fulfillment.address.region || 'NC', postal_code: fulfillment.address.postal_code || '', country: fulfillment.address.country || 'US', timezone: fulfillment.address.timezone || 'America/New_York'}, confirmed: false};
    const form = node('form', '', 'fulfillment-form');
    if (draft.version !== fulfillment.version) form.append(node('p', 'This draft is outdated. Discard it and review the latest imported details.', 'warning'));
    for (const [key, title, maximum] of [['line1', 'Street address', 300], ['line2', 'Apartment / unit / delivery suite', 100], ['city', 'City', 100], ['region', 'State / region', 100], ['postal_code', 'Postal code', 32], ['country', 'Country code (US)', 2], ['timezone', 'Property time zone', 100]]) {
      const label = node('label', title);
      const input = document.createElement('input');
      input.name = key;
      input.value = draft.address[key];
      input.maxLength = maximum;
      input.required = key !== 'line2';
      label.append(input);
      form.append(label);
    }
    const instructionsLabel = node('label', 'Delivery instructions (leave blank if none)');
    const instructions = document.createElement('textarea');
    instructions.name = 'instructions';
    instructions.value = draft.instructions;
    instructions.maxLength = 2000;
    instructionsLabel.append(instructions);
    const quantityLabel = node('label', 'Default hand towels needed (individual towels, not packs)');
    const quantity = document.createElement('input');
    quantity.name = 'hand_towel_quantity';
    quantity.type = 'number';
    quantity.min = '1';
    quantity.max = '20';
    quantity.step = '1';
    quantity.required = true;
    quantity.value = draft.hand_towel_quantity;
    quantityLabel.append(quantity);
    const confirmation = node('label', '', 'checkbox-label');
    const confirmed = document.createElement('input');
    confirmed.type = 'checkbox';
    confirmed.required = true;
    confirmed.checked = draft.confirmed;
    confirmation.append(confirmed, node('span', 'I confirm the complete delivery address, unit number, instructions, and default towel quantity.'));
    const collect = () => ({property_id: profileUnit, version: draft.version, address: Object.fromEntries([...form.querySelectorAll('input[name]')].filter(input => input.name !== 'hand_towel_quantity').map(input => [input.name, input.value])), instructions: instructions.value, hand_towel_quantity: Number(quantity.value), confirmed: confirmed.checked});
    form.oninput = event => {
      if (event.target !== confirmed) confirmed.checked = false;
      fulfillmentDrafts.set(profileUnit, collect());
    };
    const save = node('button', 'Confirm delivery profile');
    const discard = node('button', 'Discard profile changes', 'secondary');
    discard.type = 'button';
    discard.onclick = () => { fulfillmentDrafts.delete(profileUnit); document.activeElement?.blur(); catalogSignature = ''; renderCatalog(); };
    const feedback = node('p', '', 'save-status');
    feedback.setAttribute('role', 'status');
    form.append(instructionsLabel, quantityLabel, confirmation, save, discard, feedback);
    form.onsubmit = async event => {
      event.preventDefault();
      const data = collect();
      for (const input of form.elements) input.disabled = true;
      try {
        await submitAction('/api/fulfillment', data);
        fulfillmentDrafts.delete(profileUnit);
        document.activeElement?.blur();
        catalogSignature = '';
        await load();
        notice.textContent = 'Delivery profile confirmed for this unit. Imported address changes will require confirmation again.';
      } catch (error) { feedback.textContent = error.message; }
      finally { for (const input of form.elements) input.disabled = false; }
    };
    details.append(form);
    container.append(details);
  }
  const products = catalog.products.filter(product => product.property_id === selectedUnit);
  const list = node('div', '', 'preferred-list');
  for (const product of products) {
    const row = node('div', '', 'preferred-row');
    const kind = catalog.item_types.find(item => item.id === product.item_type).label;
    row.append(node('strong', `${kind}${product.room ? ' · ' + product.room : ''}`), node('p', product.title, 'help-text'));
    if (product.item_type === 'tv_remote') row.append(node('p', `${product.tv_brand} · TV model ${product.tv_model}`, 'help-text'));
    const edit = node('button', 'Edit', 'secondary');
    edit.type = 'button';
    edit.onclick = () => { selectedProduct = product.id; catalogSignature = ''; renderCatalog(); };
    const remove = node('button', 'Remove preference', 'text-button');
    remove.type = 'button';
    remove.onclick = async () => {
      remove.disabled = true;
      try {
        await submitAction('/api/catalog/archive', {id: product.id, version: product.version});
        preferenceDrafts.delete(product.property_id + ':' + product.id);
        if (selectedProduct === product.id) selectedProduct = null;
        document.activeElement?.blur();
        await load();
      } catch (error) { notice.textContent = error.message; }
      finally { remove.disabled = false; }
    };
    row.append(edit, remove);
    list.append(row);
  }
  if (!products.length) list.append(node('p', 'No preferred products saved for this unit yet.', 'help-text'));
  container.append(list);
  const current = products.find(product => product.id === selectedProduct);
  const key = selectedUnit + ':' + (current?.id || 'new');
  const base = preferenceDrafts.get(key) || {id: current?.id || null, version: current?.version || 0, property_id: selectedUnit, item_type: current?.item_type || 'hand_towels', title: current?.title || '', amazon_product: current?.asin || '', room: current?.room || '', tv_brand: current?.tv_brand || '', tv_model: current?.tv_model || ''};
  const form = node('form', '', 'preference-form');
  form.append(node('h3', current ? 'Edit preferred product' : 'Add a preferred product'));
  if (current && base.version !== current.version) form.append(node('p', 'This draft is based on an older version. Discard it before editing the current preference.', 'warning'));
  const kindLabel = node('label', 'Item');
  const kindSelect = document.createElement('select');
  kindSelect.name = 'item_type';
  for (const item of catalog.item_types) {
    const option = node('option', item.label);
    option.value = item.id;
    kindSelect.append(option);
  }
  kindSelect.value = base.item_type;
  kindLabel.append(kindSelect);
  form.append(kindLabel);
  const fields = [['title', 'Product name', 300], ['amazon_product', 'Full Amazon.com product link or ASIN', 2000], ['room', 'TV location, e.g. living room', 100], ['tv_brand', 'TV brand, e.g. Samsung', 100], ['tv_model', 'Exact TV model from its label', 100]];
  for (const [name, title, maximum] of fields) {
    const label = node('label', title);
    label.dataset.field = name;
    const input = document.createElement('input');
    input.name = name;
    input.value = base[name];
    input.maxLength = maximum;
    label.append(input);
    form.append(label);
  }
  function televisionFields() {
    const remote = kindSelect.value === 'tv_remote';
    for (const name of ['room', 'tv_brand', 'tv_model']) {
      form.querySelector(`[data-field=${name}]`).hidden = !remote;
      form.elements[name].required = remote;
    }
    form.elements.title.required = true;
    form.elements.amazon_product.required = true;
  }
  televisionFields();
  kindSelect.addEventListener('change', televisionFields);
  form.oninput = () => preferenceDrafts.set(key, {...base, ...Object.fromEntries(new FormData(form))});
  const save = node('button', 'Save preferred item');
  const discard = node('button', 'Clear / start a new item', 'secondary');
  discard.type = 'button';
  discard.onclick = () => { preferenceDrafts.delete(key); selectedProduct = null; document.activeElement?.blur(); catalogSignature = ''; renderCatalog(); };
  const feedback = node('p', '', 'save-status');
  feedback.setAttribute('role', 'status');
  form.append(save, discard, feedback);
  form.onsubmit = async event => {
    event.preventDefault();
    const data = {...base, ...Object.fromEntries(new FormData(form))};
    for (const control of form.elements) control.disabled = true;
    try {
      await submitAction('/api/catalog', data);
      preferenceDrafts.delete(key);
      selectedProduct = null;
      document.activeElement?.blur();
      catalogSignature = '';
      await load();
      notice.textContent = 'Preferred product saved for this unit. Price, delivery, and TV compatibility still need checking.';
    } catch (error) { feedback.textContent = error.message; }
    finally { for (const control of form.elements) control.disabled = false; }
  };
  container.append(form);
}
window.addEventListener('beforeunload', event => { if (preferenceDrafts.size || fulfillmentDrafts.size) { event.preventDefault(); event.returnValue = ''; } });
