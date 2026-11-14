let selectedUnit = null;
let selectedProduct = null;
let catalogSignature = '';
const preferenceDrafts = new Map();
function renderCatalog() {
  if (!dataset.catalog) return;
  const container = document.querySelector('#catalog-editor');
  const catalog = dataset.catalog;
  const signature = JSON.stringify(catalog);
  if (container.querySelector('form')?.contains(document.activeElement)) return;
  if (signature === catalogSignature && container.firstChild) return;
  catalogSignature = signature;
  container.replaceChildren();
  const defaults = node('details');
  defaults.append(node('summary', 'Shared preferred items · Charlotte'));
  defaults.append(node('p', 'Choose suitable quality and earliest confirmed arrival, then compare full cost. Unit-specific products take priority. Sizes, prices, and delivery still require confirmation on Amazon.', 'help-text'));
  for (const product of catalog.defaults || []) {
    const row = node('div', '', 'preferred-row');
    const link = node('a', product.title + ' ↗', 'amazon-link');
    link.href = 'https://www.amazon.com/s?' + new URLSearchParams({k: product.title});
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    row.append(link, node('p', product.check, 'help-text'));
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
window.addEventListener('beforeunload', event => { if (preferenceDrafts.size) { event.preventDefault(); event.returnValue = ''; } });
