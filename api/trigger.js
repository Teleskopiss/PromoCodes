export const config = { runtime: 'edge' };

const OWNER = 'Teleskopiss';
const REPO  = 'PromoCodes';
const WF    = 'scrape.yml';

export default async function handler(req) {
  // Allow browser requests from any origin
  const cors = {
    'Access-Control-Allow-Origin':  '*',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
  };

  if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
  if (req.method !== 'POST')   return new Response('Method not allowed', { status: 405, headers: cors });

  const pat = process.env.DISPATCH_PAT;
  if (!pat) return new Response('DISPATCH_PAT not set', { status: 500, headers: cors });

  const gh = await fetch(
    `https://api.github.com/repos/${OWNER}/${REPO}/actions/workflows/${WF}/dispatches`,
    {
      method: 'POST',
      headers: {
        'Authorization':        `Bearer ${pat}`,
        'Accept':               'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'Content-Type':         'application/json',
      },
      body: JSON.stringify({ ref: 'main' }),
    }
  );

  return new Response(gh.status === 204 ? 'ok' : 'error', {
    status: gh.status === 204 ? 204 : 502,
    headers: cors,
  });
}
