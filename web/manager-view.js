function managerSolutionCard(issue, message, controls) {
  const panel = node('section', '', 'manager-purchase');
  panel.append(node('h2', 'Solution'));
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

function issueWorkflow(issue, message) {
  const section = node('section', '', 'purchase-panel');
  const workflow = issue.workflow;
  const actions = {
    Timing: [['approved', 'Record approval'], ['declined', 'Record decline']],
    Maintenance: [['resolved', 'Mark resolved']],
    Cleaning: [['handoff_recorded', 'Record cleaner handoff'], ['resolved', 'Mark resolved']],
    Safety: [['acknowledged', 'Acknowledge safety report'], ['resolved', 'Mark resolved']],
    Supplies: [['resolved', 'Mark resolved']],
    Instructions: [['resolved', 'Mark resolved']],
    Other: [['resolved', 'Mark resolved']],
  }[issue.category];
  const labels = {approved: 'Approved', declined: 'Declined', handoff_recorded: 'Handoff recorded', acknowledged: 'Acknowledged', resolved: 'Resolved'};
  section.append(node('h3', 'Manager follow-up'));
  if (issue.lane === 'practice') section.append(node('p', 'Practice record', 'help-text'));
  if (issue.category === 'Timing') {
    section.append(node('p', `Scheduled arrival ${date(issue.check_in)} · departure ${date(issue.check_out)}. Confirm cleaner availability separately. This decision does not change the reservation.`, 'help-text'));
  }
  if (issue.category === 'Maintenance') section.append(maintenanceHandoff(issue));
  if (['Cleaning', 'Timing'].includes(issue.category)) {
    const label = issue.category === 'Cleaning' ? 'Cleaning follow-up' : 'Turnover check';
    const copy = node('button', `Copy ${label.toLowerCase()}`, 'secondary');
    copy.type = 'button';
    copy.onclick = async () => {
      const context = [label, `Property: ${issue.property}`, `Reservation: ${issue.reservation_id}`,
        `Scheduled arrival: ${issue.check_in}`, `Scheduled departure: ${issue.check_out}`, message.body].join('\n');
      try {
        await navigator.clipboard.writeText(context);
        notice.textContent = 'Copied. Paste into the appropriate workspace.';
      } catch {
        notice.textContent = 'Clipboard unavailable. Copy the message from the conversation.';
      }
    };
    section.append(copy);
  }
  if (workflow.decision) {
    section.append(node('p', workflow.stale ? 'New information needs review. Previous decision: ' + labels[workflow.decision] : labels[workflow.decision], 'purchase-status'));
    section.append(node('p', workflow.note, 'help-text'));
  }
  const form = node('form');
  const label = node('label', issue.category === 'Timing' ? 'Requested time, timezone, and turnover check' : 'Follow-up note');
  const note = node('textarea');
  note.required = true;
  note.maxLength = 1000;
  note.rows = 2;
  label.append(note);
  form.append(label);
  for (const [decision, text] of actions) {
    if (issue.category === 'Safety' && decision === 'resolved' && (workflow.stale || workflow.decision !== 'acknowledged')) continue;
    const button = node('button', text, 'secondary');
    button.value = decision;
    form.append(button);
  }
  form.onsubmit = async event => {
    event.preventDefault();
    if (!event.submitter) return;
    const buttons = [...form.querySelectorAll('button')];
    buttons.forEach(button => { button.disabled = true; });
    try {
      await submitAction('/api/issue-workflow', {issue_id: issue.id, version: workflow.version,
        fingerprint: workflow.fingerprint, decision: event.submitter.value, note: note.value});
      document.activeElement?.blur();
      await load();
    } catch (error) {
      notice.textContent = error.message;
    } finally {
      buttons.forEach(button => { button.disabled = false; });
    }
  };
  section.append(form);
  return section;
}

function maintenanceHandoff(issue) {
  const panel = node('div');
  const bridge = issue.maintenance;
  if (bridge.ticket) {
    const {ticket_number, status, priority, updated_at} = bridge.ticket;
    panel.append(node('p', `Repair #${ticket_number} · ${status.replaceAll('_', ' ').toLowerCase()} · ${priority.toLowerCase()}`, 'purchase-status'));
    panel.append(node('p', `Last repair update ${date(updated_at)}`, 'help-text'));
  }
  if (!bridge.available || (!bridge.ticket && !bridge.eligible)) {
    panel.append(node('p', bridge.available ? 'A current stay with one matched property is required to send a repair.' : 'Maintenance workspace connection is not configured.', 'help-text'));
    return panel;
  }
  const form = node('form');
  let summary;
  if (!bridge.ticket) {
    const label = node('label', 'Repair summary for Maintenance');
    summary = node('textarea');
    summary.required = true;
    summary.maxLength = 5000;
    label.append(summary);
    form.append(label, node('p', 'Describe the fault and leave out guest names, contact details, and access codes.', 'help-text'));
  }
  const button = node('button', bridge.ticket ? 'Refresh repair status' : 'Send to Maintenance', 'secondary');
  form.append(button);
  form.onsubmit = async event => {
    event.preventDefault();
    button.disabled = true;
    try {
      await submitAction(bridge.ticket ? '/api/maintenance/status' : '/api/maintenance',
        {issue_id: issue.id, ...(summary ? {description: summary.value, fingerprint: bridge.fingerprint} : {})});
      document.activeElement?.blur();
      await load();
    } catch (error) {
      notice.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  };
  panel.append(form);
  return panel;
}
