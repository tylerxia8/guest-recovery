const purchasePlanDrafts = new Map();
function purchasePlanPanel(issue, message) {
  const plan = issue.purchase_plans.find(item => item.message_id === message.id);
  const key = issue.id + ':' + message.id;
  const cached = purchasePlanDrafts.get(key);
  const choices = cached?.choices || structuredClone(plan.choices);
  const section = node('details', '', 'purchase-plan');
  section.open = Boolean(cached);
  section.append(node('summary', 'Purchase plan · manual checkout'));
  section.append(node('p', 'Quality and fastest arrival first. Price, availability, and delivery must be checked in Amazon.', 'help-text'));
  if (plan.stale || (cached && cached.fingerprint !== plan.source_fingerprint)) section.append(node('p', 'The conversation, property, or preferred items changed. Review and save the current plan again.', 'warning'));
  for (const reason of plan.blockers) section.append(node('p', reason, 'help-text'));
  const form = node('form');
  const counts = new Map();
  for (const item of plan.order_lines) section.append(node('p', `${item.packs || '?'} pack(s) · ${item.title}`, 'purchase-summary'));
  for (const item of plan.lines) {
    const row = node('div', '', 'preferred-row');
    row.append(node('strong', item.label), node('p', `Need ${item.quantity} ${item.unit} · ${item.basis}`, 'help-text'));
    if (item.asin) {
      const link = node('a', item.title + ' ↗', 'amazon-link');
      link.href = item.url;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      row.append(link);
      if (item.bundle) row.append(node('p', 'Included in the shampoo-and-conditioner set. Buy the combined pack quantity above, not a separate set for each item.', 'help-text'));
      else {
        const label = node('label', 'Individual units per Amazon pack');
        const input = document.createElement('input');
        input.type = 'number';
        input.min = '1';
        input.max = '1000';
        input.step = '1';
        input.value = choices.pack_sizes[item.item_type]?.asin === item.asin ? choices.pack_sizes[item.item_type].units : item.units_per_pack || '';
        const count = node('p', '', 'help-text');
        input.oninput = () => { count.textContent = input.value && input.validity.valid ? `Buy ${Math.ceil(item.quantity / Number(input.value))} pack(s)` : 'Pack quantity needs review'; };
        input.oninput();
        label.append(input);
        row.append(label, count);
        counts.set(item.item_type, {input, asin: item.asin});
      }
    } else {
      const search = node('a', item.title + ' ↗', 'amazon-link');
      search.href = item.url;
      search.target = '_blank';
      search.rel = 'noopener noreferrer';
      row.append(search, node('p', 'Small-pack preference · exact Amazon listing and pack size need selection in Settings.', 'help-text'));
    }
    row.append(node('p', item.check, 'help-text'));
    form.append(row);
  }
  const giftLabel = node('label', '', 'checkbox-label');
  const gift = document.createElement('input');
  gift.type = 'checkbox';
  gift.checked = choices.gift;
  giftLabel.append(gift, node('span', `Optional: ${plan.gift.title} · up to $3, only without delaying essentials`));
  form.append(giftLabel);
  const giftLink = node('a', 'Find ' + plan.gift.title + ' ↗', 'amazon-link');
  giftLink.href = plan.gift.url;
  giftLink.target = '_blank';
  giftLink.rel = 'noopener noreferrer';
  form.append(giftLink);
  form.append(node('p', 'Verify the full cost and delivery window in Amazon Business.', 'help-text'));
  const collect = () => ({gift: gift.checked, pack_sizes: Object.fromEntries([...counts].filter(([, value]) => value.input.value).map(([kind, value]) => [kind, {asin: value.asin, units: Number(value.input.value)}]))});
  form.oninput = () => { quote.disabled = true; purchasePlanDrafts.set(key, {choices: collect(), fingerprint: cached?.fingerprint || plan.source_fingerprint, version: cached?.version ?? plan.version}); };
  const save = node('button', 'Save plan');
  const feedback = node('p', plan.saved_at ? 'Plan saved · not ordered · no notice sent' : 'Not saved · not ordered', 'save-status');
  feedback.setAttribute('role', 'status');
  const reset = node('button', 'Reload current plan', 'secondary');
  reset.type = 'button';
  reset.onclick = () => { purchasePlanDrafts.delete(key); document.activeElement?.blur(); render(); };
  const actions = node('div', '', 'primary-actions');
  actions.append(save, reset);
  form.append(actions, feedback);
  form.onsubmit = async event => {
    event.preventDefault();
    for (const control of form.elements) control.disabled = true;
    try {
      await submitAction('/api/purchase-plans', {issue_id: issue.id, message_id: message.id, version: cached?.version ?? plan.version, source_fingerprint: cached?.fingerprint || plan.source_fingerprint, choices: collect()});
      purchasePlanDrafts.delete(key);
      document.activeElement?.blur();
      await load();
      notice.textContent = 'Purchase plan saved. No order or notification was sent.';
    } catch (error) { feedback.textContent = error.message; }
    finally { for (const control of form.elements) control.disabled = false; }
  };
  section.append(form);
  if (plan.destination) {
    const destination = node('details');
    destination.append(node('summary', 'Delivery details'));
    const address = plan.destination.address;
    destination.append(node('p', [address.line1, address.line2, `${address.city}, ${address.region} ${address.postal_code}`].filter(Boolean).join(' · ')));
    destination.append(node('p', plan.destination.instructions || 'No delivery instructions supplied.', 'help-text'));
    section.append(destination);
  }
  const previews = node('details');
  previews.append(node('summary', 'Draft messages · not sent'), node('strong', 'Manager notice'), node('p', plan.manager_notice), node('strong', 'Guest acknowledgment'), node('p', plan.guest_acknowledgment));
  section.append(previews);
  const quote = node('button', 'Record Amazon quote', 'secondary');
  quote.type = 'button';
  quote.disabled = Boolean(issue.manual_order_status || plan.blockers.length || plan.stale || !plan.saved_at || cached || issue.purchases.some(item => item.decision !== 'vetoed'));
  quote.onclick = () => {
    const address = plan.destination.address;
    purchaseDrafts.set(issue.id, {purchase_plan: {message_id: message.id, version: plan.version, source_fingerprint: plan.source_fingerprint}, summary: plan.summary + (plan.gift.requested ? '; Optional ' + plan.gift.title + ' only if verified at $3 or less and without delaying essentials' : ''), asins: [...new Set(plan.lines.map(item => item.asin))].join(', '), location: `${address.city}, ${address.region}`, timezone: address.timezone, fulfillment_version: plan.destination.version, arrival_date: '', delivery_window: '', address_confirmed: false, total: '', gift: '0', all_in_confirmed: false});
    document.activeElement?.blur();
    render();
    const form = document.querySelector('.purchase-form');
    if (form) { form.closest('.action-details').open = true; form.closest('details').open = true; form.scrollIntoView({behavior: 'smooth', block: 'start'}); }
  };
  section.append(quote);
  return section;
}
