function managerSolutionCard(issue, message, controls) {
  const panel = node('section', '', 'manager-purchase');
  panel.append(node('h2', 'Solution'));
  if (issue.category === 'Safety') {
    panel.append(node('p', 'Urgent safety response', 'eyebrow'));
    const reply = issue.guest_replies.find(item => item.message_id === message.id);
    const alert = issue.safety_alerts?.find(item => item.message_id === message.id);
    if (reply?.suggested) panel.append(node('blockquote', reply.suggested));
    if (issue.lane === 'practice') panel.append(node('p', 'Historical practice case. No automatic alerts or messages are sent.', 'help-text'));
    else {
      const emailStates = {pending: 'Urgent manager email queued', sending: 'Sending urgent manager email', sent: 'Gmail accepted the manager alert', unknown: 'Manager email outcome unknown — contact the manager directly'};
      panel.append(node('p', alert ? emailStates[alert.state] : 'No automatic manager alert recorded — contact the manager immediately.', alert?.state === 'sent' ? 'help-text' : 'warning'));
      if (alert?.error) panel.append(node('p', alert.error, 'warning'));
      const guestStates = {queued: 'Emergency guest message queued', attempting: 'Sending emergency instructions', accepted: 'Hospitable accepted the emergency message', blocked: 'Emergency message blocked — contact the guest immediately', failed: 'Emergency message failed — contact the guest immediately', unknown: 'Guest message outcome unknown — check Hospitable and contact the guest'};
      panel.append(node('p', reply?.send ? guestStates[reply.send.state] : 'No emergency guest message recorded — contact the guest immediately.', reply?.send?.state === 'accepted' ? 'help-text' : 'warning'));
    }
    panel.append(node('p', 'Do not assume that emergency services have been called.', 'warning'));
    return panel;
  }
  if (issue.category === 'Maintenance') {
    panel.append(maintenancePanel(issue, message));
    return panel;
  }
  const request = issue.purchases.find(item => item.decision !== 'vetoed');
  const plan = issue.purchase_plans?.find(item => item.message_id === message.id);
  const subtitle = request || plan?.lines.length ? 'Amazon purchase' : {Maintenance: 'Repair', Cleaning: 'Cleaning follow-up', Instructions: 'Property instructions', Safety: 'Urgent manager review', Supplies: 'Supply replacement'}[issue.category];
  if (subtitle) panel.append(node('p', subtitle, 'eyebrow'));
  if (request) {
    panel.append(node('p', request.summary, 'purchase-summary'), node('strong', `$${(request.total_cents / 100).toFixed(2)} total`, 'purchase-total'));
    if (request.offer) panel.append(node('p', `Arrival: ${request.offer.arrival_date} · ${request.offer.delivery_window}`, 'help-text'));
    panel.append(node('p', request.status, 'purchase-status'));
    if (request.eligible_at) {
      const clock = node('p', '', 'countdown');
      clock.dataset.deadline = request.eligible_at;
      panel.append(clock);
    } else panel.append(node('p', request.approval_required ? (request.decision === 'approved' ? 'Manager approval recorded.' : 'Your approval is required before any purchase.') : '10-minute window starts after the manager notice is sent successfully.', 'help-text'));
    const actions = node('div', '', 'primary-actions');
    for (const [action, label] of [['approve', request.decision === 'approved' ? 'Confirmed' : 'Confirm'], ['veto', 'Deny']]) {
      const button = node('button', label, action === 'veto' ? 'secondary' : '');
      button.type = 'button';
      button.disabled = action === 'approve' && (request.decision === 'approved' || request.offer?.stale);
      button.onclick = async () => {
        for (const control of actions.querySelectorAll('button')) control.disabled = true;
        try { await submitAction(`/api/purchases/${request.id}/${action}`, {}); document.activeElement?.blur(); await load(); }
        catch (error) { notice.textContent = error.message; }
        finally { for (const control of actions.querySelectorAll('button')) control.disabled = control.textContent === 'Confirmed' || (control.textContent === 'Confirm' && request.offer?.stale); }
      };
      actions.append(button);
    }
    panel.append(actions);
  } else {
    if (plan?.lines.length) {
      for (const item of plan.order_lines) panel.append(node('p', `${item.packs || '?'} pack(s) · ${item.title}`, 'purchase-summary'));
      if (plan.gift.requested) panel.append(node('p', 'Optional chocolates · up to $3', 'help-text'));
      panel.append(node('p', 'Price and delivery need an Amazon quote.', 'purchase-status'));
      const setup = node('button', 'Complete purchase details', 'secondary');
      setup.type = 'button';
      setup.onclick = () => { controls.closest('.manager-advanced').open = true; controls.open = true; const plan = controls.querySelector('.purchase-plan'); plan.open = true; plan.scrollIntoView({behavior: 'smooth', block: 'start'}); };
      panel.append(setup);
    } else panel.append(node('p', issue.recommendation || 'Review the conversation to choose the next step.', 'help-text'));
    if (issue.purchases[0]?.decision === 'vetoed') panel.append(node('p', 'Previous proposal denied.', 'help-text'));
  }
  if (request || plan?.lines.length) panel.append(node('p', 'Checkout is not connected. No automatic purchase will occur.', 'checkout-note'));
  return panel;
}
