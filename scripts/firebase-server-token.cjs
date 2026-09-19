// Called only by the opt-in local Python credential adapter. Never run to a terminal.
// The access token is consumed by Python in memory; no credentials are written to disk.
const fs = require('node:fs');
const path = require('node:path');
(async () => {
  const lib = path.dirname(path.dirname(fs.realpathSync(process.argv[2])));
  const auth = require(path.join(lib, 'auth.js'));
  const account = auth.getGlobalDefaultAccount();
  if (!account) throw new Error('No CLI login');
  const options = { ...account, project: 'blyatbike' };
  await require(path.join(lib, 'requireAuth.js')).requireAuth(options);
  const token = await auth.getAccessToken(account.tokens.refresh_token, options.authScopes);
  process.stdout.write(JSON.stringify({ access_token: token.access_token, expires_at: token.expires_at }));
})().catch(() => { process.exitCode = 1; });
