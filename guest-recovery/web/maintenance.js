function maintenancePanel(issue, message) {
  const panel = node('section', '', 'solution-panel');
  panel.append(node('h3', 'Maintenance'));
  const settings = dataset.maintenance || {};
  const messageLink = issue.maintenance_links?.[message.id];
  const link = messageLink || issue.maintenance_link;
  const useMessage = Boolean(messageLink || Object.keys(issue.maintenance_links || {}).length);
  const fingerprint = useMessage ? issue.maintenance_fingerprints?.[message.id] : issue.maintenance_fingerprint;
  if (link) {
    panel.append(node('p', `Repair #${link.remote.ticket_number} · ${link.remote.priority.toLowerCase().replaceAll('_', ' ')} priority · ${link.remote.status.toLowerCase().replaceAll('_', ' ')}`), node('p', `Status checked ${date(link.checked_at)}`, 'help-text'));
    if (link.remote.source_fingerprint !== fingerprint && !issue.maintenance_synced_messages?.includes(message.id)) {
      panel.append(node('p', 'This guest message is available in the inbox. Check the repair for the details already shared.', 'help-text'));
    }
    if (link.remote.next_question) {
      panel.append(node('p', 'Maintenance intake question', 'help-text'), node('blockquote', link.remote.next_question.body));
    }
    if (settings.dashboard_url) {
      const open = node('a', 'Open repair', 'button');
      const repairUrl = new URL('/maintenance/review', settings.dashboard_url);
      repairUrl.searchParams.set('ticket', link.remote.ticket_id);
      if (['COMPLETED', 'CLOSED', 'CANCELED'].includes(link.remote.status)) repairUrl.searchParams.set('past', '1');
      open.href = repairUrl.href;
      open.target = '_blank';
      open.rel = 'noopener noreferrer';
      panel.append(open);
    }
  }
  if (!link && settings.configured && settings.mode === 'workspace' && issue.lane === 'needs_attention'
      && issue.property_ids?.some(id => settings.properties?.some(property => property.external_property_id === id))) {
    panel.append(node('p', 'Eligible maintenance reports start intake automatically.', 'help-text'));
  }
  if (!settings.configured) {
    panel.append(node('p', 'Maintenance is not connected.', 'help-text'));
    const settingsButton = node('button', 'Open Settings', 'secondary');
    settingsButton.type = 'button';
    settingsButton.onclick = () => {
      showSettings();
      const connection = document.querySelector('#maintenance-panel');
      connection.open = true;
      connection.scrollIntoView({behavior: 'smooth', block: 'start'});
      connection.querySelector('summary').focus();
    };
    panel.append(settingsButton);
    return panel;
  }
  panel.append(node('p', 'Maintenance asks intake questions in the guest conversation. Review contractor dispatch and any further guest replies separately.', 'help-text'));
  let summary;
  if (!link) {
    const label = node('label', 'Repair summary for Maintenance');
    summary = node('textarea');
    summary.maxLength = 5000;
    label.append(summary);
    panel.append(label, node('p', 'Describe the fault without guest names, contact details, or access codes.', 'help-text'));
  }
  const button = node('button', link ? 'Refresh repair status' : 'Open in maintenance');
  button.type = 'button';
  const feedback = node('p', '', 'help-text');
  feedback.setAttribute('role', 'status');
  button.onclick = async () => {
    if (summary && !summary.value.trim()) {
      feedback.textContent = 'Enter a repair summary before opening Maintenance.';
      summary.focus();
      return;
    }
    button.disabled = true;
    try {
      await submitAction(link ? '/api/maintenance/refresh' : '/api/maintenance/import', {issue_id: issue.id, ...(useMessage ? {message_id: message.id} : {}), source_fingerprint: fingerprint, ...(summary ? {description: summary.value} : {})});
      await load();
    } catch (error) {
      feedback.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  };
  panel.append(button, feedback);
  return panel;
}

const maintenanceForm = document.querySelector('#maintenance-settings');
managerSession.then(session => {
  document.querySelector('#maintenance-panel').hidden = false;
  maintenanceForm.hidden = session.role !== 'admin';
  document.querySelector('#maintenance-admin-note').hidden = session.role === 'admin';
});
document.querySelector('#maintenance-panel').addEventListener('toggle', event => {
  if (!event.target.open) return;
  const settings = dataset.maintenance || {};
  document.querySelector('#maintenance-connection-status').textContent = `${settings.configured ? 'Connected' : 'Not connected'}${settings.expires_at ? ' · Token expires ' + date(settings.expires_at) : ''}${settings.mode === 'demo' ? ' · Demo workspace: real guest imports are blocked.' : ''}`;
  const workspace = document.querySelector('#maintenance-setup-link');
  workspace.hidden = !settings.dashboard_url;
  if (settings.dashboard_url) workspace.href = settings.dashboard_url + '/workspace';
  maintenanceForm.elements.url.value = dataset.maintenance?.url || '';
  maintenanceForm.elements.dashboard_url.value = dataset.maintenance?.dashboard_url || '';
  maintenanceForm.elements.token.placeholder = dataset.maintenance?.configured ? 'Configured — leave blank to keep' : 'Create a Guest Messages token in maintenance';
});
maintenanceForm.onsubmit = async event => {
  event.preventDefault();
  const button = maintenanceForm.querySelector('button');
  button.disabled = true;
  try {
    await submitAction('/api/settings/maintenance', Object.fromEntries(new FormData(maintenanceForm)));
    maintenanceForm.elements.token.value = '';
    document.querySelector('#maintenance-feedback').textContent = 'Maintenance connected. Imports require mapped properties and manager selection.';
    await load();
  } catch (error) {
    document.querySelector('#maintenance-feedback').textContent = error.message;
  } finally {
    button.disabled = false;
  }
};
