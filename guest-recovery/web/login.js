let setup = false;
const form = document.querySelector('#login-form');
const submit = document.querySelector('#sign-in');
const feedback = document.querySelector('#login-feedback');
const requested = new URLSearchParams(window.location.search).get('next');
const destination = /^\/\?(?:purchase=[a-f0-9-]{36}|supply=[A-Za-z0-9_.~%+-]{1,512})$/.test(requested || '') ? requested : '/';
async function initializeLogin() {
  try {
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
    if (!response.ok) throw new Error('Could not connect. Refresh the page.');
    const session = await response.json();
    if (session.authenticated) { window.location.replace(destination); return; }
    setup = session.setup_allowed;
    if (setup) {
      document.querySelector('#login-title').textContent = 'Create your manager account';
      document.querySelector('#login-description').textContent = 'Set up the first administrator on this computer. Choose a password of at least 12 characters. No shared access token is needed.';
      document.querySelector('#confirm-label').hidden = false;
      form.elements.confirm_password.required = true;
      form.elements.password.autocomplete = 'new-password';
      submit.textContent = 'Create account';
    }
    submit.disabled = false;
  } catch (error) { feedback.textContent = error.message; }
}
form.onsubmit = async event => {
  event.preventDefault();
  if (setup && form.elements.password.value !== form.elements.confirm_password.value) { feedback.textContent = 'Passwords do not match.'; return; }
  submit.disabled = true;
  feedback.textContent = '';
  try {
    const response = await fetch(setup ? '/api/auth/setup' : '/api/auth/login', {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID()}, body: JSON.stringify({username: form.elements.username.value, password: form.elements.password.value}), signal: AbortSignal.timeout(15000)});
    const result = await response.json();
    form.elements.password.value = '';
    form.elements.confirm_password.value = '';
    if (!response.ok) throw new Error(result.error);
    window.location.replace(destination);
  } catch (error) { feedback.textContent = error.message; }
  finally { submit.disabled = false; }
};
initializeLogin();
