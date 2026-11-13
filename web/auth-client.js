const managerSession = (async () => {
  let response;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      response = await fetch('/api/auth/session', {credentials: 'same-origin', signal: AbortSignal.timeout(15000)});
      if ((response.status === 429 || response.status >= 500) && attempt < 2) { await new Promise(resolve => setTimeout(resolve, 1000 * 2 ** attempt)); continue; }
      break;
    } catch (error) {
      if (attempt === 2) throw error;
      await new Promise(resolve => setTimeout(resolve, 1000 * 2 ** attempt));
    }
  }
  if (!response.ok) throw new Error('Could not check your session. Refresh the page.');
  const session = await response.json();
  if (!session.authenticated) { window.location.replace('/login'); throw new Error('Sign in to continue.'); }
  document.querySelector('#manager-name').textContent = session.username;
  if (session.role === 'admin') document.querySelector('#email-panel').hidden = false;
  return session;
})();
async function apiFetch(url, options = {}) {
  const session = await managerSession;
  const headers = new Headers(options.headers);
  if ((options.method || 'GET').toUpperCase() !== 'GET') headers.set('X-CSRF-Token', session.csrf);
  const read = (options.method || 'GET').toUpperCase() === 'GET';
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const response = await fetch(url, {...options, headers, credentials: 'same-origin', signal: AbortSignal.timeout(15000)});
      if (response.status === 401) { window.location.replace('/login'); throw new Error('Your session expired. Sign in again.'); }
      if (read && !attempt && (response.status === 429 || response.status >= 500)) { await new Promise(resolve => setTimeout(resolve, 1000)); continue; }
      return response;
    } catch (error) {
      if (!read || attempt || !['TypeError', 'TimeoutError', 'AbortError'].includes(error.name)) throw error;
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  }
}
document.querySelector('#logout').onclick = async () => {
  try {
    await submitAction('/api/auth/logout', {});
    window.location.replace('/login');
  } catch (error) { document.querySelector('#notice').textContent = error.message; }
};
const emailForm = document.querySelector('#email-settings');
function showEmailProvider() {
  const gmail = emailForm.elements.provider.value === 'gmail';
  document.querySelector('#gmail-settings').hidden = !gmail;
  document.querySelector('#resend-settings').hidden = gmail;
}
emailForm.elements.provider.onchange = showEmailProvider;
document.querySelector('#email-panel').addEventListener('toggle', async event => {
  if (!event.target.open || emailForm.dataset.loaded) return;
  try {
    const response = await apiFetch('/api/settings/email', {signal: AbortSignal.timeout(15000)});
    if (!response.ok) throw new Error('Could not load email settings.');
    const settings = await response.json();
    emailForm.elements.provider.value = settings.provider;
    showEmailProvider();
    emailForm.elements.sender.value = settings.sender;
    emailForm.elements.recipient.value = settings.recipient;
    emailForm.elements.api_key.placeholder = settings.api_key_configured ? 'Already configured — leave blank to keep it' : 'Paste your Resend API key';
    emailForm.elements.app_password.placeholder = settings.app_password_configured ? 'Already configured — leave blank to keep it' : '16-character Google app password';
    document.querySelector('#email-hosting').textContent = settings.remote_ready ? `Manager email links use ${settings.public_url}.` : 'Manager email links currently open this computer. Set APP_PUBLIC_URL when deploying for remote access.';
    emailForm.dataset.loaded = 'true';
  } catch (error) { document.querySelector('#email-feedback').textContent = error.message; }
});
emailForm.onsubmit = async event => {
  event.preventDefault();
  const submit = emailForm.querySelector('button');
  submit.disabled = true;
  try {
    const result = await submitAction('/api/settings/email', Object.fromEntries(new FormData(emailForm)));
    emailForm.elements.api_key.value = '';
    emailForm.elements.app_password.value = '';
    document.querySelector('#email-feedback').textContent = result.configured ? 'Email settings saved. Queued manager notices and cleaner inquiries can now be sent if their deadlines are still valid.' : 'Addresses saved. Add the selected email service’s credential before notices can be sent.';
    delete emailForm.dataset.loaded;
    await load();
  } catch (error) { document.querySelector('#email-feedback').textContent = error.message; }
  finally { submit.disabled = false; }
};
